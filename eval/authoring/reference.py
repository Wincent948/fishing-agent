"""评测的参考实现：规则查询与最佳出钓时段计算。

金标答案由这里的确定性规则从 rules.csv 和 fixtures/conditions.json 算出。
系统里的 get_species_rules 和 best_window 工具，必须与这里的口径完全一致。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]

NO_GO_WIND_KMH = 30      # 风速 >= 30 km/h 不建议出钓
NO_GO_PRECIP_MM = 10     # 降水 >= 10 mm 不建议出钓
TIDE_HALF_WINDOW_MIN = 60  # 高潮前后各 60 分钟
INLAND_WINDOW_MIN = 120    # 内陆：日出后 / 日落前 120 分钟

DISCLAIMER = "示例规则"   # 回答规则类问题时必须出现的字样


def load_species() -> dict[str, dict]:
    with open(EVAL_DIR / "species.csv", encoding="utf-8") as f:
        return {r["species_id"]: r for r in csv.DictReader(f)}


def load_rules() -> dict[str, dict]:
    with open(EVAL_DIR / "rules.csv", encoding="utf-8") as f:
        return {r["species_id"]: r for r in csv.DictReader(f)}


def load_conditions() -> dict:
    return json.loads((EVAL_DIR / "fixtures" / "conditions.json").read_text(encoding="utf-8"))


# ---------- 规则 ----------

def in_closed_season(date: str, season: str) -> bool:
    """date: YYYY-MM-DD；season: 'MM-DD~MM-DD'，允许跨年（如 10-01~03-31）。"""
    if not season:
        return False
    start, end = season.split("~")
    md = date[5:]
    if start <= end:
        return start <= md <= end
    return md >= start or md <= end


def rule_facts(species_id: str, date: str | None = None) -> dict:
    """返回某鱼种的规则金标。date 给定时同时判断是否在禁捕期。"""
    r = load_rules()[species_id]
    facts = {
        "catch_policy": r["catch_policy"],
        "min_size_cm": int(r["min_size_cm"]) if r["min_size_cm"] else None,
        "daily_limit": int(r["daily_limit"]) if r["daily_limit"] else None,
        "closed_season": r["closed_season"] or None,
        "note": r["note"] or None,
    }
    if date:
        facts["closed_on_date"] = in_closed_season(date, r["closed_season"])
        facts["can_keep_on_date"] = (r["catch_policy"] == "harvest") and not facts["closed_on_date"]
    return facts


def size_check(species_id: str, length_cm: float) -> str:
    """'keep_ok' / 'too_small' / 'release_only' / 'invasive_kill' / 'no_min_size'."""
    r = rule_facts(species_id)
    if r["catch_policy"] == "release_only":
        return "release_only"
    if r["catch_policy"] == "invasive_kill":
        return "invasive_kill"
    if r["min_size_cm"] is None:
        return "no_min_size"
    return "keep_ok" if length_cm >= r["min_size_cm"] else "too_small"


# ---------- 出钓时段 ----------

def _m(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _t(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def best_window(loc: str, date: str) -> dict:
    """返回 {'status': 'ok'|'no_go'|'tide_unavailable', 'windows': [[start,end],...], 'reason': str|None}"""
    c = load_conditions()
    L = c["locations"][loc]
    w = c["weather"][loc][date]
    if w["wind_kmh"] >= NO_GO_WIND_KMH or w["precip_mm"] >= NO_GO_PRECIP_MM:
        reasons = []
        if w["wind_kmh"] >= NO_GO_WIND_KMH:
            reasons.append(f"风速 {w['wind_kmh']} km/h")
        if w["precip_mm"] >= NO_GO_PRECIP_MM:
            reasons.append(f"降水 {w['precip_mm']} mm")
        return {"status": "no_go", "windows": [], "reason": "、".join(reasons)}

    sr, ss = _m(L["sunrise"]), _m(L["sunset"])
    if not L["coastal"]:
        return {"status": "ok", "windows": [[_t(sr), _t(sr + INLAND_WINDOW_MIN)],
                                            [_t(ss - INLAND_WINDOW_MIN), _t(ss)]], "reason": None}

    tide = c["tide"][loc][date]
    if tide["status"] != "ok":
        return {"status": "tide_unavailable", "windows": [], "reason": tide.get("error")}
    wins = []
    for e in tide["extremes"]:
        if e["type"] != "high":
            continue
        a = max(_m(e["time"]) - TIDE_HALF_WINDOW_MIN, sr)
        b = min(_m(e["time"]) + TIDE_HALF_WINDOW_MIN, ss)
        if b > a:
            wins.append([_t(a), _t(b)])
    return {"status": "ok", "windows": wins, "reason": None}


def weather_facts(loc: str, date: str) -> dict:
    return load_conditions()["weather"][loc][date]


def tide_facts(loc: str, date: str) -> dict:
    c = load_conditions()
    if not c["locations"][loc]["coastal"]:
        return {"status": "not_applicable", "reason": "inland"}
    return c["tide"][loc][date]
