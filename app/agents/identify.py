"""Identify 子 Agent：识鱼 + 低置信度时追问特征 + 拒识。
三方投票的思路（线性探针 / 近邻 / VLM 复核）在接入 BioCLIP 后启用；离线用 mock 候选。"""
from __future__ import annotations

import csv
from functools import lru_cache

from app import config
from app.graph.workflows import R, describe_rule, zh_name
from app.guards.input import check_image_text
from app.tools.base import ctx
from app.tools.knowledge import identify_fish, image_meta
from app.tools.rules import get_species_rules


@lru_cache(maxsize=1)
def _features():
    from scripts.make_synthetic_data import ID_FEATURES  # 合成数据里的识别要点
    with open(config.DATA / "species_facts.csv", encoding="utf-8") as f:
        return {r["sci"]: ID_FEATURES[r["group"]] for r in csv.DictReader(f)}


def run(text: str, slots: dict, flags: dict, today: str, want_rules: bool) -> dict:
    meta = image_meta(slots.get("image")) or {}
    if check_image_text(meta.get("overlay_text", "")):            # 图里写着指令：记录护栏命中，只识鱼
        ctx().guard_hits.append({"guard": "input", "reason": "image_text_injection"})
    res = identify_fish(image=slots["image"])
    if not res.ok:
        return R("answer", res.error)
    v, cands = res.data["verdict"], res.data["candidates"]
    if v == "not_fish":
        return R("answer", "这张图里没有识别到鱼（不是鱼的照片）。请上传清晰的鱼的照片。")
    if v == "not_in_library":
        return R("answer", "这条鱼不在我的鱼种库里，无法确定是什么鱼（不确定）。可以换个角度再拍一张，或告诉我更多特征。")
    top = cands[0]["sci"]
    if v == "uncertain":
        opts = "、".join(f"{zh_name(c['sci'])}（{c['sci']}，{c['p']:.0%}）" for c in cands)
        feat = "；".join(f"{zh_name(c['sci'])}：{_features().get(c['sci'], '')}" for c in cands[:2])
        return R("clarify", f"不太确定，可能是：{opts}。能再看一下这些特征吗？{feat}。")
    ans = f"这是{zh_name(top)}（{top}），置信度 {cands[0]['p']:.0%}。"
    if want_rules:
        r = get_species_rules(scientific_name=top, date=today)
        if r.ok:
            ans += "\n" + describe_rule(r.data, text)
            return {**R("answer", ans, species=top, rules=r.data), "rules_used": True}
    return R("answer", ans, species=top)
