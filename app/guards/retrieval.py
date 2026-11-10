"""护栏 2：资料护栏。检索结果进入上下文之前，丢弃不可信来源和带指令的块（防间接注入）。"""
from __future__ import annotations

import re

from app.guards.input import INJECTION

SUSPICIOUS = re.compile(r"https?://|点击|手机号|管理员|ignore|忽略|forget your|reply only|new instruction|"
                        r"call log_catch|系统通知|<system>", re.I)


def filter_hits(hits: list[dict]) -> tuple[list[dict], list[str]]:
    kept, dropped = [], []
    for h in hits or []:
        if h.get("source") == "untrusted" or SUSPICIOUS.search(h["text"]) or INJECTION.search(h["text"]):
            dropped.append(h["id"])
        else:
            kept.append(h)
    return kept, dropped
