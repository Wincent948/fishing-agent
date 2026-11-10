"""评测运行器：批量跑题 → 逐轮打分 → 写结果与报告。

用法（项目根目录）：
  python -m eval.runner                       # 全部：黄金集 200 + 多轮 50 + 对抗 80
  python -m eval.runner --sets golden --split test
  python -m eval.runner --compare <run_id_A> <run_id_B>   # 版本对比

输出 eval/results/<run_id>/：
  items.jsonl   每题（多轮按轮）：是否通过、失败检查与原因、工具调用、护栏、token、延迟、trace_id
  summary.json  各类别通过率、各检查项失败次数、延迟、token、工具调用统计
  report.md     人读的报告（含失败明细）
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

from app.memory import store
from app.service import run_turn
from eval.scorers import score_turn

EVAL = Path(__file__).resolve().parent
SETS = {"golden": "golden.jsonl", "multiturn": "multiturn.jsonl", "adversarial": "adversarial.jsonl"}


def ensure_assets():
    """边界题素材：PDF、GIF、超过 10 MB 的 JPG（稀疏文件，不占真实磁盘）。"""
    b = EVAL / "assets" / "boundary"
    b.mkdir(parents=True, exist_ok=True)
    (b / "file.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (b / "anim.gif").write_bytes(b"GIF89a")
    with open(b / "huge_25mb.jpg", "wb") as f:
        f.truncate(25 * 2**20)


def load(name):
    return [json.loads(l) for l in open(EVAL / "datasets" / SETS[name], encoding="utf-8")]


def run_item(item: dict, run_id: str) -> dict:
    ctx = item["context"]
    thread = f"{run_id}-{item['id']}"
    turns_out, all_pass = [], True
    for i, turn in enumerate(item["turns"]):
        if turn.get("new_session"):
            thread = f"{thread}-s{i}"
        got = run_turn(ctx["user_id"], thread, text=turn.get("user") or "", image=turn.get("image"),
                       resume=turn.get("resume"), today=ctx["today"])
        exp = turn["expected"] if "expected" in turn else item["expected"]
        checks = score_turn(exp, got)
        ok = all(c["passed"] for c in checks)
        all_pass &= ok
        turns_out.append({"turn": i + 1, "passed": ok, "user": turn.get("user"), "answer": got["answer"],
                          "behavior": got["behavior"], "intents": got["intents"], "route_source": got["route_source"],
                          "failed_checks": [c for c in checks if not c["passed"]], "checks": [c["check"] for c in checks],
                          "tool_calls": [{k: t.get(k) for k in ("name", "args", "ok", "note", "latency_ms")} for t in got["tool_calls"]],
                          "guard_hits": got["guard_hits"], "tokens": got["usage"], "latency_ms": got["latency_ms"],
                          "trace_id": got["trace_id"], "trace": got["trace"], "error": got["error"]})
        if not item.get("turns") or "expected" not in turn:
            break
    return {"id": item["id"], "category": item["category"], "split": item["split"], "passed": all_pass,
            "turns": turns_out}


def summarize(rows: list[dict]) -> dict:
    by_cat = defaultdict(lambda: [0, 0])
    fails = Counter()
    for r in rows:
        by_cat[r["category"]][0] += r["passed"]
        by_cat[r["category"]][1] += 1
        for t in r["turns"]:
            fails.update(c["check"] for c in t["failed_checks"])
    turns = [t for r in rows for t in r["turns"]]
    lat = sorted(t["latency_ms"] for t in turns)
    multi = [r for r in rows if r["category"] == "multiturn"]
    return {
        "items": len(rows), "passed": sum(r["passed"] for r in rows),
        "pass_rate": round(sum(r["passed"] for r in rows) / max(1, len(rows)), 4),
        "by_category": {k: {"passed": v[0], "total": v[1], "rate": round(v[0] / v[1], 4)} for k, v in sorted(by_cat.items())},
        "failed_checks": dict(fails.most_common()),
        "multiturn_turn_pass_rate": round(statistics.mean(t["passed"] for r in multi for t in r["turns"]), 4) if multi else None,
        "latency_ms": {"p50": lat[len(lat) // 2] if lat else 0, "p95": lat[int(len(lat) * 0.95) - 1] if lat else 0},
        "avg_tool_calls": round(statistics.mean(len(t["tool_calls"]) for t in turns), 2) if turns else 0,
        "tokens": {"in": sum(t["tokens"]["in"] for t in turns), "out": sum(t["tokens"]["out"] for t in turns),
                   "llm_calls": sum(t["tokens"]["llm_calls"] for t in turns)},
        "guard_hits": dict(Counter(h["guard"] for t in turns for h in t["guard_hits"])),
        "route_sources": dict(Counter((t["route_source"] or "guard").split(":")[0] for t in turns)),
        "errors": sum(1 for t in turns if t["error"]),
    }


def report_md(run_id: str, s: dict, rows: list[dict], meta: dict) -> str:
    L = [f"# 评测报告 {run_id}", "", f"- 模式：{meta['mode']}；数据：合成数据（syn-v1）；集合：{meta['sets']}；切分：{meta['split']}",
         f"- **总通过率 {s['pass_rate']:.1%}**（{s['passed']}/{s['items']}）；多轮按轮通过率 {s['multiturn_turn_pass_rate']}",
         f"- 延迟 P50 {s['latency_ms']['p50']} ms / P95 {s['latency_ms']['p95']} ms；平均工具调用 {s['avg_tool_calls']} 次；"
         f"LLM 调用 {s['tokens']['llm_calls']} 次，token 输入 {s['tokens']['in']} / 输出 {s['tokens']['out']}",
         f"- 护栏命中：{s['guard_hits']}；路由来源：{s['route_sources']}；运行错误：{s['errors']}", "",
         "## 分类别通过率", "", "| 类别 | 通过 | 总数 | 通过率 |", "| --- | --- | --- | --- |"]
    L += [f"| {k} | {v['passed']} | {v['total']} | {v['rate']:.1%} |" for k, v in s["by_category"].items()]
    L += ["", "## 失败检查项（按轮计数）", "", "| 检查项 | 失败次数 |", "| --- | --- |"]
    L += [f"| {k} | {v} |" for k, v in s["failed_checks"].items()]
    L += ["", "## 失败明细（前 40 条）", ""]
    n = 0
    for r in rows:
        for t in r["turns"]:
            if t["passed"] or n >= 40:
                continue
            n += 1
            why = "；".join(f"{c['check']}：{c['detail']}" for c in t["failed_checks"])
            L.append(f"- **{r['id']}** 第 {t['turn']} 轮「{(t['user'] or '')[:30]}」→ {t['behavior']} | {why} | trace {t['trace_id']}")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="golden,multiturn,adversarial")
    ap.add_argument("--split", default="all", choices=["all", "dev", "test"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--run-id", default="")
    a = ap.parse_args()
    from app import config
    run_id = a.run_id or time.strftime("%Y%m%d-%H%M%S")
    ensure_assets()
    store.reset()
    rows = []
    for name in a.sets.split(","):
        items = [x for x in load(name) if a.split == "all" or x["split"] == a.split]
        for item in items[: a.limit or None]:
            rows.append(run_item(item, run_id))
    s = summarize(rows)
    out = EVAL / "results" / run_id
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "items.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    meta = {"mode": "offline（规则 / 模板）" if config.OFFLINE else "online（LLM）", "sets": a.sets, "split": a.split}
    (out / "summary.json").write_text(json.dumps({**meta, **s}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(report_md(run_id, s, rows, meta), encoding="utf-8")
    print(json.dumps({"run_id": run_id, "pass_rate": s["pass_rate"], "by_category": s["by_category"],
                      "failed_checks": s["failed_checks"]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
