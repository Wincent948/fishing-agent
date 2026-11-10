"""上下文组装：固定顺序 + 每层 token 预算 + 超预算按「从轻到重」压缩（见计划 8.1）。
在线模式把结果交给 LLM；离线模式只用来统计每轮上下文规模（写进 trace）。"""
from __future__ import annotations

from app.kb.text import count_tokens

BUDGET = {"system": 1200, "tools": 600, "profile": 200, "summary": 300, "slots": 150, "recent": 2000, "retrieved": 2500}
TOTAL, RESERVED_OUTPUT = 8000, 800


def _fit(text: str, budget: int) -> str:
    if count_tokens(text) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:                                   # 二分截断到预算内
        mid = (lo + hi + 1) // 2
        lo, hi = (mid, hi) if count_tokens(text[:mid]) <= budget else (lo, mid - 1)
    return text[:lo] + "…"


def build(system: str, tools_desc: str, profile: dict, summary: str, slots: dict, recent: list[str],
          retrieved: list[dict], question: str) -> dict:
    recent_kept = []
    for m in reversed(recent):                       # 最近的轮次优先保留（滑动窗口）
        if count_tokens("\n".join([m, *recent_kept])) > BUDGET["recent"]:
            break
        recent_kept.insert(0, m)
    layers = {
        "system": _fit(system, BUDGET["system"]), "tools": _fit(tools_desc, BUDGET["tools"]),
        "profile": _fit(str(profile), BUDGET["profile"]), "summary": _fit(summary, BUDGET["summary"]),
        "slots": str({k: v for k, v in slots.items() if v}), "recent": "\n".join(recent_kept),
        "retrieved": _fit("\n".join(f"<retrieved id={h['id']}>{h['text']}</retrieved>" for h in retrieved), BUDGET["retrieved"]),
        "question": question,                        # 当前问题：不压缩
    }
    used = {k: count_tokens(v) for k, v in layers.items()}
    assert sum(used.values()) + RESERVED_OUTPUT <= TOTAL + count_tokens(question), "上下文超预算"
    return {"layers": layers, "tokens": used, "total": sum(used.values())}
