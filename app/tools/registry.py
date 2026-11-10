"""把工具函数包装成 LangChain StructuredTool，供在线模式 bind_tools 使用（JSON Schema 由函数签名生成）。"""
from __future__ import annotations

from langchain_core.tools import StructuredTool

from app.tools import conditions, knowledge, rules

FUNCS = {
    "get_species_rules": rules.get_species_rules, "get_weather": conditions.get_weather, "get_tide": conditions.get_tide,
    "best_window": conditions.best_window, "search_species_kb": knowledge.search_species_kb,
    "query_species_graph": knowledge.query_species_graph, "identify_fish": knowledge.identify_fish,
    "search_images": knowledge.search_images, "list_catches": knowledge.list_catches,
}  # 写入工具（log_catch / forget_memory）不交给模型，只由确认后的 Workflow 调用


def lc_tools(names: list[str]):
    out = []
    for n in names:
        f = FUNCS[n]
        out.append(StructuredTool.from_function(func=lambda _f=f, **kw: _f(**kw).brief(), name=n,
                                                description=(f.__wrapped__.__doc__ or n).strip(),
                                                args_schema=None, infer_schema=True))
    return out
