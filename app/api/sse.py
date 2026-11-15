"""SSE 流式对话：把 LangGraph 主图的执行过程按 Server-Sent Events 推给前端。

只是 service.run_turn 的流式版本，不改任何节点逻辑：
  - 用 GRAPH.stream(stream_mode="updates") 代替 invoke，每跑完一个节点推一条 `node` 事件（进度）
  - 回答经过 output_guard 审查后才分片推送（`delta`），避免把未审查的模型原文先流给用户
  - 最后推 `done`，内容与 /v1/chat 的返回结构一致（由调用方决定公开哪些字段）

事件顺序：start → node* → delta* → done        （出错时 done 里 behavior=partial_answer）
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator

from langgraph.types import Command

from app import config
from app.graph.main_graph import GRAPH
from app.tools.base import _CTX, RunContext, set_ctx

DELTA_CHARS = 24          # 每个 delta 事件的字数


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream_turn(user_id: str, thread_id: str, text: str = "", resume=None,
                today: str | None = None) -> Iterator[tuple[str, dict]]:
    """逐步产出 (event, data)。最后一个事件是 ("done", result)，result 与 run_turn 的返回结构相同。"""
    rc = RunContext(user_id=user_id, today=today or config.FROZEN_TODAY, thread_id=thread_id)
    token = set_ctx(rc)
    cfg = {"configurable": {"thread_id": thread_id, "run_ctx": rc}, "recursion_limit": config.MAX_GRAPH_STEPS}
    trace_id = uuid.uuid4().hex[:12]
    t0 = time.time()
    yield "start", {"trace_id": trace_id, "thread_id": thread_id}

    interrupt_msg, error = None, None
    try:
        inp = Command(resume=resume) if resume is not None else {"text": text, "image": None}
        for update in GRAPH.stream(inp, cfg, stream_mode="updates"):
            for node, value in update.items():
                if node == "__interrupt__":
                    interrupt_msg = value[0].value["message"]
                else:
                    yield "node", {"node": node, "elapsed_ms": int((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001 - 与 run_turn 一致：不向用户暴露堆栈
        error = f"{type(e).__name__}: {e}"

    state = GRAPH.get_state(cfg).values
    if interrupt_msg is not None:
        behavior, answer = "confirm_required", interrupt_msg
    elif error:
        behavior, answer = "partial_answer", "抱歉，处理时出了点问题，请稍后再试。"
    else:
        behavior, answer = state.get("behavior"), state.get("answer")

    for i in range(0, len(answer or ""), DELTA_CHARS):
        yield "delta", {"text": answer[i:i + DELTA_CHARS]}

    result = {
        "trace_id": trace_id, "thread_id": thread_id, "answer": answer, "behavior": behavior,
        "intents": state.get("intents") or [], "slots": {k: v for k, v in (state.get("slots") or {}).items() if v},
        "route_source": state.get("route_source"),
        "guard": rc.guard_hits[0]["guard"] if rc.guard_hits else None, "guard_hits": rc.guard_hits,
        "tool_calls": rc.tool_calls, "trace": rc.trace, "usage": rc.usage,
        "latency_ms": int((time.time() - t0) * 1000), "error": error,
    }
    try:
        _CTX.reset(token)
    except ValueError:      # 生成器的各步可能跑在不同线程 / 上下文里，reset 失败无害
        pass
    yield "done", result
