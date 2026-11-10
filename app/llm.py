"""LLM 接入：统一走 OpenAI 兼容接口（LiteLLM 网关 / 托管推理），按节点区分参数，自动记录 token。
离线模式（没配 key）返回 None，调用方降级为规则 / 模板实现。"""
from __future__ import annotations

from app import config
from app.tools.base import ctx

PARAMS = {   # 按节点设置参数（见计划 9.6）
    "router": dict(model=config.MODEL_SMALL, temperature=0, max_tokens=200),
    "agent": dict(model=config.MODEL_SMALL, temperature=0.1, max_tokens=500),
    "planner": dict(model=config.MODEL_LARGE, temperature=0.1, max_tokens=600),
    "answer": dict(model=config.MODEL_SMALL, temperature=0.3, top_p=0.9, max_tokens=600),
    "compliance": dict(model=config.MODEL_SMALL, temperature=0, max_tokens=300),
}


def get_llm(role: str):
    if config.OFFLINE:
        return None
    from langchain_openai import ChatOpenAI
    p = dict(PARAMS[role])
    top_p = p.pop("top_p", None)
    extra = {"extra_body": {"enable_thinking": False}} if config.DISABLE_THINKING else {}
    return ChatOpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY, timeout=60, max_retries=2, seed=7,
                      **({"top_p": top_p} if top_p else {}), **extra, **p)


def record_usage(msg) -> None:
    u = getattr(msg, "usage_metadata", None) or {}
    c = ctx()
    c.usage["in"] += u.get("input_tokens", 0)
    c.usage["out"] += u.get("output_tokens", 0)
    c.usage["llm_calls"] += 1
