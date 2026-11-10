"""切块：三类文本三种切法（见计划 5.3）。

1. 卡片（结构化记录渲染而来）：不切，一张卡片就是一块。
2. 长文（Markdown）：先按 ## 标题切成章节，章节过长再按 token 递归切（带 overlap），
   过短的章节并入前一节；每块加「上下文块头」；并记录父段 id（父子块）。
3. 规则：不进向量库（由规则工具精确查询）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.kb.text import count_tokens

SEPARATORS = ["\n\n", "\n", "。", ". ", "；", ";", "，", " "]


@dataclass
class Chunk:
    id: str
    text: str                       # 用于向量化的文本（含块头）
    meta: dict = field(default_factory=dict)


def load_cards(path: Path) -> list[Chunk]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out.append(Chunk(r["id"], r["text"], {**r["meta"], "kind": "card"}))
    return out


def _recursive_split(text: str, size: int, overlap: int, seps=SEPARATORS) -> list[str]:
    """按分隔符优先级递归切分，保证每段 ≤ size token，相邻段重叠约 overlap token。"""
    if count_tokens(text) <= size:
        return [text]
    sep = next((s for s in seps if s in text), None)
    if sep is None:                                   # 实在没有分隔符：按字符硬切
        step = max(1, size - overlap)
        return [text[i:i + size] for i in range(0, len(text), step)]
    parts, out, cur = text.split(sep), [], ""
    for p in parts:
        cand = (cur + sep + p) if cur else p
        if count_tokens(cand) <= size:
            cur = cand
            continue
        if cur:
            out.append(cur)
        cur = p if count_tokens(p) <= size else ""
        if not cur:
            out.extend(_recursive_split(p, size, overlap, seps[seps.index(sep) + 1:] or [" "]))
    if cur:
        out.append(cur)
    if overlap and len(out) > 1:                      # 加 overlap：把上一段末尾带到下一段开头
        tail = lambda s: s[-overlap:]                 # noqa: E731  按字符近似
        out = [out[0]] + [tail(out[i - 1]) + out[i] for i in range(1, len(out))]
    return out


def split_markdown(md: str, doc_id: str, meta: dict, size: int = 512, overlap: int = 64,
                   min_section: int = 80, header: bool = True, child_size: int | None = None) -> list[Chunk]:
    title = md.splitlines()[0].lstrip("# ").strip()
    sections = re.split(r"\n(?=## )", md)
    named = []
    for sec in sections[1:]:
        name, _, body = sec.partition("\n")
        named.append([name.lstrip("# ").strip(), body.strip()])
    merged = []                                       # 过短章节并入前一节
    for name, body in named:
        if merged and count_tokens(body) < min_section:
            merged[-1][1] += "\n\n" + body
        else:
            merged.append([name, body])
    out = []
    for si, (name, body) in enumerate(merged):
        parent_id = f"{doc_id}#s{si}"
        pieces = _recursive_split(body, child_size or size, overlap)
        for pi, piece in enumerate(pieces):
            head = f"[文档: {title} | 章节: {name}]\n" if header else ""
            out.append(Chunk(f"{parent_id}c{pi}", head + piece,
                             {**meta, "kind": "article", "section": name, "parent_id": parent_id,
                              "parent_text": body[:4000]}))
    return out


def load_articles(folder: Path, facts_by_id: dict, **kw) -> list[Chunk]:
    out = []
    for p in sorted(folder.glob("*.md")):
        sid = p.stem
        f = facts_by_id.get(sid, {})
        meta = {"spec_id": sid if f else "", "sci": f.get("sci", ""), "family": f.get("Family", ""),
                "doc_type": "article", "core": 1, "lang": "zh", "source": "synthetic", "kb_version": "syn-v1"}
        out.extend(split_markdown(p.read_text(encoding="utf-8"), f"art_{sid}", meta, **kw))
    return out
