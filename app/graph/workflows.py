"""固定 Workflow：步骤写死，不让模型决定路径（规则查询、钓况查询、渔获记录、超范围）。

每个处理函数返回 Result：{behavior, answer, facts, rules_used}。
"""
from __future__ import annotations

import re

from app.tools import conditions as C
from app.tools.knowledge import forget_memory, list_catches, log_catch
from app.tools.rules import DISCLAIMER, get_species_rules, load_rules

ZH = {}


def zh_name(sci: str) -> str:
    if not ZH:
        import csv
        from app import config
        with open(config.DATA / "species_facts.csv", encoding="utf-8") as f:
            ZH.update({r["sci"]: r["zh"] for r in csv.DictReader(f)})
    return ZH.get(sci, sci)


def R(behavior, answer, **facts):
    return {"behavior": behavior, "answer": answer, "facts": facts}


DATE_WORD = {0: "今天", 1: "明天", 2: "后天"}


def date_word(date: str, today: str) -> str:
    import datetime as dt
    d = (dt.date.fromisoformat(date) - dt.date.fromisoformat(today)).days
    return DATE_WORD.get(d, date)


# ======================================================================= 规则
POLICY_ZH = {"release_only": "仅限钓获后放流，不能带走", "invasive_kill": "入侵物种：不得放回，也不得活体携带，钓获后须就地处理"}


def describe_rule(f: dict, text: str, english: bool = False) -> str:
    name = zh_name(f["scientific_name"])
    if english:
        parts = [f"{f['scientific_name']}:"]
        parts.append(f"minimum size {f['min_size_cm']} cm" if f["min_size_cm"] else "no minimum size")
        parts.append(f"daily limit {f['daily_limit']}" if f["daily_limit"] else "no daily limit")
        if f["closed_season"]:
            parts.append(f"closed season {f['closed_season']}")
        return ", ".join(parts) + ". (示例规则 / example rules — check local regulations)"
    p = [f"{name}（{f['scientific_name']}）"]
    if f["catch_policy"] in POLICY_ZH:
        p.append(POLICY_ZH[f["catch_policy"]])
    else:
        p.append(f"最小可留尺寸 {f['min_size_cm']} cm" if f["min_size_cm"] else "没有最小尺寸限制")
        p.append(f"每天最多留 {f['daily_limit']} 条" if f["daily_limit"] else "不限数量")
        if f["closed_season"]:
            a, b = f["closed_season"].split("~")
            p.append(f"禁捕期 {int(a[:2])}月{int(a[3:])}日 至 {int(b[:2])}月{int(b[3:])}日（{f['closed_season']}）")
    if f.get("note"):
        p.append(f["note"])
    if f.get("closed_on_date"):
        p.append(f"{f['date']} 正处于禁捕期，不能带走，钓获请放回")
    if "size_check" in f:
        sc, L = f["size_check"], f["length_cm"]
        p.append({"keep_ok": f"{L:g} cm 达标，可以留", "too_small": f"{L:g} cm 不达标（不足 {f['min_size_cm']} cm），不能留，需放回",
                  "no_min_size": f"{L:g} cm 没有尺寸限制，可以留"}.get(sc, ""))
    return "；".join(x for x in p if x) + f"。（{DISCLAIMER}）"


