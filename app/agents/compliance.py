"""Compliance 子 Agent：审查回答里的规则数字是否和规则工具结果一致（Reflexion：审查 → 打回 → 重写，最多 2 次）。
独立于生成回答的节点，避免「自己检查自己」。离线用规则审查；在线可交给 LLM 输出 {pass, problems}。"""
from __future__ import annotations

import re


def review(answer: str, rules: dict | None, extra: set | None = None) -> dict:
    if not rules:
        return {"pass": True, "problems": []}
    facts = rules.values()
    allowed = set(extra or ())
    for f in facts:
        allowed |= {str(v) for v in (f.get("min_size_cm"), f.get("daily_limit"), f.get("length_cm")) if v is not None}
        if f.get("length_cm") is not None:
            allowed.add(f"{f['length_cm']:g}")
    problems = []
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(cm|条)", answer):
        if num not in allowed and f"{float(num):g}" not in allowed:
            problems.append(f"数字 {num}{unit} 不在规则工具结果里")
    if "示例规则" not in answer:
        problems.append("缺少示例规则免责声明")
    return {"pass": not problems, "problems": problems}
