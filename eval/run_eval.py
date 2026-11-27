"""记忆管理 agent 的评测（A7）：用检验用例测本地模型在三个问题上判断得准不准，以及代码核对拦下了多少错误降级。

被测的是 B 写的判断函数，不经过服务、不经过 MCP。它的输入输出和接口文档 13.7 一致：
    judge(material: dict) -> dict
    material 就是 manager_fetch 的返回：title、content、default_label、writer_reads、related
    返回就是 manager_suggest 的参数：keep、keep_reason、relation、related_ids、relation_reason、downgrade
函数应当自己处理模型出错；这里仍然兜底：抛异常、返回不是 dict、字段不对，都记为“输出不合法”。

用法（仓库根目录；本地模型已预热）：
    python eval/run_eval.py --judge review.manager:judge --cases eval/cases_sample
    python eval/run_eval.py --judge review.manager:judge --cases ~/mv-heldout --runs 3
    python eval/run_eval.py ... --cloud       # 只含外部数据的用例，再用现有云端 reviewer 跑一遍做对比

--judge 是“模块:函数”，模块按仓库根目录导入。--cases 是文件夹（每个用例一个 .json）或一个 JSON 列表文件。
门槛写在 eval/criteria.md 的 JSON 代码块里，必须在跑之前提交；结果写到 eval/results/<时间>/。

计分规则：
- keep、relation 各自和标准答案比，输出不合法算不一致。
- 降级比两次：核对前（模型建议的标签）和核对后（policy.check_downgrade 通过才算数）。
  输出不合法或核对作废都按“不降级”处理，这正是系统的实际行为。
- 误降级：最终标签比标准答案更宽松（标准答案是不降级、却降了，也算）。
"""

import argparse
import csv
import importlib
import json
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.policy import EXPOSURES, check_downgrade, label_at_most  # noqa: E402

RELATIONS = ("none", "duplicate", "conflict")
CRITERIA_FILE = ROOT / "eval" / "criteria.md"


# ---------------------------------------------------------------- 用例和门槛


def load_cases(path: Path) -> list[dict]:
    path = path.expanduser()
    if path.is_dir():
        cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(path.glob("*.json"))]
    else:
        cases = json.loads(path.read_text(encoding="utf-8"))
    for case in cases:
        missing = {"id", "write", "writer_reads", "related", "expected"} - set(case)
        if missing:
            raise ValueError(f"用例 {case.get('id', '?')} 缺字段：{sorted(missing)}")
    return cases


def load_criteria(path: Path = CRITERIA_FILE) -> dict:
    """criteria.md 里第一个 ```json 代码块就是门槛。"""
    match = re.search(r"```json\s*(\{.*?\})\s*```", path.read_text(encoding="utf-8"), re.S)
    if not match:
        raise ValueError(f"{path} 里没有 ```json 门槛代码块")
    return json.loads(match.group(1))


def criteria_commit(path: Path = CRITERIA_FILE) -> str:
    """门槛文件最后一次提交的 commit；没提交过就提示，防止跑完再改门槛。"""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%h %ci", "--", str(path)], cwd=ROOT,
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        out = ""
    return out or "未提交（先提交 eval/criteria.md 再跑正式评测）"


def material_of(case: dict) -> dict:
    """用例 → 判断函数的输入，形状和 manager_fetch 的返回一致。"""
    w = case["write"]
    return {"pending_id": case["id"], "title": w["title"], "content": w["content"],
            "default_label": w["default_label"], "writer_reads": case["writer_reads"], "related": case["related"]}


# ---------------------------------------------------------------- 判断一条


def valid_output(out: object) -> bool:
    return (isinstance(out, dict) and isinstance(out.get("keep"), bool) and out.get("relation") in RELATIONS
            and (out.get("downgrade") is None or isinstance(out.get("downgrade"), dict)))


