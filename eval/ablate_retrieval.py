"""检索消融：同一批问题，比较不同检索配置的 Recall@5 和 MRR。

问题：知识题 K021–K040（每题都知道正确卡片的 id）。为了看清检索本身的能力，这里**不加鱼种过滤**。
配置：纯向量 / 纯 BM25 / 混合（RRF） / 混合 + 重排；切块大小与块头的消融见 --chunks。

用法：python -m eval.ablate_retrieval            结果写入 eval/results/ablation_retrieval.md
注意：离线向量是哈希向量（没有语义能力），所以「纯向量」偏弱是预期内的；换 EMBEDDER=qwen 后结论可能变化。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.kb.index import KB

EVAL = Path(__file__).resolve().parent
FIELD_DOC = {"Length": "overview", "Weight": "overview", "Family": "overview", "DemersPelag": "habitat", "habitat": "habitat"}


def questions():
    import csv
    sid = {r["sci"]: r["species_id"] for r in csv.DictReader(open(EVAL.parent / "data/synthetic/species_facts.csv", encoding="utf-8"))}
    out = []
    for line in open(EVAL / "datasets" / "knowledge_fishbase.jsonl", encoding="utf-8"):
        r = json.loads(line)
        f = r["expected"]["answer"]["facts"]
        if "field" in f:
            out.append((r["turns"][0]["user"], f"card_{sid[f['species']]}_{FIELD_DOC[f['field']]}"))
    return out


def evaluate(mode: str, rerank: bool, k: int = 5):
    hits, rr = 0, 0.0
    qs = questions()
    for q, gold in qs:
        res = KB().search(q, {}, k=20, n=k, mode=mode, rerank=rerank, min_relevance=0.0)
        ids = [h["id"] for h in res]
        if gold in ids:
            hits += 1
            rr += 1 / (ids.index(gold) + 1)
    return {"recall@5": round(hits / len(qs), 3), "mrr": round(rr / len(qs), 3), "n": len(qs)}


def main():
    rows = [("纯向量", "vector", False), ("纯 BM25", "bm25", False), ("混合（RRF）", "hybrid", False), ("混合 + 重排", "hybrid", True)]
    lines = ["# 检索消融（合成数据，无鱼种过滤）", "", "| 配置 | Recall@5 | MRR |", "| --- | --- | --- |"]
    for name, mode, rr in rows:
        m = evaluate(mode, rr)
        lines.append(f"| {name} | {m['recall@5']:.1%} | {m['mrr']:.3f} |")
        print(name, m)
    lines += ["", f"问题数：{m['n']}。离线向量为哈希向量（无语义能力），结论仅说明方法可运行；换真实嵌入模型后需重跑。"]
    out = EVAL / "results" / "ablation_retrieval.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
