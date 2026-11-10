"""LangGraph 主图：Workflow 做骨架，子 Agent 处理开放部分。

input_guard ─▶ load_memory ─▶ router ─┬▶ rules_wf ─────────┐
   │（拦截）                            ├▶ conditions_wf ────┤
   │                                   ├▶ catch_wf（interrupt 确认）
   │                                   ├▶ identify（子 Agent）┤
   │                                   ├▶ knowledge（子 Agent）┤
   │                                   ├▶ planner（子 Agent）─┤
   │                                   ├▶ orchestrator（多意图，模式 A）
   │                                   ├▶ clarify / out_of_scope
   └──────────────────────────────────────────────────────────▶ output_guard（含 Compliance）─▶ update_memory ─▶ END

短期记忆：checkpointer（按 thread_id 保存 State，支持 interrupt 后恢复）；长期记忆：SQLite（按 user_id）。
"""
from __future__ import annotations

import json
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

from app import config as settings
from app.agents import compliance, identify, knowledge, planner
from app.graph import workflows as W
from app.graph.router import route
from app.guards import action as action_guard
from app.guards import input as input_guard
from app.guards import output as output_guard
from app.memory import extract, store
from app.prompts.loader import version
from app.tools.base import ctx, set_ctx


class State(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    text: str
    image: str | None
    profile: dict
    slots: dict               # 跨轮保留的槽位
    last_intents: list
    pending: dict             # 上一轮的追问：{intents, missing, text}
    clarify_count: int
    intents: list
    flags: dict
    missing: list
    route_source: str
    behavior: str
    answer: str
    facts: dict
    rules_used: bool
    draft: dict


def _bind(cfg):
    """把本轮的 RunContext 绑定到当前线程（user_id 来自鉴权，节点里通过 ctx() 读取）。"""
    rc = cfg["configurable"].get("run_ctx")
    if rc is not None:
        set_ctx(rc)
    return ctx()


def _trace(node: str, **kw):
    ctx().trace.append({"node": node, **kw})


CLARIFY_MSG = {"location": "你想去哪里钓？告诉我地点（比如 Chicago、Annapolis、Shorncliffe）。",
               "species": "你想查哪种鱼？", "image": "请上传鱼的照片，我来帮你识别。"}


# ------------------------------------------------------------------ 节点
def n_input_guard(s: State, config) -> Command:
    c = _bind(config)
    v = input_guard.check(s.get("text", ""), s.get("image"))
    if v.allowed:
        v = action_guard.precheck(s.get("text", ""), c.user_id)
    if v.guard:
        c.guard_hits.append({"guard": v.guard, "reason": v.reason})
    _trace("input_guard", allowed=v.allowed, guard=v.guard, reason=v.reason)
    if not v.allowed:
        return Command(goto="output_guard", update={"behavior": v.behavior, "answer": v.message,
                                                    "intents": _blocked_intents(s, v.reason),
                                                    "flags": {}, "facts": {}, "rules_used": False})
    return Command(goto="load_memory")


def _blocked_intents(s: State, reason: str) -> list:
    """被护栏拦截的请求不进入 Router，但仍记录它想做什么（便于分析攻击和误拦）。"""
    import re
    if reason in ("empty", "too_long"):
        return []
    if reason in ("image_format", "image_size"):
        return ["identify_fish"]
    if reason in ("harmful", "injection"):
        return ["out_of_scope"]
    if reason == "idor":
        return ["log_catch"] if re.search(r"记录|渔获|log_catch", s.get("text", "")) else ["out_of_scope"]
    return route(s.get("text", ""), s.get("image"), {}, {}, ctx().today)["intents"]


def n_load_memory(s: State, config):
    c = _bind(config)
    prof = store.get_profile(c.user_id)
    _trace("load_memory", profile_keys=list(prof))
    return {"profile": prof}


def n_router(s: State, config) -> Command:
    c = _bind(config)
    prev = {"slots": s.get("slots") or {}, "last_intents": s.get("last_intents") or [], "pending": s.get("pending") or {}}
    r = route(s["text"], s.get("image"), prev, s.get("profile") or {}, c.today)
    upd = {"intents": r["intents"], "slots": r["slots"], "flags": r["flags"], "missing": r["missing"],
           "route_source": r["source"], "facts": {}, "rules_used": False}
    _trace("router", intents=r["intents"], source=r["source"], missing=r["missing"], prompt=version("router"),
           slots={k: v for k, v in r["slots"].items() if v and k not in ("species_all", "locations")})
    cc = (s.get("clarify_count") or 0) if prev["pending"] else 0
    if r["missing"]:
        if cc >= config_max_clarify():                     # 追问上限：不再追问，说明缺什么
            return Command(goto="output_guard", update={**upd, "behavior": "answer", "pending": {}, "clarify_count": 0,
                           "answer": f"缺少{('地点' if 'location' in r['missing'] else '必要信息')}，无法给出具体建议；"
                                     "需要地点时请提供地点（如 Chicago）。"})
        return Command(goto="output_guard", update={**upd, "behavior": "clarify", "answer": CLARIFY_MSG[r["missing"][0]],
                       "clarify_count": cc + 1, "pending": {"intents": r["intents"], "missing": r["missing"],
                                                          "text": prev["pending"].get("text") or s["text"]}})
    upd.update(pending={}, clarify_count=0)
    if r["flags"].get("resume_text"):
        upd["text"] = r["flags"]["resume_text"] + " " + s["text"]
    target = {"rules": "rules_wf", "conditions": "conditions_wf", "log_catch": "catch_wf", "identify_fish": "identify",
              "species_info": "knowledge", "trip_plan": "planner", "out_of_scope": "out_of_scope"}
    goto = "orchestrator" if len(r["intents"]) > 1 and "identify_fish" not in r["intents"] else target[r["intents"][0]]
    return Command(goto=goto, update=upd)


def config_max_clarify():
    return settings.MAX_CLARIFY_TURNS


HANDLERS = {
    "rules": lambda s, t: W.rules_wf(s["text"], s["slots"], s["flags"], t),
    "conditions": lambda s, t: W.conditions_wf(s["text"], s["slots"], s["flags"], t),
    "species_info": lambda s, t: knowledge.run(s["text"], s["slots"], s["flags"], t),
    "trip_plan": lambda s, t: planner.run(s["text"], s["slots"], s["flags"], t),
    "out_of_scope": lambda s, t: W.out_of_scope(s["text"], s["slots"], s["flags"], t),
}


def _handler_node(intent: str, name: str):
    def node(s: State, config):
        c = _bind(config)
        res = HANDLERS[intent](s, c.today)
        _trace(name, behavior=res["behavior"])
        return {"behavior": res["behavior"], "answer": res["answer"], "facts": res.get("facts", {}),
                "rules_used": res.get("rules_used", intent == "rules")}
    return node


def n_identify(s: State, config):
    c = _bind(config)
    res = identify.run(s["text"], s["slots"], s["flags"], c.today, "rules" in s["intents"])
    _trace("identify_agent", behavior=res["behavior"], prompt=version("identify"))
    return {"behavior": res["behavior"], "answer": res["answer"], "facts": res.get("facts", {}),
            "rules_used": res.get("rules_used", False)}


def n_orchestrator(s: State, config):
    """模式 A：多意图一次拆清 → 按依赖顺序各派发一次 → 直接汇总，不循环。"""
    c = _bind(config)
    order = [i for i in ("rules", "species_info", "conditions", "trip_plan", "log_catch", "out_of_scope") if i in s["intents"]]
    parts, facts, beh = [], {}, "answer"
    for i in order:
        if i == "log_catch":
            continue
        r = HANDLERS[i](s, c.today)
        parts.append(r["answer"])
        facts[i] = r.get("facts", {})
        if r["behavior"] in ("clarify", "partial_answer") and beh == "answer":
            beh = r["behavior"]
    _trace("orchestrator", dispatched=order)
    return {"behavior": beh, "answer": "\n\n".join(parts), "facts": facts, "rules_used": "rules" in order}


def n_catch(s: State, config):
    """渔获 / 记忆写入 Workflow：准备草稿 → interrupt() 等用户确认 → 写入。写入在代码里强制确认，模型绕不过去。"""
    c = _bind(config)
    prep = W.catch_prepare(s["text"], s["slots"], s["flags"], c.today)
    if prep["behavior"] != "confirm_required":
        _trace("catch_wf", behavior=prep["behavior"])
        return {"behavior": prep["behavior"], "answer": prep["answer"], "facts": prep.get("facts", {})}
    _trace("catch_wf", behavior="confirm_required", draft=prep["draft"])
    decision = interrupt({"message": prep["answer"], "draft": prep["draft"]})      # 暂停，等 /v1/confirm
    res = W.catch_commit(prep["draft"], decision, c.thread_id)
    _trace("catch_wf", behavior=res["behavior"], committed=res["answer"].startswith("已记录") or res["answer"].startswith("已删除"))
    return {"behavior": res["behavior"], "answer": res["answer"], "facts": {}, "rules_used": False}


def n_output_guard(s: State, config):
    """护栏 4 + Compliance 子 Agent（审查 → 打回 → 重写，最多 MAX_REWRITE 次）。"""
    c = _bind(config)
    ans, intents, facts = s.get("answer", ""), s.get("intents") or [], s.get("facts") or {}
    rules = _extract_rules(facts)
    extra = {str(x) for x in ((s.get("slots") or {}).get("count"), (s.get("slots") or {}).get("length_cm")) if x is not None}
    for attempt in range(settings.MAX_REWRITE):
        rev = compliance.review(ans, rules, extra) if rules else {"pass": True, "problems": []}
        if rev["pass"]:
            break
        _trace("compliance", attempt=attempt + 1, problems=rev["problems"])
        if attempt + 1 == settings.MAX_REWRITE:               # 重写仍不过：只输出工具原始结果
            ans = "\n".join(W.describe_rule(f, s["text"]) for f in rules.values())
        elif "示例规则" not in ans:
            ans += "（示例规则，请以当地法规为准）"
    tool_text = json.dumps([t for t in c.tool_calls], ensure_ascii=False) + json.dumps(facts, ensure_ascii=False, default=str)
    tool_text += json.dumps(_tool_results(c), ensure_ascii=False, default=str)
    ans, v = output_guard.check(ans, intents, tool_text + ("示例规则" if s.get("rules_used") else ""),
                                {**(s.get("flags") or {}), "rules_used": s.get("rules_used")})
    if v.guard:
        c.guard_hits.append({"guard": "output", "reason": v.reason})
    _trace("output_guard", fixes=v.reason)
    return {"answer": ans, "messages": [("user", s.get("text", "")), ("ai", ans)]}


def _extract_rules(facts) -> dict:
    """在各处理器返回的 facts 里找出规则工具结果，统一成 {学名: 规则}。"""
    out = {}
    def walk(x):
        if isinstance(x, dict):
            if "scientific_name" in x and "catch_policy" in x:
                out[x["scientific_name"]] = x
            else:
                for v in x.values():
                    walk(v)
    walk(facts)
    return out


def _tool_results(c):
    return [r.data for r in c.cache.values() if r.ok]


def n_update_memory(s: State, config):
    c = _bind(config)
    written = extract.update(c.user_id, s.get("flags") or {}, c.today)
    _trace("update_memory", written=written)
    return {"last_intents": s.get("intents") or s.get("last_intents") or []}


def build_graph(checkpointer=None):
    g = StateGraph(State)
    g.add_node("input_guard", n_input_guard)
    g.add_node("load_memory", n_load_memory)
    g.add_node("router", n_router)
    g.add_node("rules_wf", _handler_node("rules", "rules_wf"))
    g.add_node("conditions_wf", _handler_node("conditions", "conditions_wf"))
    g.add_node("knowledge", _handler_node("species_info", "knowledge_agent"))
    g.add_node("planner", _handler_node("trip_plan", "planner_agent"))
    g.add_node("out_of_scope", _handler_node("out_of_scope", "out_of_scope"))
    g.add_node("identify", n_identify)
    g.add_node("orchestrator", n_orchestrator)
    g.add_node("catch_wf", n_catch)
    g.add_node("output_guard", n_output_guard)
    g.add_node("update_memory", n_update_memory)
    g.add_edge(START, "input_guard")
    g.add_edge("load_memory", "router")
    for n in ("rules_wf", "conditions_wf", "knowledge", "planner", "out_of_scope", "identify", "orchestrator", "catch_wf"):
        g.add_edge(n, "output_guard")
    g.add_edge("output_guard", "update_memory")
    g.add_edge("update_memory", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


GRAPH = build_graph()
