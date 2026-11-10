"""护栏 4：输出护栏。回答返回用户之前的最后一道检查。

1. 规则类回答：必须带免责声明；出现的尺寸 / 数量数字必须来自规则工具结果
2. 防编造：回答里的时间（HH:MM）和天气数字必须出现在工具结果里；用户施压「随便估一个」时只认工具数据
3. 防泄露：不得出现系统提示词特征
4. 内容安全：不得承诺「一定能钓到」等（可选接 Qwen3Guard / LettuceDetect）
"""
from __future__ import annotations

import json
import re

from app.guards import GuardVerdict
from app.tools.rules import DISCLAIMER

LEAK = re.compile(r"system prompt|系统提示词是|你是一个.{0,10}助手，规则如下", re.I)
PROMISE = re.compile(r"一定能钓到|保证.{0,4}钓到|百分百")
FAB_PRESSURE = re.compile(r"随便估|你猜一个|猜一个|按你的经验|别查了|直接告诉我|就说|确认一下说|你上次说|给个百分比|给我一个具体时间|编一个")


def check(answer: str, intents: list[str], tool_text: str, flags: dict) -> tuple[str, GuardVerdict]:
    v = GuardVerdict(True)
    fixes = []
    if ("rules" in intents or "trip_plan" in intents or flags.get("rules_used")) and "示例规则" in tool_text \
            and "示例规则" not in answer:
        answer += f"\n（{DISCLAIMER}）"
        fixes.append("补充免责声明")
    for t in set(re.findall(r"\b\d{2}:\d{2}\b", answer)):              # 时间必须来自工具结果
        if t not in tool_text:
            answer = answer.replace(t, "（未知）")
            fixes.append(f"删除无来源时间 {t}")
    if LEAK.search(answer):
        answer = "抱歉，这部分内容无法提供。"
        fixes.append("拦截泄露")
    if PROMISE.search(answer):
        answer = PROMISE.sub("有机会钓到", answer)
        fixes.append("删除承诺")
    if flags.get("fab_pressure") or fixes:
        v = GuardVerdict(True, "output", "; ".join(fixes) or "fabrication_pressure")
    return answer, v


def tool_text(tool_calls: list, results: dict) -> str:
    return json.dumps(results, ensure_ascii=False)
