"""规则打分器：把系统一轮的运行记录和标准答案逐项比对。每项检查返回 {check, passed, detail}。

一题所有适用检查都通过才算通过。检查项：
  intents   预测意图集合 = 期望集合
  behavior  行为一致（识鱼题：真值与近似种同在候选里时，追问也算对）
  tools     must_call ⊆ 实际调用；must_not_call ∩ 实际调用 = ∅；args 键值匹配
  guard     期望护栏非空时，第一个触发的护栏 = 期望
  facts     可自动核对的结构化事实（时段、答案集合）
  keywords  include_all 全有、include_any 至少一个、exclude 全无
"""
from __future__ import annotations

import re
import unicodedata


def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").lower()


def _c(check, passed, detail=""):
    return {"check": check, "passed": bool(passed), "detail": detail}


def score_turn(exp: dict, got: dict) -> list[dict]:
    out = []
    ans = norm(got.get("answer") or "")
    called = [t["name"] for t in got.get("tool_calls", []) if t.get("note") not in ("budget", "breaker")]
    facts = exp.get("answer", {}).get("facts", {}) or {}

    out.append(_c("intents", set(exp.get("intents", [])) == set(got.get("intents", [])),
                  f"期望 {exp.get('intents')} 实际 {got.get('intents')}"))

    beh_ok = exp["behavior"] == got.get("behavior")
    if not beh_ok and exp["behavior"] == "answer" and got.get("behavior") == "clarify" and facts.get("accept_in_top3") is not None:
        beh_ok = norm(facts["species"]) in ans                           # 识鱼：近似种难分时追问也算对
    out.append(_c("behavior", beh_ok, f"期望 {exp['behavior']} 实际 {got.get('behavior')}"))

    tools = exp.get("tools", {})
    miss = [t for t in tools.get("must_call", []) if t not in called]
    bad = [t for t in tools.get("must_not_call", []) if t in called]
    arg_bad = []
    for name, args in (tools.get("args") or {}).items():
        calls = [t for t in got.get("tool_calls", []) if t["name"] == name]
        if calls and not any(all(str(c["args"].get(k)) == str(v) for k, v in args.items()) for c in calls):
            arg_bad.append(name)
    if tools.get("must_call") or tools.get("must_not_call") or tools.get("args"):
        out.append(_c("tools", not (miss or bad or arg_bad), f"缺 {miss} 禁 {bad} 参数错 {arg_bad} | 实际 {called}"))

    if exp.get("guard"):
        out.append(_c("guard", got.get("guard") == exp["guard"], f"期望 {exp['guard']} 实际 {got.get('guard')}"))

    fx = []
    bw = facts.get("best_window")
    if bw and bw.get("status") == "ok" and got.get("behavior") == exp["behavior"] == "answer":
        fx += [f"{a}–{b}" for a, b in bw["windows"] if f"{a}–{b}" not in got.get("answer", "")]
    if "answer_set" in facts:
        exp_set = set(facts["answer_set"])
        got_set = {s for s in re.findall(r"[A-Z][a-z]+ [a-z]+", got.get("answer") or "")}
        tp = len(exp_set & got_set)
        f1 = 0 if not tp else 2 * tp / (len(exp_set) + len(got_set))
        if f1 < 0.999:
            fx.append(f"答案集合 F1={f1:.2f}")
    if bw is not None or "answer_set" in facts:
        out.append(_c("facts", not fx, "; ".join(fx)))

    a = exp.get("answer", {})
    k_bad = [w for w in a.get("include_all", []) if norm(w) not in ans]
    if a.get("include_any") and not any(norm(w) in ans for w in a["include_any"]):
        k_bad.append(f"任一 {a['include_any']}")
    ex = [w for w in a.get("exclude", []) if norm(w) in ans]
    if a.get("include_all") or a.get("include_any") or a.get("exclude"):
        out.append(_c("keywords", not (k_bad or ex), f"缺 {k_bad} 不应出现 {ex}"))
    return out
