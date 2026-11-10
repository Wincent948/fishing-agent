"""知识类工具：混合检索、知识图谱查询、识鱼（mock / BioCLIP）、以图搜图、渔获与记忆。"""
from __future__ import annotations

import csv
import json
import os
from functools import lru_cache
from pathlib import Path

from app import config
from app.kb.graph import G, parse_question
from app.kb.index import KB
from app.memory import store
from app.tools.base import ToolResult, ctx, tool_wrapper
from app.tools.normalize import find_species

DOC_TYPES = {"overview", "identification", "habitat", "diet_bait", "rule_hint", "article"}


@tool_wrapper("search_species_kb")
def search_species_kb(query: str, scientific_name: str | None = None, doc_type: str | None = None) -> ToolResult:
    """在鱼类知识库里做混合检索（向量 + BM25 + RRF + 重排）。可按学名、文档类型过滤。返回带 id 的资料，回答时必须引用 id。"""
    if doc_type and doc_type not in DOC_TYPES:
        return ToolResult(False, error=f"【知识检索】doc_type 只能是 {sorted(DOC_TYPES)}", source="validation")
    filters = {"sci": scientific_name, "doc_type": doc_type}
    thr = 0.0 if scientific_name else None             # 已按学名精确过滤：资料一定属于这条鱼，不再卡相关度
    hits = KB().search(query, filters, min_relevance=thr)
    if not hits and scientific_name:                 # 过滤太严时放宽一次
        hits = KB().search(query, {"sci": scientific_name}, min_relevance=0.0)
    if not hits:
        return ToolResult(False, error="【知识检索】知识库里没有找到足够相关的资料。", source="not_found")
    return ToolResult(True, hits, source="kb")


@tool_wrapper("query_species_graph")
def query_species_graph(question: str) -> ToolResult:
    """用知识图谱回答多条件关系题（如「哪些淡水底栖鱼体长超过 50 cm」）。只读，只在 40 种核心鱼范围内回答。"""
    hint = find_species(question)["sci"]
    cond = parse_question(question, ctx().today, hint)
    if not cond:
        return ToolResult(False, error="【图谱】无法把问题解析成图谱查询条件。", source="validation")
    return ToolResult(True, {"conditions": cond, "answer_set": G().run(cond)}, source="graph")


@lru_cache(maxsize=1)
def _registry() -> dict:
    with open(config.DATA / "images.csv", encoding="utf-8") as f:
        return {r["image_id"]: r for r in csv.DictReader(f)}


def image_meta(path: str | None) -> dict | None:
    return _registry().get(Path(path).name) if path else None


@tool_wrapper("identify_fish", retries=1, timeout_s=90)   # VLM 看图要几秒到十几秒
def identify_fish(image: str, top_k: int = 3) -> ToolResult:
    """识别图片中的鱼种，返回 top-k 候选与置信度，以及判定：confident / uncertain / not_in_library / not_fish。
    评测登记表里的图片：mock（可复现）；其他真实照片且 VISION=qwen：用 VLM 识别（app/tools/vision.py）。"""
    meta = image_meta(image)
    if meta:
        top = [{"sci": s, "p": p} for s, p in json.loads(meta["mock_top"])][:top_k]
        is_fish, source, extra = meta["profile"] != "non_fish", "mock_vision", {}
    elif config.VISION == "qwen" and not config.OFFLINE and image and Path(image).expanduser().is_file():
        from app.tools.vision import vlm_identify
        out = vlm_identify(str(Path(image).expanduser()), top_k)
        top, is_fish, source = out["candidates"], out["is_fish"], "vlm"
        extra = {"features": out["features"], "samples": out["samples"]}
    else:
        hint = "（真实照片识别需要在 .env 里设置 VISION=qwen 并配置 LLM key）" if image and Path(image).expanduser().is_file() else ""
        return ToolResult(False, error=f"【识鱼】无法读取这张图片。{hint}", source="validation")
    if not is_fish:
        verdict = "not_fish"
    elif not top or top[0]["p"] < config.ID_REJECT_THRESHOLD:
        verdict = "not_in_library"
    elif top[0]["p"] < config.ID_CONF_THRESHOLD:
        verdict = "uncertain"
    else:
        verdict = "confident"
    return ToolResult(True, {"candidates": top, "verdict": verdict, **extra}, source=source)


@tool_wrapper("search_images")
def search_images(query: str, k: int = 3) -> ToolResult:
    """以文搜图：返回与描述最相关的库内图片 id（离线按鱼名匹配；在线用 Qwen3-VL-Embedding）。"""
    sci = find_species(query)["sci"]
    if not sci:
        return ToolResult(False, error="【搜图】没识别出要找哪种鱼。", source="validation")
    ids = [k_ for k_, r in _registry().items() if r["species"] == sci and r["split"] == "train"][:k]
    return ToolResult(True, {"species": sci, "images": ids}, source="mock_vision")


# ---------------- 写入类工具：只能在用户确认后由 Workflow 调用（护栏 3）

@tool_wrapper("log_catch", idempotent=False)
def log_catch(species: str, count: int, length_cm: float | None = None, location: str | None = None,
              date: str | None = None, idem_key: str = "") -> ToolResult:
    """写入一条渔获记录（写操作，需用户确认）。user_id 来自鉴权上下文，不接受参数传入。"""
    if count is None or count <= 0:
        return ToolResult(False, error="【渔获】数量必须是正整数。", source="validation")
    if length_cm is not None and length_cm <= 0:
        return ToolResult(False, error="【渔获】长度必须大于 0。", source="validation")
    c = ctx()
    ok = store.add_catch(c.user_id, {"date": date or c.today, "species": species, "count": count,
                                     "length_cm": length_cm, "location": location}, idem_key or f"{c.thread_id}-{species}-{count}")
    return ToolResult(True, {"written": ok}, source="sqlite")


@tool_wrapper("list_catches")
def list_catches(limit: int = 5) -> ToolResult:
    """读取当前用户自己的渔获记录（只读，按 user_id 隔离）。"""
    return ToolResult(True, store.list_catches(ctx().user_id, limit), source="sqlite")


@tool_wrapper("forget_memory", idempotent=False)
def forget_memory(key: str | None = None) -> ToolResult:
    """删除当前用户的长期记忆（写操作，需用户确认）。"""
    return ToolResult(True, {"deleted": store.delete_profile(ctx().user_id, key)}, source="sqlite")
