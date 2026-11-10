"""知识图谱（内存版，接口按 Neo4j 的思路设计）。

节点：Species / Family / Zone / Water / Food / Bait / Trait；关系：BELONGS_TO / LIVES_IN_ZONE / LIVES_IN_WATER / EATS / BAIT / HAS_TRAIT。
查询分两步：①把问题解析成结构化条件（离线用规则；在线可让 LLM 生成 Cypher，经只读白名单校验后执行）
           ②在图上过滤，条件全部满足才命中。
只在 40 种核心鱼（「库里」）范围内回答关系题。
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from functools import lru_cache

from app import config
from app.tools.rules import closed_on, load_rules


class SpeciesGraph:
    def __init__(self):
        self.edges = defaultdict(set)            # (subject, relation) -> {object}
        with open(config.DATA / "triples.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                self.edges[(r["subject"], r["relation"])].add(r["object"])
        with open(config.DATA / "species_facts.csv", encoding="utf-8") as f:
            self.facts = {r["sci"]: r for r in csv.DictReader(f) if r["core"] == "1"}

    def has(self, s, rel, obj):
        return obj in self.edges[(s, rel)]

    def run(self, cond: dict) -> list[str]:
        """cond 示例：{"water_all": ["fresh","marine"], "length_gt": 100, "family": "Centrarchidae", ...}"""
        rules = load_rules()
        out = []
        for s, f in self.facts.items():
            ok = True
            for w in cond.get("water_all", []):
                ok &= self.has(s, "LIVES_IN_WATER", w)
            if "zone" in cond:
                ok &= self.has(s, "LIVES_IN_ZONE", cond["zone"])
            if "family" in cond:
                ok &= self.has(s, "BELONGS_TO", cond["family"])
            if "length_gt" in cond:
                ok &= float(f["Length"]) > cond["length_gt"]
            if "length_lt" in cond:
                ok &= float(f["Length"]) < cond["length_lt"]
            if "weight_gt" in cond:
                ok &= float(f["Weight"]) > cond["weight_gt"]
            if "exclude" in cond:
                ok &= s not in cond["exclude"]
            r = rules.get(s)
            if cond.get("no_closed_season"):
                ok &= bool(r) and not r["closed_season"]
            if cond.get("closed_on"):
                ok &= bool(r) and closed_on(cond["closed_on"], r["closed_season"])
            if ok:
                out.append(s)
        return sorted(out)

    def family_of(self, sci: str) -> str | None:
        return next(iter(self.edges[(sci, "BELONGS_TO")]), None)


FAMILY_WORDS = {"太阳鱼科": "Centrarchidae", "centrarchidae": "Centrarchidae", "鲶科": "Ictaluridae",
                "ictaluridae": "Ictaluridae", "鲑科": "Salmonidae", "salmonidae": "Salmonidae",
                "鲈科": "Percidae", "狗鱼科": "Esocidae", "鲤科": "Cyprinidae"}


def parse_question(q: str, today: str, species_hint: str | None = None) -> dict | None:
    """离线 Text2Query：用规则把关系题解析成条件。解析不了返回 None（交给向量检索或 LLM）。"""
    s = q.lower()
    cond: dict = {}
    if ("淡水" in s and "海水" in s) or ("fresh" in s and "marine" in s):
        cond["water_all"] = ["fresh", "marine"]
    elif "半咸水" in s or "brackish" in s:
        cond["water_all"] = ["brackish"]
    if "底栖" in s or "demersal" in s:
        cond["zone"] = "demersal"
    for w, fam in FAMILY_WORDS.items():
        if w in s:
            cond["family"] = fam
    if m := re.search(r"(?:超过|大于|>)\s*(\d+)\s*(?:cm|厘米)", s):
        cond["length_gt"] = float(m.group(1))
    if m := re.search(r"(?:不到|小于|<)\s*(\d+)\s*(?:cm|厘米)", s):
        cond["length_lt"] = float(m.group(1))
    if m := re.search(r"(?:超过|大于)\s*(\d+)\s*(?:kg|公斤|千克)", s):
        cond["weight_gt"] = float(m.group(1)) * 1000
        cond.pop("length_gt", None)
    if "没有禁捕期" in s or "无禁捕期" in s:
        cond["no_closed_season"] = True
    if "禁捕期" in s and ("今天" in s or "现在" in s):
        cond["closed_on"] = today
    if ("同一个科" in s or "同科" in s) and species_hint:
        fam = G().family_of(species_hint)
        if fam:
            cond["family"], cond["exclude"] = fam, [species_hint]
    return cond or None


@lru_cache(maxsize=1)
def G() -> SpeciesGraph:  # noqa: N802
    return SpeciesGraph()
