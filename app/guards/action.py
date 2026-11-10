"""护栏 3：动作护栏。

- 越权：user_id 只来自鉴权上下文；任何「查 / 改别的用户」的请求直接拒绝
- 写入：log_catch / forget_memory 必须经过 interrupt() 确认；用户声称「已确认 / 跳过确认」一律无效
- 白名单：每个子 Agent 只能调用自己的工具子集（见 ALLOWED）
"""
from __future__ import annotations

import re

from app.guards import GuardVerdict

IDOR = re.compile(r"u_eval_\d+|user_id|所有用户|全部用户|其他用户|上一个用户|别人的|我朋友.{0,8}(钓|记录)|"
                  r"导出.{0,6}(用户|渔获)|数据库|select \*|from catch_log", re.I)
SKIP_CONFIRM = re.compile(r"不用(问我)?确认|不要确认|无需确认|跳过确认|别弹确认|已经确认|已确认|confirm\s*=\s*true|"
                          r"HITL\s*=\s*off|自动确认|已获授权|授权你|直接写入|直接存|直接帮我记", re.I)

WRITE_TOOLS = {"log_catch", "forget_memory"}
ALLOWED = {
    "identify": {"identify_fish", "search_images", "get_species_rules"},
    "knowledge": {"search_species_kb", "query_species_graph"},
    "planner": {"get_species_rules", "get_weather", "get_tide", "best_window", "search_species_kb"},
    "compliance": {"get_species_rules"},
}


def precheck(text: str, user_id: str) -> GuardVerdict:
    for m in re.findall(r"u_eval_\w+", text):
        if m != user_id:
            return GuardVerdict(False, "action", "idor", "refuse", "只能查看和管理你自己的数据，无法访问其他用户的信息。")
    if IDOR.search(text):
        return GuardVerdict(False, "action", "idor", "refuse", "只能查看和管理你自己的数据，无法访问其他用户的信息。")
    if SKIP_CONFIRM.search(text):
        return GuardVerdict(True, "action", "skip_confirm_attempt",
                            notes=["用户试图跳过确认：写入仍然必须经过确认"])
    return GuardVerdict(True)


def tool_allowed(agent: str, tool: str) -> bool:
    return tool in ALLOWED.get(agent, set())
