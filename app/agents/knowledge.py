"""Knowledge 子 Agent（Agentic RAG）。

在线模式：LLM 绑定 {search_species_kb, query_species_graph}，ReAct 循环（think → tools → think），
          由模型决定查什么、查几次、何时停止；受工具预算和步数上限约束。
离线模式：用规则做同样的决定（关系题 → 图谱；单鱼种 → 按主题过滤检索），再用模板组织带引用的回答。
"""
from __future__ import annotations

import re

from app.graph.workflows import R, zh_name
from app.guards.retrieval import filter_hits
from app.llm import get_llm, record_usage
from app.prompts.loader import load
from app.tools.base import ctx
from app.tools.knowledge import query_species_graph, search_species_kb

TOPIC = [
    (r"吃什么|食性|饵|怎么钓|用什么", "diet_bait"),
    (r"习性|生活|栖息|水域|水层|淡水|海水|半咸水|咸水|底层|中上层|活跃", "habitat"),
    (r"区分|识别|长什么样|特征", "identification"),
    (r"最大|最长|最重|体长|多重|多大|哪个科|属于|科的|max length", "overview"),
]


def _topic(text):
    return next((d for p, d in TOPIC if re.search(p, text, re.I)), None)


def _search(query, sci=None, doc_type=None):
    res = search_species_kb(query=query, scientific_name=sci, doc_type=doc_type)
    if not res.ok:
        return []
    kept, dropped = filter_hits(res.data)
    if dropped:
        ctx().guard_hits.append({"guard": "retrieval", "reason": "dropped " + ",".join(dropped)})
    return kept


def run(text: str, slots: dict, flags: dict, today: str) -> dict:
    if re.search(r"库里|有哪些|哪些鱼", text):
        g = query_species_graph(question=text)
        if g.ok:
            names = "、".join(f"{zh_name(s)}（{s}）" for s in g.data["answer_set"]) or "没有符合条件的鱼"
            return R("answer", f"符合条件的库内鱼种：{names}。[来源：知识图谱]", answer_set=g.data["answer_set"],
                     conditions=g.data["conditions"])
    species = slots.get("species_all") or ([slots["species"]] if slots.get("species") else [])
    if not species:
        return R("clarify", "你想了解哪种鱼？告诉我鱼名，或者上传一张照片。")
    bare = text
    for sci in species:                                   # 先去掉鱼名再判断主题（「淡水石首鱼」里的「淡水」不是在问栖息地）
        bare = bare.replace(zh_name(sci), " ").replace(sci, " ")
    topic = _topic(bare)
    if not topic and len(text.strip()) <= len(zh_name(species[0])) + 2:   # 只有鱼名：给简介并引导
        hits = _search("概况", species[0], "overview")
        intro = hits[0]["text"] if hits else ""
        return R("clarify", f"{intro} [{hits[0]['id']}]\n你想了解它的哪方面？食性和饵料、栖息环境、识别特征，还是捕捞规则？"
                 if hits else "你想了解它的哪方面？")
    llm = get_llm("agent")
    if llm:                                                           # 在线：ReAct（模型自己决定检索）
        return _react(llm, text, species)
    parts, cites = [], []
    for sci in species[:2]:
        hits = _search(text, sci, topic) or _search(text, sci)
        for h in hits[:1 if topic else 2]:
            parts.append(f"{h['text']} [{h['id']}]")
            cites.append(h["id"])
    if not parts:
        return R("answer", "知识库里没有找到相关资料，这个问题我暂时回答不了。")
    return R("answer", "\n".join(parts), citations=cites)


def _react(llm, text, species):                                       # pragma: no cover - 需要 API key
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    tools = {"search_species_kb": search_species_kb, "query_species_graph": query_species_graph}
    from app.tools.registry import lc_tools
    bound = llm.bind_tools(lc_tools(list(tools)))
    msgs = [SystemMessage(load("knowledge")["system"]), HumanMessage(f"{text}\n（已识别鱼种：{species}）")]
    for _ in range(4):                                                # 子 Agent 步数上限
        ai = bound.invoke(msgs)
        record_usage(ai)
        msgs.append(ai)
        if not ai.tool_calls:
            return R("answer", ai.content)
        for tc in ai.tool_calls:
            res = tools[tc["name"]](**tc["args"])
            data = res.data
            if tc["name"] == "search_species_kb" and res.ok:
                data, _ = filter_hits(res.data)
            msgs.append(ToolMessage(content=f"<retrieved>{data or res.error}</retrieved>", tool_call_id=tc["id"]))
    return R("partial_answer", "检索次数已达上限，以下是目前能确定的信息：" + str(msgs[-1].content)[:500])
