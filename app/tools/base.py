"""工具的统一外壳：标准返回、超时、重试、熔断、调用预算、重复调用拦截、埋点。

每个工具函数用 @tool_wrapper 装饰后：
  - 返回统一的 ToolResult（ok / data / error / source / latency_ms）
  - 报错时返回给 LLM 能看懂的提示，而不是堆栈
  - 受「本轮工具预算」约束（防自旋）：总次数 ≤ MAX_TOOL_CALLS，同一工具 ≤ MAX_SAME_TOOL
  - 同一工具 + 同一参数重复调用时直接返回上次结果，不再执行
  - 每次调用记入 RunContext.tool_calls，供 trace 和评测使用
"""
from __future__ import annotations

import concurrent.futures as cf
import contextvars
import functools
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app import config


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None
    source: str = ""
    latency_ms: int = 0

    def brief(self) -> str:
        """给 LLM 看的精简版本（返回精简：不把原始大数组塞进上下文）。"""
        return json.dumps({"ok": self.ok, "data": self.data, "error": self.error}, ensure_ascii=False)[:1500]


@dataclass
class RunContext:
    """一次请求（一轮对话）的运行上下文。user_id 来自鉴权，绝不由 LLM 填写。"""
    user_id: str = "anonymous"
    today: str = config.FROZEN_TODAY
    thread_id: str = ""
    tool_calls: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0, "llm_calls": 0})
    cache: dict = field(default_factory=dict)
    guard_hits: list = field(default_factory=list)


_CTX: contextvars.ContextVar[RunContext] = contextvars.ContextVar("run_ctx", default=RunContext())


def ctx() -> RunContext:
    return _CTX.get()


def set_ctx(c: RunContext):
    return _CTX.set(c)


# ---------------- 熔断器：连续失败 N 次后，一段时间内直接失败
_BREAKER: dict[str, dict] = {}
BREAKER_FAILS, BREAKER_COOLDOWN_S = 5, 60


def _breaker_open(name: str) -> bool:
    b = _BREAKER.get(name)
    return bool(b and b["fails"] >= BREAKER_FAILS and time.time() - b["since"] < BREAKER_COOLDOWN_S)


def _breaker_record(name: str, ok: bool):
    b = _BREAKER.setdefault(name, {"fails": 0, "since": 0.0})
    if ok:
        b["fails"] = 0
    else:
        b["fails"] += 1
        b["since"] = time.time()


_POOL = cf.ThreadPoolExecutor(max_workers=8)


def tool_wrapper(name: str, *, idempotent: bool = True, retries: int = 2, timeout_s: float | None = None):
    def deco(fn: Callable[..., ToolResult]):
        @functools.wraps(fn)
        def run(**kwargs) -> ToolResult:
            c = ctx()
            key = hashlib.md5(f"{name}:{json.dumps(kwargs, sort_keys=True, ensure_ascii=False)}".encode()).hexdigest()
            record = {"name": name, "args": kwargs, "ok": False, "latency_ms": 0, "note": ""}
            if key in c.cache:                                         # 重复调用：直接返回已有结果
                record.update(ok=c.cache[key].ok, note="dedup")
                c.tool_calls.append(record)
                return c.cache[key]
            executed = [t for t in c.tool_calls if t["note"] != "dedup"]
            if len(executed) >= config.MAX_TOOL_CALLS or sum(t["name"] == name for t in executed) >= config.MAX_SAME_TOOL:
                res = ToolResult(False, error=f"【{name}】本轮调用次数已达上限，请基于已有信息回答。", source="budget")
                record.update(note="budget")
                c.tool_calls.append(record)
                return res
            if _breaker_open(name):
                res = ToolResult(False, error=f"【{name}】服务暂时不可用（熔断中），请稍后再试。", source="breaker")
                record.update(note="breaker")
                c.tool_calls.append(record)
                return res
            t0 = time.time()
            attempts = (retries + 1) if idempotent else 1               # 写入类工具不自动重试
            res = ToolResult(False, error=f"【{name}】调用失败")
            for _ in range(attempts):
                try:
                    fut = _POOL.submit(contextvars.copy_context().run, fn, **kwargs)
                    res = fut.result(timeout=timeout_s or config.TOOL_TIMEOUT_S)
                    if res.ok or res.source in ("validation", "not_found", "not_applicable"):
                        break
                except cf.TimeoutError:
                    res = ToolResult(False, error=f"【{name}】超时（>{timeout_s or config.TOOL_TIMEOUT_S}s），数据暂不可用。",
                                     source="timeout")
                except Exception as e:  # noqa: BLE001 - 不把堆栈暴露给 LLM
                    res = ToolResult(False, error=f"【{name}】内部错误：{type(e).__name__}", source="exception")
            res.latency_ms = int((time.time() - t0) * 1000)
            _breaker_record(name, res.ok or res.source in ("validation", "not_found", "not_applicable"))
            record.update(ok=res.ok, latency_ms=res.latency_ms, source=res.source, error=res.error)
            c.tool_calls.append(record)
            c.cache[key] = res
            return res
        run.tool_name = name
        return run
    return deco
