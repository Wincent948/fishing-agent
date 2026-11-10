"""实体规范化：鱼种（同义词 + 拼写纠错）、地点、日期、长度单位。工具在执行前先过这里（入参兜底）。"""
from __future__ import annotations

import csv
import datetime as dt
import difflib
import re
from functools import lru_cache

from app import config


@lru_cache(maxsize=1)
def _synonyms() -> list[dict]:
    with open(config.DATA / "synonyms.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=1)
def _locations() -> list[dict]:
    with open(config.DATA / "locations.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=1)
def sci_names() -> list[str]:
    return sorted({r["sci"] for r in _synonyms() if r["sci"] != "AMBIGUOUS"})


def find_species(text: str) -> dict:
    """在一句话里找鱼种。返回 {"sci": 学名|None, "ambiguous": [学名...], "all": [学名...]}。
    规则：最长别名优先；中文别名精确匹配；学名允许 1–2 个字母的拼写错误。"""
    t = text.lower()
    hits = []
    for r in sorted(_synonyms(), key=lambda r: -len(r["alias"])):
        a = r["alias"].lower()
        if a and a in t and not any(a in h[0] for h in hits):
            hits.append((a, r))
    found, amb = [], []
    for a, r in hits:
        if r["sci"] == "AMBIGUOUS":
            if not found:
                ids = r["species_id"].split("|")
                amb = [s["sci"] for s in _synonyms() if s["species_id"] in ids and s["sci"] != "AMBIGUOUS"]
                amb = list(dict.fromkeys(amb))
        elif r["sci"] not in found:
            found.append(r["sci"])
    if not found:                                         # 学名拼写纠错：「Genus speciex」
        for m in re.finditer(r"\b([A-Z][a-z]+ [a-z]{3,})\b", text):
            best = difflib.get_close_matches(m.group(1), sci_names(), n=1, cutoff=0.88)
            if best:
                found.append(best[0])
    if found:
        amb = []
    return {"sci": found[0] if found else None, "ambiguous": amb, "all": found}


def find_location(text: str) -> str | None:
    t = text.lower()
    for r in sorted(_locations(), key=lambda r: -len(r["alias"])):
        if r["alias"].lower() in t:
            return r["location"]
    return None


def find_coords(text: str) -> tuple[float, float] | None:
    m = re.search(r"坐标\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)", text)
    return (float(m.group(1)), float(m.group(2))) if m else None


def find_date(text: str, today: str) -> dict:
    """返回 {"date": YYYY-MM-DD|None, "kind": "ok"|"past"|"too_far"|"month"|None}。"""
    d0 = dt.date.fromisoformat(today)
    rel = {"今天": 0, "今日": 0, "today": 0, "明天": 1, "明日": 1, "tomorrow": 1, "后天": 2}
    for w, k in rel.items():
        if w in text.lower():
            return {"date": str(d0 + dt.timedelta(days=k)), "kind": "ok"}
    if re.search(r"上周|昨天|前天|上个月|去年", text):
        return {"date": None, "kind": "past"}
    if "下个月" in text:
        m = d0.month % 12 + 1
        return {"date": f"{d0.year + (d0.month == 12)}-{m:02d}-15", "kind": "month"}
    if m := re.search(r"(\d{4}-\d{2}-\d{2})", text):
        d = dt.date.fromisoformat(m.group(1))
    elif m := re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text):
        d = dt.date(d0.year, int(m.group(1)), int(m.group(2)))
    else:
        return {"date": None, "kind": None}
    delta = (d - d0).days
    kind = "past" if delta < 0 else "too_far" if delta > 6 else "ok"
    return {"date": str(d), "kind": kind}


def find_length_cm(text: str) -> float | None:
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(?:英寸|inch|in\b)", text):
        return round(float(m.group(1)) * 2.54 * 2) / 2              # 换算到 0.5 cm
    if m := re.search(r"(-?\d+(?:\.\d+)?)\s*(?:cm|厘米|公分)", text, re.I):
        return float(m.group(1))
    return None


def find_count(text: str) -> int | None:
    zh = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if m := re.search(r"(-?\d+)\s*条", text):
        return int(m.group(1))
    if m := re.search(r"([一两二三四五六七八九十])\s*条", text):
        return zh[m.group(1)]
    return None