def rules_wf(text: str, slots: dict, flags: dict, today: str) -> dict:
    if slots.get("ambiguous") and not slots.get("species"):
        opts = "、".join(f"{zh_name(s)}（{s}）" for s in slots["ambiguous"])
        return R("clarify", f"「{text[:6]}」可能指好几种鱼：{opts}。你问的是哪一种？")
    if "下个月" in text or (slots.get("date_kind") == "month"):
        res = get_species_rules(scientific_name="*", date=slots["date"])
        names = [zh_name(s) for s in res.data] if res.ok else []
        return R("answer", f"{slots['date'][:7]} 处于禁捕期的鱼：{'、'.join(names) or '无'}。（{DISCLAIMER}）", closed=res.data)
    species = slots.get("species_all") or ([slots["species"]] if slots.get("species") else [])
    if not species:
        m = re.match(r"^(.+?)(的|每天|一天|最小|有没有|有|能|可以)", text.strip())
        if not m:
            return R("clarify", "你想查哪种鱼的规则？")
        res = get_species_rules(scientific_name=m.group(1).strip())
        return R("answer", f"规则表里没有收录「{m.group(1).strip()}」，无法提供它的尺寸或数量规则，请以当地法规为准。")
    date = slots["date"] if slots.get("date_kind") in ("ok", "past", "month") or re.search(r"今天|现在|\d+ ?月", text) else None
    outs, facts = [], {}
    for s in species[:2]:
        res = get_species_rules(scientific_name=s, date=date or today, length_cm=slots.get("length_cm"))
        if not res.ok:
            outs.append(res.error)
            continue
        f = dict(res.data)
        if not date:
            f.pop("closed_on_date", None)
        facts[s] = f
        outs.append(describe_rule(f, text, flags.get("english")))
    if len(facts) == 2 and re.search(r"哪个|多", text):
        a, b = facts.values()
        win = max(facts.values(), key=lambda x: x["daily_limit"] or 10**6)
        outs.append(f"相比之下，{zh_name(win['scientific_name'])}能留的数量更多。")
    if slots.get("count") and len(facts) == 1 and re.search(r"钓了|钓到", text):
        f = next(iter(facts.values()))
        if f["daily_limit"] and slots["count"] > f["daily_limit"]:
            outs.append(f"你钓了 {slots['count']} 条，但每天最多只能留 {f['daily_limit']} 条，多出的要放回。")
        elif f.get("size_check") == "keep_ok" and f["daily_limit"]:
            outs.append(f"{slots['count']} 条都能带走（每天上限 {f['daily_limit']} 条）。")
    return R("answer", "\n".join(outs), rules=facts)


# ======================================================================= 钓况
def _weather_line(w: dict) -> str:
    return (f"{w['condition']}，{w['temp_min_c']}–{w['temp_max_c']}°C，风速 {w['wind_kmh']} km/h（{w['wind_dir']}），"
            f"降水 {w['precip_mm']} mm，气压{w['pressure_trend']}")


