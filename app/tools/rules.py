"""规则工具：查自建的示例规则表（SQLite 也可以，这里直接读 CSV）。结果是确定性的，不经过 LLM。"""
from __future__ import annotations

import csv
from functools import lru_cache

from app import config
from app.tools.base import ToolResult, ctx, tool_wrapper
from app.tools.normalize import find_species

DISCLAIMER = "示例规则，请以当地法规为准"


@lru_cache(maxsize=1)
def load_rules() -> dict[str, dict]:
    with open(config.RULES_CSV, encoding="utf-8") as f:
        return {r["scientific_name"]: r for r in csv.DictReader(f)}


def closed_on(date: str, season: str) -> bool:
    if not season:
        return False
    a, b = season.split("~")
    md = date[5:]
    return a <= md <= b if a <= b else (md >= a or md <= b)


def rule_facts(sci: str, date: str | None = None) -> dict:
    r = load_rules()[sci]
    f = {"scientific_name": sci, "catch_policy": r["catch_policy"],
         "min_size_cm": int(r["min_size_cm"]) if r["min_size_cm"] else None,
         "daily_limit": int(r["daily_limit"]) if r["daily_limit"] else None,
         "closed_season": r["closed_season"] or None, "note": r["note"] or None, "disclaimer": DISCLAIMER}
    if date:
        f["date"] = date
        f["closed_on_date"] = closed_on(date, r["closed_season"])
        f["can_keep_on_date"] = r["catch_policy"] == "harvest" and not f["closed_on_date"]
    return f


def size_check(sci: str, length_cm: float) -> str:
    f = rule_facts(sci)
    if f["catch_policy"] in ("release_only", "invasive_kill"):
        return f["catch_policy"]
    if f["min_size_cm"] is None:
        return "no_min_size"
    return "keep_ok" if length_cm >= f["min_size_cm"] else "too_small"


@tool_wrapper("get_species_rules")
def get_species_rules(scientific_name: str, date: str | None = None, length_cm: float | None = None) -> ToolResult:
    """查询鱼种的示例捕捞规则：catch_policy、最小尺寸、每日数量、禁捕期；给 date 时判断当天能否带走；给 length_cm 时判断尺寸是否达标。"""
    if scientific_name == "*":                         # 列出某天处于禁捕期的全部鱼种
        d = date or ctx().today
        return ToolResult(True, [s for s, r in load_rules().items() if closed_on(d, r["closed_season"])], source="rules.csv")
    sci = scientific_name if scientific_name in load_rules() else find_species(scientific_name)["sci"]
    if not sci or sci not in load_rules():
        return ToolResult(False, error=f"【规则查询】规则表未收录「{scientific_name}」，无法提供规则；请勿编造。",
                          source="not_found")
    f = rule_facts(sci, date or ctx().today)
    if length_cm is not None:
        f["length_cm"], f["size_check"] = length_cm, size_check(sci, length_cm)
    return ToolResult(True, f, source="rules.csv")
