"""建索引 + 混合检索。

建库：卡片 + 长文切块 →（向量）ChromaDB + （关键词）BM25；manifest 记录版本与 hash。
检索：元数据过滤 → 向量召回 top-K ∥ BM25 召回 top-K → RRF 融合 → 重排 → 阈值过滤 → 父段展开。

运行：python -m app.kb.index        （重建索引）
"""
from __future__ import annotations

import csv
import hashlib
import json
import pickle
from functools import lru_cache

import numpy as np

from app import config
from app.kb.bm25 import BM25
from app.kb.chunk import Chunk, load_articles, load_cards
from app.kb.text import get_embedder, tokenize

INDEX_DIR = config.RUNTIME / "kb"
COLLECTION = "species_kb"


def load_facts() -> dict[str, dict]:
    with open(config.DATA / "species_facts.csv", encoding="utf-8") as f:
        return {r["species_id"]: r for r in csv.DictReader(f)}


def poisoned_chunks() -> list[Chunk]:
    """评测用：把投毒文档混进索引，测试「护栏 2 资料过滤」。"""
    out = []
    sci2id = {f["sci"]: k for k, f in load_facts().items()}
    for line in (config.EVAL / "fixtures" / "poisoned_docs.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        sid = sci2id.get(r["spec_code_of"], "")
        out.append(Chunk(f"poison_{r['poison_id']}", r["text"],
                         {"spec_id": sid, "sci": r["spec_code_of"], "family": "", "doc_type": r["doc_type"],
                          "core": 1, "lang": "en", "source": "untrusted", "kb_version": "syn-v1", "kind": "card"}))
    return out


def build(include_poison: bool = True, chunk_size: int = 512, overlap: int = 64, header: bool = True) -> dict:
    import chromadb
    facts = load_facts()
    chunks = load_cards(config.DATA / "cards.jsonl")
    chunks += load_articles(config.DATA / "articles", facts, size=chunk_size, overlap=overlap, header=header)
    if include_poison:
        chunks += poisoned_chunks()
    emb = get_embedder()
    vecs = emb.embed([c.text for c in chunks])

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(INDEX_DIR / "chroma"))
    try:
        client.delete_collection(COLLECTION)
    except Exception:  # noqa: BLE001 - 首次建库时不存在
        pass
    col = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    clean = lambda m: {k: v for k, v in m.items() if k != "parent_text"}  # noqa: E731
    for i in range(0, len(chunks), 1000):
        b = chunks[i:i + 1000]
        col.add(ids=[c.id for c in b], embeddings=vecs[i:i + 1000].tolist(),
                documents=[c.text for c in b], metadatas=[clean(c.meta) for c in b])
    with open(INDEX_DIR / "chunks.pkl", "wb") as f:
        pickle.dump({"chunks": chunks, "vecs": vecs}, f)
    manifest = {
        "kb_version": "syn-v1", "embedder": emb.name, "n_chunks": len(chunks),
        "n_cards": sum(c.meta["kind"] == "card" for c in chunks),
        "n_article_chunks": sum(c.meta["kind"] == "article" for c in chunks),
        "chunk_size": chunk_size, "overlap": overlap, "header": header, "include_poison": include_poison,
        "cards_sha256": hashlib.sha256((config.DATA / "cards.jsonl").read_bytes()).hexdigest(),
    }
    (INDEX_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    KB.cache_clear()
    return manifest


class KnowledgeBase:
    def __init__(self):
        import chromadb
        if not (INDEX_DIR / "chunks.pkl").exists():
            build()
        with open(INDEX_DIR / "chunks.pkl", "rb") as f:
            d = pickle.load(f)
        self.chunks: list[Chunk] = d["chunks"]
        self.vecs: np.ndarray = d["vecs"]
        self.by_id = {c.id: i for i, c in enumerate(self.chunks)}
        self.bm25 = BM25([c.text for c in self.chunks])
        self.emb = get_embedder()
        self.col = chromadb.PersistentClient(path=str(INDEX_DIR / "chroma")).get_collection(COLLECTION)

    # ---- 元数据过滤
    @staticmethod
    def _where(filters: dict) -> dict | None:
        conds = [{k: v} for k, v in filters.items() if v not in (None, "")]
        return None if not conds else conds[0] if len(conds) == 1 else {"$and": conds}

    def _allowed(self, filters: dict) -> set[int]:
        f = {k: v for k, v in filters.items() if v not in (None, "")}
        return {i for i, c in enumerate(self.chunks) if all(c.meta.get(k) == v for k, v in f.items())}

    # ---- 重排（离线版：向量相似度 + 查询词覆盖率；可替换为 Qwen3-Reranker 交叉编码器）
    def _rerank_score(self, query: str, i: int, qv: np.ndarray) -> float:
        q = set(tokenize(query))
        d = set(tokenize(self.chunks[i].text))
        coverage = len(q & d) / max(1, len(q))
        return round(0.5 * float(self.vecs[i] @ qv) + 0.5 * coverage, 4)

    def search(self, query: str, filters: dict | None = None, k: int | None = None, n: int | None = None,
               mode: str = "hybrid", rerank: bool = True, min_relevance: float | None = None) -> list[dict]:
        """mode: hybrid / vector / bm25（用于消融）。"""
        k, n = k or config.RETRIEVE_K, n or config.RERANK_N
        filters = filters or {}
        thr = config.MIN_RELEVANCE if min_relevance is None else min_relevance
        qv = self.emb.embed([query])[0]
        ranks: dict[int, float] = {}
        if mode in ("hybrid", "vector"):
            res = self.col.query(query_embeddings=[qv.tolist()], n_results=k, where=self._where(filters))
            for r, cid in enumerate(res["ids"][0]):
                ranks[self.by_id[cid]] = ranks.get(self.by_id[cid], 0) + 1 / (config.RRF_K + r + 1)
        if mode in ("hybrid", "bm25"):
            allowed = self._allowed(filters) if filters else None
            for r, (i, _) in enumerate(self.bm25.scores(query, allowed)[:k]):
                ranks[i] = ranks.get(i, 0) + 1 / (config.RRF_K + r + 1)       # RRF：只看名次，不看原始分数
        cand = sorted(ranks, key=lambda i: -ranks[i])[: k]
        if rerank:
            scored = sorted(((i, self._rerank_score(query, i, qv)) for i in cand), key=lambda x: -x[1])
        else:
            scored = [(i, round(ranks[i] * 30, 4)) for i in cand]
        out, seen_parent = [], set()
        for i, s in scored:
            if s < thr:
                continue
            c = self.chunks[i]
            pid = c.meta.get("parent_id")
            if pid and pid in seen_parent:                                  # 同一父段只返回一次
                continue
            seen_parent.add(pid)
            out.append({"id": c.id, "score": s, "text": c.meta.get("parent_text") or c.text,
                        "sci": c.meta.get("sci"), "doc_type": c.meta.get("doc_type"), "source": c.meta.get("source")})
            if len(out) >= n:
                break
        return out


@lru_cache(maxsize=1)
def KB() -> KnowledgeBase:  # noqa: N802 - 单例
    return KnowledgeBase()


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
