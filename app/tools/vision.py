"""真实识鱼：用支持看图的 LLM（VLM，默认 Qwen3.5-9B）从库内鱼种里选候选。

开关：.env 里设 VISION=qwen。评测登记表里的图片仍走 mock（保证评测可复现），
其他真实照片文件走这里。

自洽性（self-consistency）：ID_SAMPLES>1 时用较高温度采样多次，对各候选的概率取平均，
减少单次输出的偶然性；默认 1 次以节省成本。
注意：VLM 自己报的 p 不是校准过的概率，阈值需要用真实照片重新标定。
"""
from __future__ import annotations

import base64
import csv
import json
import mimetypes
import re
from functools import lru_cache
from pathlib import Path

from app import config
from app.prompts.loader import load


@lru_cache(maxsize=1)
def library() -> list[dict]:
    """识别范围：核心 40 种（学名 / 英文名 / 中文名）。"""
    with open(config.EVAL / "species.csv", encoding="utf-8") as f:
        return [{"sci": r["scientific_name"], "en": r["common_en"], "zh": r["zh_ref"]} for r in csv.DictReader(f)]


def _get_vlm(temperature: float):
    """单独一个函数，方便测试时替换成假模型。"""
    from langchain_openai import ChatOpenAI
    extra = {"extra_body": {"enable_thinking": False}} if config.DISABLE_THINKING else {}
    return ChatOpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY, model=config.MODEL_VISION,
                      temperature=temperature, max_tokens=300, timeout=60, max_retries=2, **extra)


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)          # 模型偶尔会在 JSON 外多说话，只取第一段 JSON
    if not m:
        raise ValueError(f"VLM 没有返回 JSON：{(text or '')[:80]}")
    return json.loads(m.group(0))


def vlm_identify(path: str, top_k: int = 3) -> dict:
    """返回 {"is_fish": bool, "candidates": [{"sci", "p"}], "features": str, "samples": n}。"""
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.llm import record_usage

    lib = library()
    valid = {s["sci"] for s in lib}
    lib_text = "\n".join(f"- {s['sci']}（{s['en']}，{s['zh']}）" for s in lib)
    n = max(1, config.ID_SAMPLES)
    llm = _get_vlm(temperature=0.0 if n == 1 else 0.7)
    msgs = [SystemMessage(load("vision")["system"]),
            HumanMessage(content=[
                {"type": "text", "text": f"候选鱼种库：\n{lib_text}\n\n请识别这张图片。"},
                {"type": "image_url", "image_url": {"url": _data_url(Path(path))}},
            ])]
    votes, fish_votes, features = {}, 0, ""
    for _ in range(n):
        ai = llm.invoke(msgs)
        record_usage(ai)
        out = _parse(ai.content)
        fish_votes += bool(out.get("is_fish"))
        features = features or out.get("visible_features", "")
        for c in out.get("candidates", [])[:top_k]:
            sci, p = c.get("scientific_name"), float(c.get("p", 0))
            if sci in valid:                               # 防编造：库外学名直接丢弃
                votes[sci] = votes.get(sci, 0.0) + max(0.0, min(1.0, p))
    cands = sorted(({"sci": s, "p": round(v / n, 3)} for s, v in votes.items()), key=lambda c: -c["p"])[:top_k]
    return {"is_fish": fish_votes * 2 > n, "candidates": cands, "features": features, "samples": n}
