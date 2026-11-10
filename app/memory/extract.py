"""每轮结束后的长期记忆写入：只写用户明确说出的稳定事实（离线由 Router 抽取，在线可用 memory_extract 提示词）。"""
from __future__ import annotations

from app.memory import store


def update(user_id: str, flags: dict, today: str) -> list[str]:
    written = []
    for k, v in (flags.get("profile_update") or {}).items():
        if v:
            store.set_profile(user_id, k, v, today)
            written.append(k)
    return written
