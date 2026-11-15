"""MCP server：把「潮汐查询」和「钓鱼知识 RAG 检索」暴露成 MCP 工具，供 Claude Desktop / Cursor 等客户端调用。

直接复用 app/tools 里的工具函数（同一套参数校验、超时、熔断、检索流程），这里只做协议适配。

调试：mcp dev mcp_server.py           （打开 MCP Inspector）
运行：python mcp_server.py             （stdio，给 MCP 客户端用）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # mcp dev 按文件路径加载时也能 import app

from mcp.server.fastmcp import FastMCP  # noqa: E402

from app import config  # noqa: E402
from app.tools.base import _CTX, RunContext, ToolResult, set_ctx  # noqa: E402
from app.tools.conditions import _fx  # noqa: E402
from app.tools.conditions import get_tide as _get_tide  # noqa: E402
from app.tools.knowledge import DOC_TYPES  # noqa: E402
from app.tools.knowledge import search_species_kb as _search_kb  # noqa: E402
from app.tools.normalize import find_location  # noqa: E402

mcp = FastMCP(
    "fishing-agent",
    instructions="钓鱼助手工具：get_tide 查沿海钓点的高/低潮；search_fishing_kb 检索鱼类知识库（栖息地、饵料、识别特征等）。"
                 "可用地点见资源 fishing://locations。",
    dependencies=["numpy", "chromadb>=1.0", "pyyaml"],   # mcp dev 用 uv 起临时环境时额外安装（只含检索 + 潮汐用到的包）
)


def _call(fn, **kwargs) -> dict:
    """每次 MCP 调用用独立的 RunContext：调用预算、去重缓存不在不同调用之间串。失败时抛错，客户端会看到 isError。"""
    token = set_ctx(RunContext(user_id="mcp", today=config.FROZEN_TODAY, thread_id="mcp"))
    try:
        res: ToolResult = fn(**kwargs)
    finally:
        _CTX.reset(token)
    if not res.ok:
        raise ValueError(res.error or "调用失败")
    return {"data": res.data, "source": res.source, "latency_ms": res.latency_ms}


def _location_id(location: str) -> str:
    if location in _fx()["locations"]:
        return location
    return find_location(location) or location      # 支持别名：Annapolis / 伊利湖 / Moreton Bay …


@mcp.tool()
def get_tide(location: str, date: str | None = None) -> dict:
    """查询沿海钓点某天的高/低潮时间与潮高（米，以平均海平面为基准）。

    location: 地点 id 或名称，如 annapolis、Shorncliffe、Moreton Bay（内陆水域没有潮汐，会返回错误）
    date: YYYY-MM-DD，今天起 7 天内；省略则为今天
    """
    return _call(_get_tide, location=_location_id(location), date=date or config.FROZEN_TODAY)


@mcp.tool()
def search_fishing_kb(query: str, scientific_name: str | None = None, doc_type: str | None = None) -> dict:
    """在鱼类知识库中做混合检索（向量 + BM25 + RRF + 重排），返回带 id 的资料片段。

    query: 自然语言问题，如「褐鳟喜欢什么水温」「Moreton Bay 适合钓什么」
    scientific_name: 可选，按学名过滤，如 Salmo trutta
    doc_type: 可选，overview / identification / habitat / diet_bait / rule_hint / article
    """
    return _call(_search_kb, query=query, scientific_name=scientific_name, doc_type=doc_type)


@mcp.resource("fishing://locations")
def locations() -> dict:
    """支持的钓点：id、名称、坐标、是否沿海（只有沿海钓点有潮汐），以及可查询的日期范围。"""
    fx = _fx()
    return {
        "today": config.FROZEN_TODAY if config.FIXTURE_MODE else None,
        "fixture_dates": sorted(next(iter(fx["tide"].values())).keys()) if config.FIXTURE_MODE else None,
        "doc_types": sorted(DOC_TYPES),
        "locations": [{"id": k, **{f: v[f] for f in ("name", "lat", "lon", "coastal")}} for k, v in fx["locations"].items()],
    }


if __name__ == "__main__":
    mcp.run()
