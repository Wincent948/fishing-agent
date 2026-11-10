"""Router：三级意图识别 + 槽位抽取 + 缺口分析。

① 规则（关键词 / 正则，0 成本，可审计）
② 语义路由（问句与意图示例句的向量相似度 + 阈值）——规则判不出时用
③ LLM 结构化输出（在线模式）——前两级都没把握时用；离线模式下跳过

多轮：槽位跨轮保留；「它 / 那…呢 / 回到刚才」继承上一轮意图；上一轮在追问时，本轮优先补槽。
"""
from __future__ import annotations

import re
from functools import lru_cache

import numpy as np
import yaml

from app import config
from app.guards.output import FAB_PRESSURE
from app.kb.text import HashEmbedder
from app.tools.normalize import find_coords, find_count, find_date, find_length_cm, find_location, find_species

K = {
    "log": re.compile(r"记一下|查.{0,3}记录|钓鱼记录|渔获记录|记录[：:]|帮我记|记一笔|记下来|保存.{0,4}钓获|log my catch|直接写入|直接存|记录.{0,15}条|记：|钓获|渔获|钓鱼记录|钓的鱼叫什么|长期记忆|forget_memory", re.I),
    "oos_refuse": re.compile(r"代码|python|爬虫|股票|餐厅|1\s*\+\s*1|笑话|翻译|新闻", re.I),
    "oos_answer": re.compile(r"我在哪|你是谁"),
    "cond": re.compile(r"天气|潮|风大|风小|下雨|会下|气温|几点|哪天|哪边|条件|最好|best time|weather|tide|适合钓鱼", re.I),
    "rules": re.compile(r"每天能.{0,3}\d+\s*条|尺寸|多长才|最小|几条|能留|留吗|能带|带走|带回|禁捕|禁渔|放回|放生|放流|限制|limit|size|达标|数量|能留着", re.I),
    "info": re.compile(r"介绍|吃什么|食性|习性|怎么钓|区分|用什么饵|饵|生活在|水层|水域|最大|最长|最重|体长|max length|哪个科|属于|科的|"
                       r"淡水还是|海水|半咸水|咸水|底层|中上层|能吃吗|活跃|长什么样", re.I),
    "graph": re.compile(r"库里|有哪些|哪些鱼"),
    "plan": re.compile(r"几点去|帮我规划|帮我安排|计划|想去.{0,20}钓|规划"),
    "followup": re.compile(r"^(那|不对|回到|它|那它|那么)|呢[？?]?$|刚才"),
    "profile": re.compile(r"(我一般|我常|我平时|我经常).{0,6}在\s*(.+?)\s*钓鱼"),
    "home": re.compile(r"老地方|常去的地方|我常去"),
}


@lru_cache(maxsize=1)
def _intent_bank():
    ex = yaml.safe_load((config.DATA / "intent_examples.yaml").read_text(encoding="utf-8"))
    emb = HashEmbedder()
    labels, texts = zip(*[(k, t) for k, v in ex.items() for t in v])
    return list(labels), emb.embed(list(texts)), emb


def semantic_route(text: str, thr: float = 0.45) -> tuple[str | None, float]:
    labels, M, emb = _intent_bank()
    sims = M @ emb.embed([text])[0]
    i = int(np.argmax(sims))
    return (labels[i], float(sims[i])) if sims[i] >= thr else (None, float(sims[i]))


def find_locations(text: str) -> list[str]:
    out, t = [], text
    while (loc := find_location(t)) and loc not in out:
        out.append(loc)
        from app.tools.normalize import _locations
        for r in _locations():
            if r["location"] == loc:
                t = re.sub(re.escape(r["alias"]), " ", t, flags=re.I)
    return out


