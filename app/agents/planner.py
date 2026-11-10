"""Planner 子 Agent：出钓规划（Plan-and-Execute + Supervisor）。

先列计划，再逐步执行；每一步看结果决定下一步（例如天气不宜出钓，就不再计算时段、改为给出替代建议）。
最多 MAX_STEPS 步。离线时计划由规则生成；在线时可让大模型（chat-large）生成计划与汇总。
"""
from __future__ import annotations

from app.graph.workflows import R, _weather_line, describe_rule, zh_name
from app.tools import conditions as C
from app.tools.rules import get_species_rules

MAX_STEPS = 6


def run(text: str, slots: dict, flags: dict, today: str) -> dict:
    sci, loc, date = slots["species"], slots["location"], slots["date"]
    plan = ["rules", "weather"] + (["tide"] if C.is_coastal(loc) else []) + ["window"]
    done, out, facts = [], [], {}
    for step in plan[:MAX_STEPS]:
        if step == "rules":
            r = get_species_rules(scientific_name=sci, date=date)
            facts["rules"] = r.data
            out.append(describe_rule(r.data, text))
        elif step == "weather":
            w = C.get_weather(location=loc, date=date)
            facts["weather"] = w.data
            out.append(f"{C.location_name(loc)} {date}：{_weather_line(w.data)}。")
        elif step == "tide":
            t = C.get_tide(location=loc, date=date)
            facts["tide"] = t.data if t.ok else {"status": "unavailable"}
        elif step == "window":
            bw = C.best_window(location=loc, date=date).data
            facts["best_window"] = bw
            if bw["status"] == "no_go":                      # Supervisor 判断：结果不理想 → 调整建议
                out.append(f"不建议这天出钓（{bw['reason']}），不适合安全垂钓，建议换一天。")
            elif bw["status"] == "tide_unavailable":
                out.append("潮汐数据暂不可用，无法计算最佳时段。")
            else:
                out.append("最佳时段：" + "、".join(f"{a}–{b}" for a, b in bw["windows"]) + "。")
        done.append(step)
    beh = "partial_answer" if facts.get("best_window", {}).get("status") == "tide_unavailable" else "answer"
    head = f"出钓规划：{zh_name(sci)} @ {C.location_name(loc)}，{date}（计划：{' → '.join(done)}）"
    return {**R(beh, head + "\n" + "\n".join(out), **facts), "rules_used": True}