def conditions_wf(text: str, slots: dict, flags: dict, today: str) -> dict:
    if slots.get("coords"):
        lat, lon = slots["coords"]
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return R("clarify", f"坐标 {lat}, {lon} 不合法（纬度应在 -90 到 90 之间），请检查后重新提供地点或坐标。")
    kind = slots.get("date_kind")
    if kind == "past":
        return R("answer", "不支持查询历史日期的天气或潮汐，只能查今天起 7 天内的预报。")
    if kind == "too_far":
        return R("answer", f"{slots['date']} 超出了预报范围：只支持今天起 7 天内的天气和潮汐。")
    if flags.get("refuse_fabricate"):
        return R("refuse", "不能编造潮汐表；我只提供来自数据源的真实预报。")
    locs, date = slots.get("locations") or [slots["location"]], slots["date"]
    wants_tide = bool(re.search(r"潮", text)) and not re.search(r"几点.{0,4}(钓|去|最好)|最好", text)
    compare_days = bool(re.search(r"哪天", text))
    compare_locs = len(locs) >= 2

    if compare_locs:
        rows = {}
        for loc in locs[:2]:
            w, bw = C.get_weather(location=loc, date=date), C.best_window(location=loc, date=date)
            rows[loc] = (w.data, bw.data)
        names = {l: C.location_name(l).split(" - ")[1] for l in rows}
        lines = [f"{names[l]}：{_weather_line(w)}；" + ("不建议出钓（" + b["reason"] + "）" if b["status"] == "no_go" else "可以出钓")
                 for l, (w, b) in rows.items()]
        if "风" in text:
            best = min(rows, key=lambda l: rows[l][0]["wind_kmh"])
            return R("answer", "\n".join(lines) + f"\n结论：{names[best]} 风更小。")
        ok = [l for l, (w, b) in rows.items() if b["status"] != "no_go"]
        if not ok:
            return R("answer", "\n".join(lines) + "\n结论：两边都不建议出钓。")
        best = min(ok, key=lambda l: rows[l][0]["wind_kmh"])
        return R("answer", "\n".join(lines) + f"\n结论：{names[best]} 条件更好。")

    loc = locs[0]
    lname = C.location_name(loc)
    if compare_days:
        words = re.findall(r"今天|明天|后天", text)
        from app.tools.normalize import find_date
        days = [find_date(w, today)["date"] for w in dict.fromkeys(words)][:2]
        rows = {d: (C.get_weather(location=loc, date=d).data, C.best_window(location=loc, date=d).data) for d in days}
        ok = [d for d in days if rows[d][1]["status"] != "no_go"]
        best = min(ok, key=lambda d: rows[d][0]["wind_kmh"]) if ok else None
        lines = [f"{date_word(d, today)}：{_weather_line(w)}；" + ("不建议出钓" if b["status"] == "no_go" else "可以出钓")
                 for d, (w, b) in rows.items()]
        return R("answer", "\n".join(lines) + (f"\n结论：{date_word(best, today)}更适合。" if best else "\n结论：两天都不建议。"))

    if wants_tide:
        if not C.is_coastal(loc):
            return R("answer", f"{lname} 是内陆水域，没有潮汐，不受潮汐影响。")
        t = C.get_tide(location=loc, date=date)
        if not t.ok:
            return R("partial_answer", f"{lname} {date} 的潮汐数据暂不可用（获取失败），无法给出潮汐时间。")
        ext = "；".join(f"{'高潮' if e['type'] == 'high' else '低潮'} {e['time']}（{e['height_m']} m）" for e in t.data["extremes"])
        return R("answer", f"{lname} {date} 潮汐：{ext}。潮高以平均海平面为基准，近岸精度有限。", tide=t.data)

    w = C.get_weather(location=loc, date=date)
    if not w.ok:
        return R("partial_answer", w.error)
    wants_window = bool(re.search(r"几点|最好|最佳|best time|适合", text, re.I))
    ans = f"{lname} {date}：{_weather_line(w.data)}。"
    if not wants_window:
        return R("answer", ans, weather=w.data)
    if C.is_coastal(loc):
        C.get_tide(location=loc, date=date)
    bw = C.best_window(location=loc, date=date).data
    if bw["status"] == "no_go":
        return R("answer", ans + f"\n不建议出钓：{bw['reason']}，不适合安全垂钓。", weather=w.data, best_window=bw)
    if bw["status"] == "tide_unavailable":
        return R("partial_answer", ans + "\n潮汐数据暂不可用（获取失败），无法计算潮汐相关的最佳时段。", best_window=bw)
    wins = "、".join(f"{a}–{b}" for a, b in bw["windows"])
    basis = "高潮前后 1 小时（白天）" if C.is_coastal(loc) else "日出后与日落前各 2 小时"
    return R("answer", ans + f"\n最佳时段：{wins}（依据：{basis}）。", weather=w.data, best_window=bw)