def _label(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    try:
        label = {"level": int(value["level"]), "scope": str(value["scope"]), "exposure": str(value["exposure"])}
    except (KeyError, TypeError, ValueError):
        return None
    return label if label["exposure"] in EXPOSURES else None


def more_permissive(final: dict | None, expected: dict | None, default: dict) -> bool:
    """final 是否比标准答案更宽松（误降级）。expected 为 None 表示应保持默认标签。"""
    if final is None:
        return False
    target = expected or default
    return not label_at_most(target, final)


def score_case(case: dict, judge) -> dict:
    material, expected = material_of(case), case["expected"]
    default = case["write"]["default_label"]
    start = time.monotonic()
    try:
        out = judge(material)
    except Exception as e:
        out = {"_error": f"{type(e).__name__}: {e}"}
    seconds = time.monotonic() - start
    valid = valid_output(out)

    suggestion = out.get("downgrade") if valid else None
    suggested = _label(suggestion.get("label")) if isinstance(suggestion, dict) else None
    reads = {b["block_id"]: b for b in case["writer_reads"]}
    check, reason, computed = check_downgrade(default, suggestion, case["write"]["content"], reads) \
        if valid else ("none", "输出不合法，按不降级处理", None)
    exp_down = _label(expected.get("downgrade"))

    return {
        "id": case["id"], "category": case.get("category", ""), "valid": valid, "seconds": round(seconds, 2),
        "keep": out.get("keep") if valid else None, "expected_keep": expected["keep"],
        "keep_ok": valid and out["keep"] == expected["keep"],
        "relation": out.get("relation") if valid else None, "expected_relation": expected["relation"],
        "relation_ok": valid and out["relation"] == expected["relation"],
        "suggested": json.dumps(suggested, ensure_ascii=False) if suggested else "",
        "check": check, "check_reason": reason,
        "final": json.dumps(computed, ensure_ascii=False) if computed else "",
        "expected_downgrade": json.dumps(exp_down, ensure_ascii=False) if exp_down else "",
        "downgrade_ok_before": suggested == exp_down,
        "downgrade_ok_after": computed == exp_down,
        "false_down_before": more_permissive(suggested, exp_down, default),
        "false_down_after": more_permissive(computed, exp_down, default),
        "error": out.get("_error", "") if isinstance(out, dict) else "返回值不是 dict",
    }


def summarize(rows: list[dict]) -> dict:
    n = len(rows) or 1
    return {
        "cases": len(rows),
        "keep_agreement": sum(r["keep_ok"] for r in rows) / n,
        "relation_agreement": sum(r["relation_ok"] for r in rows) / n,
        "downgrade_agreement_before": sum(r["downgrade_ok_before"] for r in rows) / n,
        "downgrade_agreement_after": sum(r["downgrade_ok_after"] for r in rows) / n,
        "false_downgrades_before": sum(r["false_down_before"] for r in rows),
        "false_downgrades_after": sum(r["false_down_after"] for r in rows),
        "invalid_rate": sum(not r["valid"] for r in rows) / n,
        "mean_seconds": statistics.mean(r["seconds"] for r in rows) if rows else 0.0,
    }


def verdict(summary: dict, criteria: dict) -> list[tuple[str, bool, str]]:
    """逐条对照门槛，返回 (名称, 是否达标, 说明)。"""
    checks = []
    for key in ("keep_agreement", "relation_agreement", "downgrade_agreement_after"):
        if key in criteria:
            checks.append((key, summary[key] >= criteria[key], f"{summary[key]:.0%} ≥ {criteria[key]:.0%}"))
    if "max_false_downgrades_after" in criteria:
        lim = criteria["max_false_downgrades_after"]
        checks.append(("false_downgrades_after", summary["false_downgrades_after"] <= lim,
                       f"{summary['false_downgrades_after']} ≤ {lim}"))
    if "max_invalid_rate" in criteria:
        lim = criteria["max_invalid_rate"]
        checks.append(("invalid_rate", summary["invalid_rate"] <= lim, f"{summary['invalid_rate']:.0%} ≤ {lim:.0%}"))
    return checks


# ---------------------------------------------------------------- 云端对比（只用外部数据）


def external_only(case: dict) -> bool:
    blocks = case["writer_reads"] + case["related"]
    return (case["write"]["default_label"].get("exposure") == "external"
            and all(b.get("exposure", "external") == "external" for b in blocks))


def cloud_keep(case: dict) -> bool | None:
    """现有云端 reviewer 只给出 pass/quarantine/reject，所以只能比“是否保存”。内部数据绝不发出去。"""
    if not external_only(case):
        raise ValueError(f"{case['id']} 含内部数据，不能发给云端")
    from review.reviewer import review
    context = [{k: b[k] for k in ("block_id", "title", "content", "level", "scope")}
               for b in case["writer_reads"] + case["related"]]
    action = review(case["write"]["title"], case["write"]["content"], context).get("action")
    return None if action not in ("pass", "quarantine", "reject") else action == "pass"


# ---------------------------------------------------------------- 主流程


def load_judge(spec: str):
    module, _, name = spec.partition(":")
    if not name:
        raise ValueError("--judge 要写成 模块:函数，例如 review.manager:judge")
    return getattr(importlib.import_module(module), name)


def write_report(out_dir: Path, runs: list[list[dict]], criteria: dict, commit: str, cloud: list[dict]) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = list(runs[0][0]) if runs and runs[0] else []
    with (out_dir / "results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["run", *fields])
        writer.writeheader()
        for i, rows in enumerate(runs, start=1):
            for r in rows:
                writer.writerow({"run": i, **r})

    summaries = [summarize(rows) for rows in runs]
    lines = [f"# 记忆管理评测 {out_dir.name}", "", f"- 门槛文件：eval/criteria.md（{commit}）",
             f"- 用例 {summaries[0]['cases'] if summaries else 0} 条，跑 {len(runs)} 遍", "",
             "| 指标 | " + " | ".join(f"第 {i} 遍" for i in range(1, len(runs) + 1)) + " |",
             "| --- |" + " --- |" * len(runs)]
    labels = [("keep_agreement", "是否保存 一致率", "{:.0%}"), ("relation_agreement", "重复/矛盾 一致率", "{:.0%}"),
              ("downgrade_agreement_before", "降级 一致率（核对前）", "{:.0%}"),
              ("downgrade_agreement_after", "降级 一致率（核对后）", "{:.0%}"),
              ("false_downgrades_before", "误降级（核对前）", "{}"), ("false_downgrades_after", "误降级（核对后）", "{}"),
              ("invalid_rate", "输出不合法比例", "{:.0%}"), ("mean_seconds", "平均耗时（秒）", "{:.1f}")]
    for key, name, fmt in labels:
        lines.append(f"| {name} | " + " | ".join(fmt.format(s[key]) for s in summaries) + " |")
    lines += ["", "## 对照门槛（每一遍都要达标）", ""]
    for i, s in enumerate(summaries, start=1):
        checks = verdict(s, criteria)
        status = "达标" if all(ok for _, ok, _ in checks) else "未达标"
        lines.append(f"- 第 {i} 遍：{status}；" + "；".join(f"{'✓' if ok else '✗'} {k} {d}" for k, ok, d in checks))
    if cloud:
        agree = sum(c["cloud_keep"] == c["expected_keep"] for c in cloud if c["cloud_keep"] is not None)
        lines += ["", "## 云端 reviewer 对比（只含外部数据的用例，只比“是否保存”）", "",
                  f"- 用例 {len(cloud)} 条，云端与标准答案一致 {agree} 条"]
        lines += [f"  - {c['id']}：标准 {c['expected_keep']}，云端 {c['cloud_keep']}，本地 {c['local_keep']}" for c in cloud]
    text = "\n".join(lines) + "\n"
    (out_dir / "summary.md").write_text(text, encoding="utf-8")
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="记忆管理 agent 评测")
    parser.add_argument("--judge", required=True, help="模块:函数，例如 review.manager:judge")
    parser.add_argument("--cases", required=True, type=Path, help="用例文件夹或 JSON 列表文件")
    parser.add_argument("--runs", type=int, default=1, help="重复几遍，确认结果稳定（正式评测用 2–3）")
    parser.add_argument("--cloud", action="store_true", help="外部数据用例再用云端 reviewer 跑一遍")
    parser.add_argument("--out", type=Path, help="结果文件夹，默认 eval/results/<时间>")
    args = parser.parse_args()

    criteria, commit = load_criteria(), criteria_commit()
    cases, judge = load_cases(args.cases), load_judge(args.judge)
    print(f"用例 {len(cases)} 条；门槛 {criteria}；门槛文件 {commit}\n")

    runs = []
    for i in range(1, args.runs + 1):
        rows = []
        for case in cases:
            row = score_case(case, judge)
            rows.append(row)
            flag = "" if row["valid"] else "  [输出不合法]"
            print(f"[{i}] {row['id']:<8} keep {'✓' if row['keep_ok'] else '✗'}  relation {'✓' if row['relation_ok'] else '✗'}"
                  f"  降级 {'✓' if row['downgrade_ok_after'] else '✗'}（{row['check']}）  {row['seconds']:.1f}s{flag}")
        runs.append(rows)

    cloud = []
    if args.cloud:
        local = {r["id"]: r["keep"] for r in runs[0]}
        for case in filter(external_only, cases):
            try:
                keep = cloud_keep(case)
            except Exception as e:
                print(f"云端对比跳过 {case['id']}：{type(e).__name__}: {e}")
                continue
            cloud.append({"id": case["id"], "expected_keep": case["expected"]["keep"], "cloud_keep": keep,
                          "local_keep": local.get(case["id"])})

    out_dir = args.out or ROOT / "eval" / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    print("\n" + write_report(out_dir, runs, criteria, commit, cloud))
    print(f"结果：{out_dir}/results.csv、summary.md")


if __name__ == "__main__":
    main()
