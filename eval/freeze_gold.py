"""冻结依赖下载数据的金标，并合成黄金集 golden.jsonl。

只需在下载完数据后运行一次。输出写死在 datasets/ 里，manifest.json 记录全部文件与数据快照的 sha256。
之后不要再运行：金标一旦冻结就不再变化；数据快照变了，就重新冻结并升级版本号。

用法（在 eval/ 目录下）：
  python freeze_gold.py \
      --fishvista-test  ../data/raw/fish-vista/classification_test.csv \
      --fishbase-species ../data/raw/fishbase/species.parquet \
      --fishbase-families ../data/raw/fishbase/families.parquet \
      --version v1

生成：
  datasets/identify.jsonl            I001–I050  识鱼（图片 → 鱼种标签取自 Fish-Vista test 划分）
  datasets/knowledge_fishbase.jsonl  K021–K050  FishBase 事实题 + 关系多跳题
  datasets/golden.jsonl              200 题 = 识鱼 50 + 知识 50 + 多跳 50 + 边界 50
  manifest.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

import pandas as pd

from authoring.build_authored import split_of
from authoring.reference import load_species, rule_facts

EVAL = Path(__file__).resolve().parent
DS = EVAL / "datasets"
SEED = 42
SP = load_species()
CORE_SCI = {r["scientific_name"]: sid for sid, r in SP.items()}
WRITE_TOOLS = ["log_catch", "forget_memory"]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pick_col(df: pd.DataFrame, *names: str) -> str:
    for n in names:
        if n in df.columns:
            return n
    raise KeyError(f"找不到列 {names}，实际列：{list(df.columns)[:30]}")


def base(item_id, category, user, image, intents, behavior, *, must_call=(), facts=None, include_all=(),
         include_any=(), exclude=(), gold_source, notes=""):
    return {
        "id": item_id, "category": category, "split": split_of(item_id),
        "context": {"user_id": "u_eval_01", "today": "2026-10-10"},
        "turns": [{"user": user, "image": image}],
        "expected": {"intents": intents, "behavior": behavior, "guard": None,
                     "tools": {"must_call": list(must_call), "must_not_call": WRITE_TOOLS, "args": {}},
                     "answer": {"facts": facts or {}, "include_all": list(include_all),
                                "include_any": list(include_any), "exclude": list(exclude)}},
        "gold_source": gold_source, "notes": notes,
    }


# ---------------------------------------------------------------- 识鱼

ID_QUESTIONS = [
    "这是什么鱼？", "帮我认一下这条鱼", "What fish is this?", "刚钓上来的，这是啥？", "这条鱼叫什么名字？",
    "这是什么鱼？能留吗？",  # 每 5 题插入一道「识鱼 + 规则」多意图题（见下方 combo 判断）
]


def build_identify(test_csv: Path, prefix: str = "fishvista") -> list[dict]:
    df = pd.read_csv(test_csv)
    if "split" in df.columns:
        df = df[df["split"] == "test"]
    sp_col = pick_col(df, "standardized_species", "species", "scientific_name")
    fn_col = pick_col(df, "filename", "file_name", "image_id", "image", "path")
    df[sp_col] = df[sp_col].astype(str).str.strip()

    missing = [s for s in CORE_SCI if s not in set(df[sp_col])]
    if missing:
        counts = df[sp_col].value_counts()
        cand = [s for s in counts.index if s not in CORE_SCI][:20]
        raise SystemExit("以下核心鱼种不在 Fish-Vista test 划分中，请在 species.csv / rules.csv 中替换后重跑：\n  "
                         + "\n  ".join(missing) + "\n可选替换（按图片数排序）：\n  " + "\n  ".join(cand))

    rng = random.Random(SEED)
    rows = []
    groups = {}
    for sid, r in SP.items():
        groups.setdefault(r["group"], []).append(r["scientific_name"])

    for i, (sid, r) in enumerate(sorted(SP.items()), 1):
        files = sorted(df.loc[df[sp_col] == r["scientific_name"], fn_col].tolist())
        img = rng.choice(files)
        combo = i % 5 == 0
        q = ID_QUESTIONS[5] if combo else ID_QUESTIONS[(i - 1) % 5]
        partners = [s for s in groups[r["group"]] if s != r["scientific_name"]]
        facts = {"species": r["scientific_name"], "accept_in_top3": partners}
        intents, calls, inc = ["identify_fish"], ["identify_fish"], []
        if combo:
            intents.append("rules")
            calls.append("get_species_rules")
            facts["rules"] = rule_facts(sid, "2026-10-10")
            inc = ["示例规则"]
        rows.append(base(f"I{i:03d}", "identify", q, f"{prefix}/{img}", intents, "answer",
                         must_call=calls, facts=facts, include_all=inc,
                         include_any=[r["scientific_name"], r["common_en"], r["zh_ref"]],
                         gold_source="fishvista@freeze",
                         notes="同组近似种（accept_in_top3）出现在 top-3 时，若系统选择追问而非直接作答，也判为正确"
                         if partners else ""))

    others = sorted(set(df[sp_col]) - set(CORE_SCI) - {"NOT_FISH"})
    for j, s in enumerate(rng.sample(others, 5), 41):
        img = rng.choice(sorted(df.loc[df[sp_col] == s, fn_col].tolist()))
        rows.append(base(f"I{j:03d}", "identify", "这是什么鱼？", f"{prefix}/{img}", ["identify_fish"], "answer",
                         must_call=["identify_fish"], facts={"species": s, "in_library": False},
                         include_any=["不在", "未收录", "无法确定", "不确定"],
                         exclude=list(CORE_SCI), gold_source="fishvista@freeze",
                         notes="库外鱼种：应拒识或说明不确定，不得高置信度地报出库内鱼种"))

    for k in range(1, 6):
        rows.append(base(f"I{45 + k:03d}", "identify", "这是什么鱼？", f"assets/nonfish/nf_{k:02d}.jpg",
                         ["identify_fish"], "answer", must_call=["identify_fish"], facts={"is_fish": False},
                         include_any=["不是鱼", "没有识别到鱼", "未检测到鱼"], exclude=list(CORE_SCI),
                         gold_source="authored", notes="非鱼图片（自备 5 张，如猫、鞋、风景）"))
    assert len(rows) == 50
    return rows


# ---------------------------------------------------------------- FishBase

FB_FACT_PLAN = [  # (题号, 鱼种, 字段, 问法)
    (21, "S01", "Length", "大口黑鲈最大体长记录是多少？"),
    (22, "S12", "Length", "Northern pike 最长能长到多少厘米？"),
    (23, "S14", "Weight", "斑点叉尾鮰最重记录是多少？"),
    (24, "S22", "habitat", "虹鳟能在海水里生活吗？"),
    (25, "S25", "Length", "长吻雀鳝最大体长？"),
    (26, "S26", "Family", "弓鳍鱼属于哪个科？"),
    (27, "S20", "Length", "鲤鱼最大能长多长？"),
    (28, "S11", "DemersPelag", "玻璃梭鲈生活在哪个水层？"),
    (29, "S35", "habitat", "圆虾虎鱼生活在淡水还是咸水？"),
    (30, "S10", "Length", "Yellow perch max length?"),
    (31, "S18", "Family", "金眼狼鲈是哪个科的？"),
    (32, "S29", "Length", "淡水石首鱼最大体长？"),
    (33, "S23", "habitat", "褐鳟能进入海水吗？"),
    (34, "S27", "DemersPelag", "美洲真鰶是底层鱼还是中上层鱼？"),
    (35, "S03", "Length", "蓝鳃太阳鱼最大能长多大？"),
    (36, "S24", "Weight", "美洲红点鲑最重记录？"),
    (37, "S28", "Family", "White sucker 属于哪个科？"),
    (38, "S30", "Length", "黑头软口鲦最大体长？"),
    (39, "S38", "habitat", "食蚊鱼能在半咸水生活吗？"),
    (40, "S19", "Length", "美洲狼鲈最大体长？"),
]


def load_fishbase_core(species_pq: Path, families_pq: Path) -> pd.DataFrame:
    sp = pd.read_parquet(species_pq)
    fam = pd.read_parquet(families_pq)[["FamCode", "Family"]]
    sp["sci"] = sp["Genus"].astype(str) + " " + sp["Species"].astype(str)
    return sp[sp["sci"].isin(CORE_SCI)].merge(fam, on="FamCode", how="left").set_index("sci")


def load_synthetic_core(facts_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(facts_csv)
    return df[df["sci"].isin(CORE_SCI)].set_index("sci")


def build_fishbase(core: pd.DataFrame, source: str = "fishbase@freeze") -> list[dict]:
    missing = sorted(set(CORE_SCI) - set(core.index))
    if missing:
        raise SystemExit("FishBase 中找不到这些学名（可能是同物异名，请改用 FishBase 的有效名）：\n  " + "\n  ".join(missing))

    def habitat(row):
        return {"fresh": bool(row["Fresh"] == -1 or row["Fresh"] == 1),
                "brackish": bool(row["Brack"] == -1 or row["Brack"] == 1),
                "marine": bool(row["Saltwater"] == -1 or row["Saltwater"] == 1)}

    rows = []
    for no, sid, field, q in FB_FACT_PLAN:
        s = SP[sid]["scientific_name"]
        row = core.loc[s]
        if field == "habitat":
            val = habitat(row)
        else:
            val = row[field]
            if pd.isna(val):
                raise SystemExit(f"K{no:03d}: {s} 的 {field} 在 FishBase 中为空，请在 FB_FACT_PLAN 里换一个鱼种")
            val = val.item() if hasattr(val, "item") else val
        inc = [f"{val:g}"] if isinstance(val, (int, float)) else ([str(val)] if field != "habitat" else [])
        rows.append(base(f"K{no:03d}", "knowledge", q, None, ["species_info"], "answer",
                         must_call=["search_species_kb"],
                         facts={"species": s, "field": field, "value": val,
                                "unit": {"Length": "cm", "Weight": "g"}.get(field)},
                         include_all=inc, gold_source=source,
                         notes="数值以冻结时的数据快照为准；回答需带引用"))

    # 关系 / 多跳题：在 40 个核心鱼种范围内计算答案集合
    core = core.assign(**{k: core.apply(lambda r, k=k: habitat(r)[k], axis=1) for k in ("fresh", "brackish", "marine")})
    sm = core.reset_index()

    def names(mask):
        return sorted(sm.loc[mask, "sci"].tolist())

    closed_today = {SP[s]["scientific_name"] for s in SP if rule_facts(s, "2026-10-10")["closed_on_date"]}
    no_closed = {SP[s]["scientific_name"] for s in SP if not rule_facts(s)["closed_season"]}
    rel = [
        (41, "库里哪些鱼既能在淡水也能在海水里生活？", names(sm["fresh"] & sm["marine"])),
        (42, "库里最大体长超过 100 cm 的鱼有哪些？", names(sm["Length"] > 100)),
        (43, "库里哪些鱼属于太阳鱼科（Centrarchidae）？", names(sm["Family"] == "Centrarchidae")),
        (44, "库里的北美鲶科（Ictaluridae）鱼有哪些？", names(sm["Family"] == "Ictaluridae")),
        (45, "库里哪些底栖鱼最大体长超过 50 cm？",
         names(sm["DemersPelag"].astype(str).str.contains("demersal") & (sm["Length"] > 50))),
        (46, "能在半咸水生活、而且没有禁捕期的鱼有哪些？", names(sm["brackish"] & sm["sci"].isin(no_closed))),
        (47, "鲑科（Salmonidae）里今天处于禁捕期的有哪些？",
         names((sm["Family"] == "Salmonidae") & sm["sci"].isin(closed_today))),
        (48, "库里最大体长不到 10 cm 的小鱼有哪些？", names(sm["Length"] < 10)),
        (49, "和大口黑鲈同一个科的其他鱼有哪些？",
         names((sm["Family"] == core.loc["Micropterus salmoides", "Family"]) & (sm["sci"] != "Micropterus salmoides"))),
        (50, "库里最重记录超过 10 kg 的鱼有哪些？", names(sm["Weight"] > 10000)),
    ]
    for no, q, ans in rel:
        if not ans:
            raise SystemExit(f"K{no:03d} 的答案集合为空，请调整题目条件")
        rows.append(base(f"K{no:03d}", "knowledge", q, None, ["species_info"], "answer",
                         must_call=["query_species_graph"], facts={"answer_set": ans},
                         gold_source=source + "+rules.csv",
                         notes="按集合打分：precision / recall / F1；答案集合只在 40 个核心鱼种内计算"))
    assert len(rows) == 30
    return rows


def write(name, rows):
    with open(DS / name, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def read(name):
    return [json.loads(l) for l in open(DS / name, encoding="utf-8")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic-dir", type=Path, help="合成数据目录（data/synthetic），给了就不需要下面三个参数")
    ap.add_argument("--fishvista-test", type=Path)
    ap.add_argument("--fishbase-species", type=Path)
    ap.add_argument("--fishbase-families", type=Path)
    ap.add_argument("--version", default="v1")
    a = ap.parse_args()
    if a.synthetic_dir:
        a.fishvista_test = a.synthetic_dir / "images.csv"
        snap = [a.fishvista_test, a.synthetic_dir / "species_facts.csv"]
        core, prefix, source = load_synthetic_core(a.synthetic_dir / "species_facts.csv"), "synthetic", "synthetic@freeze"
    else:
        snap = [a.fishvista_test, a.fishbase_species, a.fishbase_families]
        core, prefix, source = load_fishbase_core(a.fishbase_species, a.fishbase_families), "fishvista", "fishbase@freeze"

    if (DS / "golden.jsonl").exists():
        raise SystemExit("golden.jsonl 已存在：金标已冻结。若确需重冻，先删除它并升级 --version。")

    write("identify.jsonl", build_identify(a.fishvista_test, prefix))
    write("knowledge_fishbase.jsonl", build_fishbase(core, source))

    golden = (read("identify.jsonl") + read("knowledge_rules.jsonl") + read("knowledge_fishbase.jsonl")
              + read("multihop.jsonl") + read("boundary.jsonl"))
    ids = [g["id"] for g in golden]
    assert len(golden) == 200 and len(set(ids)) == 200, (len(golden), len(set(ids)))
    write("golden.jsonl", golden)

    files = ["species.csv", "rules.csv", "fixtures/conditions.json", "fixtures/poisoned_docs.jsonl",
             *[f"datasets/{p.name}" for p in sorted(DS.glob("*.jsonl"))]]
    manifest = {
        "version": a.version, "seed": SEED, "frozen_today": "2026-10-10",
        "counts": {"golden": 200, "multiturn": len(read("multiturn.jsonl")),
                   "adversarial": len(read("adversarial.jsonl"))},
        "files": {f: sha256(EVAL / f) for f in files},
        "data_mode": source.split("@")[0],
        "data_snapshot": {p.name: sha256(p) for p in snap},
    }
    (EVAL / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
