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
        out = _react(llm, text, species, topic)
        if out:
            return out                                                # 失败时返回 None → 降级到下面的离线实现
    parts, cites = [], []
    for sci in species[:2]:
        hits = _search(text, sci, topic) or _search(text, sci)
        for h in hits[:1 if topic else 2]:
            parts.append(f"{h['text']} [{h['id']}]")
            cites.append(h["id"])
    if not parts:
        return R("answer", "知识库里没有找到相关资料，这个问题我暂时回答不了。")
    return R("answer", "\n".join(parts), citations=cites)


def _react(llm, text, species, topic=None, max_rounds: int = 3):
    """ReAct 循环：模型决定是否检索、查什么；最多 max_rounds 轮工具调用。
    防自旋：工具被预算拦截或轮数用完 → 强制「不带工具」再问一次，让模型基于已有资料作答。
    先检索再推理（retrieve-first）：进入循环前先按鱼种 + 主题检索一次，把资料放进上下文，
    保证回答有依据（不让模型凭参数记忆作答），模型觉得不够再自己追加检索。
    任何异常（网络、格式、超时）→ 记录到 trace 并返回 None，由调用方降级为离线实现。"""
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from app.tools.registry import lc_tools
    tools = {"search_species_kb": search_species_kb, "query_species_graph": query_species_graph}
    trace = ctx().trace
    try:
        bound = llm.bind_tools(lc_tools(list(tools)))
        seed = []
        for sci in species[:2]:
            seed += (_search(text, sci, topic) or _search(text, sci))[:3]
        cites = [h["id"] for h in seed]
        ctx_block = "\n".join(f"[{h['id']}] {h['text']}" for h in seed) or "（未检索到资料）"
        msgs = [SystemMessage(load("knowledge")["system"]),
                HumanMessage(f"{text}\n（已识别鱼种：{species}）\n已检索到的资料：\n<retrieved>\n{ctx_block}\n</retrieved>\n"
                             "资料足够就直接回答并标注 [资料id]；不够再调用工具。")]
        for rnd in range(max_rounds):
            ai = bound.invoke(msgs)
            record_usage(ai)
            msgs.append(ai)
            if not ai.tool_calls:
                if (ai.content or "").strip():
                    trace.append({"node": "knowledge_react", "rounds": rnd, "stop": "answered"})
                    return R("answer", ai.content, citations=cites)
                break                                                 # 空回答 → 走强制作答
            blocked = False
            for tc in ai.tool_calls:
                fn = tools.get(tc["name"])
                if fn is None:                                        # 模型编造了不存在的工具
                    msgs.append(ToolMessage(content="不存在该工具", tool_call_id=tc["id"]))
                    continue
                res = fn(**tc["args"])
                blocked |= res.source == "budget"
                data = res.data
                if tc["name"] == "search_species_kb" and res.ok:
                    data, _ = filter_hits(res.data)
                    cites += [h["id"] for h in data]
                msgs.append(ToolMessage(content=f"<retrieved>{data or res.error}</retrieved>", tool_call_id=tc["id"]))
            if blocked:
                break
        # 强制收尾：不再给工具，只能基于已检索内容回答
        msgs.append(HumanMessage("请只根据上面 <retrieved> 里的资料直接回答用户问题，不要再调用工具；资料里没有的就说查不到。"))
        ai = llm.invoke(msgs)
        record_usage(ai)
        trace.append({"node": "knowledge_react", "rounds": max_rounds, "stop": "forced_answer"})
        if (ai.content or "").strip():
            return R("answer", ai.content, citations=cites)
    except Exception as e:                                            # noqa: BLE001
        trace.append({"node": "knowledge_react", "error": f"{type(e).__name__}: {e}"[:200]})
    return None
