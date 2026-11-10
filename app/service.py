"""对外的统一入口：跑一轮对话，返回回答 + 结构化运行记录（API 和评测都调用它）。"""
from __future__ import annotations

import time
import uuid

from langgraph.types import Command

from app import config
from app.graph.main_graph import GRAPH
from app.tools.base import RunContext, set_ctx


def run_turn(user_id: str, thread_id: str, text: str = "", image: str | None = None, resume=None,
             today: str | None = None) -> dict:
    rc = RunContext(user_id=user_id, today=today or config.FROZEN_TODAY, thread_id=thread_id)
    token = set_ctx(rc)
    cfg = {"configurable": {"thread_id": thread_id, "run_ctx": rc}, "recursion_limit": config.MAX_GRAPH_STEPS}
    trace_id = uuid.uuid4().hex[:12]
    t0 = time.time()
    try:
        inp = Command(resume=resume) if resume is not None else {"text": text, "image": image}
        out = GRAPH.invoke(inp, cfg)
        error = None
    except Exception as e:  # noqa: BLE001 - 兜底：任何异常都不暴露堆栈
        out, error = {}, f"{type(e).__name__}: {e}"
    finally:
        pass
    state = GRAPH.get_state(cfg).values
    interrupted = bool(out.get("__interrupt__")) if isinstance(out, dict) else False
    if interrupted:
        behavior, answer = "confirm_required", out["__interrupt__"][0].value["message"]
    elif error:
        behavior, answer = "partial_answer", "抱歉，处理时出了点问题，请稍后再试。"
    else:
        behavior, answer = state.get("behavior"), state.get("answer")
    res = {
        "trace_id": trace_id, "thread_id": thread_id, "answer": answer, "behavior": behavior,
        "intents": state.get("intents") or [], "slots": {k: v for k, v in (state.get("slots") or {}).items() if v},
        "route_source": state.get("route_source"),
        "guard": rc.guard_hits[0]["guard"] if rc.guard_hits else None, "guard_hits": rc.guard_hits,
        "tool_calls": rc.tool_calls, "trace": rc.trace, "usage": rc.usage,
        "latency_ms": int((time.time() - t0) * 1000), "error": error,
    }
    from app.tools.base import _CTX
    _CTX.reset(token)
    return res