# ======================================================================= 渔获（写入需确认）
def catch_prepare(text: str, slots: dict, flags: dict, today: str) -> dict:
    """返回需要确认的记录草稿，或直接给出读取 / 拒绝结果。真正写入在用户确认之后。"""
    if re.search(r"删除.{0,6}(所有|全部).{0,4}(钓鱼)?记录", text):
        return R("refuse", "目前不支持批量删除渔获记录。")
    if re.search(r"长期记忆|forget_memory|忘掉|删除.{0,4}记忆", text):
        return {"behavior": "confirm_required", "answer": "确认要删除你的长期记忆（常去地点等偏好）吗？回复「确认」或「取消」。",
                "draft": {"op": "forget"}}
    if re.search(r"上次|查看|我的.{0,4}记录|钓的鱼叫什么", text) and not re.search(r"\d|[一两三四五]条", text):
        rows = list_catches(limit=3).data
        if not rows:
            return R("answer", "你还没有任何渔获记录。")
        last = rows[0]
        return R("answer", f"你最近一次记录：{last['date']} 在 {last['location'] or '未知地点'} 钓到 {last['count']} 条"
                           f"{zh_name(last['species'])}。", catches=rows)
    sci, count, L = slots.get("species"), slots.get("count"), slots.get("length_cm")
    if count is not None and count <= 0:
        return R("clarify", f"数量是 {count}，应该是正整数。这次钓到了几条？")
    if not sci:
        return R("clarify", "钓到的是哪种鱼？告诉我鱼名（或上传照片），我再帮你记录。")
    count = count or 1
    rem = ""
    r = get_species_rules(scientific_name=sci, date=slots.get("date") or today, length_cm=L)
    if r.ok:
        f = r.data
        if f["catch_policy"] == "invasive_kill":
            rem = "提醒：这是入侵物种，不得放回，也不得活体携带。"
        elif f["catch_policy"] == "release_only":
            rem = "提醒：这种鱼仅限放流，记录后请放回。"
        elif f.get("size_check") == "too_small":
            rem = f"提醒：{L:g} cm 低于示例规则最小尺寸 {f['min_size_cm']} cm，不足尺寸，请放回。"
        elif f.get("closed_on_date"):
            rem = "提醒：今天处于禁捕期，请放回。"
    draft = {"op": "log", "species": sci, "count": count, "length_cm": L, "location": slots.get("location"),
             "date": slots.get("date") or today}
    msg = (f"请确认要记录：{draft['date']}，{zh_name(sci)} {count} 条" + (f"，{L:g} cm" if L else "")
           + (f"，地点 {draft['location']}" if draft["location"] else "") + f"。{rem}（{DISCLAIMER}）\n回复「确认」写入，或「取消」。")
    return {"behavior": "confirm_required", "answer": msg, "draft": draft, "rules_used": True}


def catch_commit(draft: dict, decision, thread_id: str) -> dict:
    ok = decision is True or (isinstance(decision, dict) and decision.get("confirm")) or \
        (isinstance(decision, str) and re.search(r"确认|是|好|yes|ok", decision, re.I) and not re.search(r"取消|不", decision))
    if not ok:
        return R("answer", "已取消，不记录。")
    if draft["op"] == "forget":
        forget_memory()
        return R("answer", "已删除你的长期记忆。")
    log_catch(species=draft["species"], count=draft["count"], length_cm=draft["length_cm"], location=draft["location"],
              date=draft["date"], idem_key=f"{thread_id}-{draft['species']}-{draft['count']}-{draft['date']}")
    return R("answer", f"已记录：{zh_name(draft['species'])} {draft['count']} 条。")


# ======================================================================= 超范围
def out_of_scope(text: str, slots: dict, flags: dict, today: str) -> dict:
    if flags.get("profile_update"):
        return R("answer", "好的，记住了，之后问「老地方」我就按这个地点查。")
    if re.search(r"我在哪", text):
        return R("answer", "我不会获取或猜测你的位置。你可以告诉我想查询的钓点。")
    if re.search(r"编一个|编造", text):
        return R("refuse", "不能编造潮汐表或其他数据；我只提供来自数据源的信息。")
    t = re.sub(r"\s", "", text)
    if len(t) <= 4 or not re.search(r"[一-鿿A-Za-z]", t):
        return R("clarify", "你想了解什么？我可以识鱼、查鱼种知识、查示例捕捞规则、查天气潮汐和最佳出钓时段。")
    return R("refuse", "这个问题超出了我的范围。我是钓鱼助手：可以识鱼、查鱼类知识、捕捞规则、天气潮汐和出钓时段。")