def route(text: str, image: str | None, prev: dict, profile: dict, today: str) -> dict:
    """返回 {intents, slots, missing, risk, flags, source}。prev = 上一轮留下的 {slots, last_intents, pending}。"""
    t = text.strip()
    sp = find_species(t)
    locs = find_locations(t)
    date = find_date(t, today)
    slots = {
        "species": sp["sci"], "species_all": sp["all"], "ambiguous": sp["ambiguous"],
        "location": locs[0] if locs else None, "locations": locs, "date": date["date"], "date_kind": date["kind"],
        "length_cm": find_length_cm(t), "count": find_count(t), "coords": find_coords(t), "image": image,
    }
    flags = {"fab_pressure": bool(FAB_PRESSURE.search(t)), "refuse_fabricate": bool(re.search(r"编一个|编造|伪造", t)), "english": bool(re.fullmatch(r"[\x00-\x7f]+", t)) or "in English" in t}
    if m := K["profile"].search(t):
        flags["profile_update"] = {"home_spot": find_location(m.group(2)) or m.group(2)}
    if K["home"].search(t) and profile.get("home_spot"):
        slots["location"] = slots["location"] or profile["home_spot"]

    intents, source = [], "rules"
    if image:
        intents = ["identify_fish"] + (["rules"] if re.search(r"能留|能带|留吗", t) else [])
    elif K["log"].search(t):
        intents = ["log_catch"]
    elif K["oos_refuse"].search(t) or K["oos_answer"].search(t):
        intents = ["out_of_scope"]
    elif flags.get("profile_update"):
        intents = ["out_of_scope"]
    else:
        is_cond, is_rules, is_info = bool(K["cond"].search(t)), bool(K["rules"].search(t)), bool(K["info"].search(t))
        if K["graph"].search(t) and (is_info or "科" in t or "kg" in t.lower()) and not ("禁捕" in t and "科" not in t and not is_info):
            intents = ["species_info"]
        elif is_cond and is_rules and slots["species"] and K["plan"].search(t):
            intents = ["trip_plan"]
        else:
            if is_rules and (slots["species"] or slots["ambiguous"] or not is_info):
                intents.append("rules")
            if is_cond:
                intents.append("conditions")
            if is_info and "rules" not in intents:
                intents.append("species_info")
            if not intents and K["plan"].search(t) and slots["species"]:
                intents = ["trip_plan"]

    # 多轮：本轮没识别出意图，或是明显的追问 / 纠正 → 继承上一轮意图
    pending = prev.get("pending") or {}
    if pending.get("missing") and not intents:
        intents, source = pending["intents"], "pending"
        flags["resume_text"] = pending.get("text", "")
    elif (not intents or K["followup"].search(t)) and prev.get("last_intents") and len(t) <= 30:
        if not intents or intents == ["species_info"] and prev["last_intents"] != ["species_info"] and not K["info"].search(t):
            intents, source = prev["last_intents"], "inherit"
    if not intents:
        if slots["species"]:
            intents = ["species_info"]
        elif len(re.sub(r"\s", "", t)) <= 3:
            intents, source = ["out_of_scope"], "too_short"
        else:
            label, score = semantic_route(t)
            intents, source = ([label], f"semantic:{score:.2f}") if label else (["out_of_scope"], "fallback")

    # 槽位继承：这一轮没给的，沿用之前的
    for k in ("species", "location", "date"):
        if not slots[k] and prev.get("slots", {}).get(k) and not (k == "date" and slots["date_kind"]):
            if k == "species" and slots["ambiguous"]:
                continue
            slots[k] = prev["slots"][k]
    if not slots["date"] and slots["date_kind"] is None:
        slots["date"], slots["date_kind"] = today, "default"

    missing = []
    if any(i in intents for i in ("conditions", "trip_plan")) and not slots["location"] and not slots["coords"]:
        missing.append("location")
    if "identify_fish" in intents and not image:
        missing.append("image")
    return {"intents": intents, "slots": slots, "missing": missing, "flags": flags, "source": source,
            "risk": "medium" if flags["fab_pressure"] else "low"}
