"""eval/run_eval.py 的单元测试：用几个假的判断函数检查计分规则，不需要模型。

运行（在仓库根目录）：
    python -m unittest tests.test_eval -v
命令行冒烟测试也可以用这里的假函数：
    python eval/run_eval.py --judge tests.test_eval:perfect_judge --cases eval/cases_sample
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
import run_eval  # noqa: E402

CASES = run_eval.load_cases(ROOT / "eval" / "cases_sample")
GOOD_EVIDENCE = [
    {"sentence": "新员工第一周要开通账号并完成安全培训。", "block_id": "b-801", "snippet": "新员工第一周完成账号开通和安全培训"},
    {"sentence": "周五有部门介绍会", "block_id": "b-801", "snippet": "周五参加部门介绍会"},
]


def _expected(material):
    return next(c for c in CASES if c["id"] == material["pending_id"])["expected"]


def perfect_judge(material):
    """照标准答案回答，降级时给出能通过核对的证据。"""
    exp = _expected(material)
    out = {"keep": exp["keep"], "keep_reason": "测试", "relation": exp["relation"],
           "related_ids": exp["related_ids"], "downgrade": None}
    if exp["downgrade"]:
        out["downgrade"] = {"label": exp["downgrade"], "evidence": GOOD_EVIDENCE}
    return out


def reckless_judge(material):
    """每条都建议降到 L0 外部，证据是编的：核对前全是误降级，核对后应该全被作废。"""
    out = perfect_judge(material)
    out["downgrade"] = {"label": {**material["default_label"], "level": 0, "exposure": "external"},
                        "evidence": [{"sentence": material["content"], "block_id": "b-801", "snippet": "完全编造的一段原文"}]}
    return out


def broken_judge(material):
    if material["pending_id"] == "s-01":
        raise TimeoutError("模型超时")
    if material["pending_id"] == "s-02":
        return "keep: yes"
    return {"keep": "yes", "relation": "none"}


class TestScoring(unittest.TestCase):
    def test_sample_cases_load(self):
        self.assertEqual([c["id"] for c in CASES], ["s-01", "s-02", "s-03"])

    def test_perfect(self):
        s = run_eval.summarize([run_eval.score_case(c, perfect_judge) for c in CASES])
        self.assertEqual((s["keep_agreement"], s["relation_agreement"], s["downgrade_agreement_after"]), (1, 1, 1))
        self.assertEqual((s["false_downgrades_before"], s["false_downgrades_after"], s["invalid_rate"]), (0, 0, 0))

    def test_code_check_blocks_reckless_downgrades(self):
        rows = [run_eval.score_case(c, reckless_judge) for c in CASES]
        s = run_eval.summarize(rows)
        # 只有 s-03 算误降级：s-01 的默认标签本来就是 L0 外部，s-02 本来就该降到 L0 外部
        self.assertEqual(s["false_downgrades_before"], 1)
        self.assertEqual(s["false_downgrades_after"], 0)
        self.assertTrue(all(r["check"] == "void" for r in rows))

    def test_invalid_outputs_count_as_no_downgrade(self):
        rows = [run_eval.score_case(c, broken_judge) for c in CASES]
        s = run_eval.summarize(rows)
        self.assertEqual(s["invalid_rate"], 1)
        self.assertEqual(s["keep_agreement"], 0)
        self.assertEqual(s["false_downgrades_after"], 0)
        self.assertIn("TimeoutError", rows[0]["error"])
        # 不合法按“不降级”：标准答案也是不降级的 s-01、s-03 算一致，该降级的 s-02 算不一致
        self.assertEqual([r["downgrade_ok_after"] for r in rows], [True, False, True])

    def test_more_permissive(self):
        default = {"level": 1, "scope": "rnd", "exposure": "internal"}
        l0 = {"level": 0, "scope": "rnd", "exposure": "external"}
        self.assertTrue(run_eval.more_permissive(l0, None, default))
        self.assertFalse(run_eval.more_permissive(l0, l0, default))
        self.assertFalse(run_eval.more_permissive(None, None, default))
        self.assertTrue(run_eval.more_permissive({"level": 1, "scope": "rnd", "exposure": "external"}, None, default))


class TestCriteria(unittest.TestCase):
    def test_parse_repo_criteria(self):
        c = run_eval.load_criteria()
        self.assertEqual(c["max_false_downgrades_after"], 0)

    def test_verdict(self):
        good = run_eval.summarize([run_eval.score_case(c, perfect_judge) for c in CASES])
        bad = run_eval.summarize([run_eval.score_case(c, broken_judge) for c in CASES])
        criteria = run_eval.load_criteria()
        self.assertTrue(all(ok for _, ok, _ in run_eval.verdict(good, criteria)))
        self.assertFalse(all(ok for _, ok, _ in run_eval.verdict(bad, criteria)))


class TestCloudGuard(unittest.TestCase):
    def test_only_external_cases_go_to_cloud(self):
        self.assertEqual([c["id"] for c in CASES if run_eval.external_only(c)], ["s-01"])
        with self.assertRaises(ValueError):
            run_eval.cloud_keep(CASES[1])   # 含内部手册，必须在发出去之前拒绝


class TestReport(unittest.TestCase):
    def test_writes_csv_and_summary(self):
        runs = [[run_eval.score_case(c, perfect_judge) for c in CASES] for _ in range(2)]
        with tempfile.TemporaryDirectory() as tmp:
            text = run_eval.write_report(Path(tmp), runs, run_eval.load_criteria(), "abc123", [])
            self.assertIn("第 2 遍：达标", text)
            rows = (Path(tmp) / "results.csv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1 + 2 * len(CASES))

    def test_case_file_list_format(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(CASES, f, ensure_ascii=False)
        self.assertEqual(len(run_eval.load_cases(Path(f.name))), 3)
        Path(f.name).unlink()


if __name__ == "__main__":
    unittest.main()
