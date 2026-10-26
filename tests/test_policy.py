"""policy.py 的单元测试。

运行（在仓库根目录）：
    python -m unittest tests.test_policy -v
"""

import json
import unittest
from pathlib import Path

from server.policy import can_read, check_review_action, denial, label_for_write

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"  # 测试专用数据，与 data/ 下的演示数据无关


def load_agents() -> dict:
    config = json.loads((ROOT / "config" / "agents.example.json").read_text(encoding="utf-8"))
    return {a["agent_id"]: a for a in config["agents"]}


def load_blocks(name: str = "seed_blocks_zh.json") -> dict:
    blocks = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return {b["block_id"]: b for b in blocks}


# 预期权限矩阵，抄自 tests/fixtures/README.md。None 表示 ALLOW。
EXPECTED = {
    "b-001": {"finance": None, "intern": None},
    "b-002": {"finance": None, "intern": None},
    "b-101": {"finance": None, "intern": "LEVEL_TOO_LOW"},
    "b-102": {"finance": None, "intern": "LEVEL_TOO_LOW"},
    "b-207": {"finance": None, "intern": "LEVEL_TOO_LOW"},
    "b-208": {"finance": None, "intern": "LEVEL_TOO_LOW"},
    "b-318": {"finance": None, "intern": "LEVEL_TOO_LOW"},
    "b-412": {"finance": "LEVEL_TOO_LOW", "intern": "LEVEL_TOO_LOW"},
    "b-413": {"finance": "LEVEL_TOO_LOW", "intern": "LEVEL_TOO_LOW"},
    "b-501": {"finance": "SCOPE_MISMATCH", "intern": "LEVEL_TOO_LOW"},
}


class TestCanRead(unittest.TestCase):
    def setUp(self):
        self.agents = load_agents()

    def test_permission_matrix_zh(self):
        self._check_matrix(load_blocks("seed_blocks_zh.json"))

    def test_permission_matrix_en(self):
        self._check_matrix(load_blocks("seed_blocks_en.json"))

    def _check_matrix(self, blocks):
        self.assertEqual(set(blocks), set(EXPECTED), "种子数据的 block 与预期矩阵不一致")
        for bid, expected in EXPECTED.items():
            for aid, code in expected.items():
                with self.subTest(block=bid, agent=aid):
                    self.assertEqual(can_read(self.agents[aid], blocks[bid]), code)

    def test_quarantined_is_not_found_even_for_high_level(self):
        boss = {"level": 3, "read_scopes": ["public", "finance", "mgmt"]}
        block = {"level": 0, "scope": "public", "status": "quarantined"}
        self.assertEqual(can_read(boss, block), "NOT_FOUND")

    def test_unverified_is_readable(self):
        agent = {"level": 1, "read_scopes": ["release"]}
        block = {"level": 1, "scope": "release", "status": "unverified"}
        self.assertIsNone(can_read(agent, block))

    def test_level_checked_before_scope(self):
        agent = {"level": 0, "read_scopes": ["public"]}
        block = {"level": 1, "scope": "rnd", "status": "verified"}
        self.assertEqual(can_read(agent, block), "LEVEL_TOO_LOW")

    def test_equal_level_is_allowed(self):
        agent = {"level": 2, "read_scopes": ["finance"]}
        block = {"level": 2, "scope": "finance", "status": "verified"}
        self.assertIsNone(can_read(agent, block))


class TestDenial(unittest.TestCase):
    def setUp(self):
        self.blocks = load_blocks()

    def test_level_denial_fields(self):
        d = denial("LEVEL_TOO_LOW", "b-412", self.blocks["b-412"])
        self.assertEqual(d["denied"], True)
        self.assertEqual(d["reason_code"], "LEVEL_TOO_LOW")
        self.assertEqual(d["required_level"], 3)
        self.assertNotIn("required_scope", d)
        self.assertIn("管理层负责人", d["message"])

    def test_scope_denial_fields(self):
        d = denial("SCOPE_MISMATCH", "b-501", self.blocks["b-501"])
        self.assertEqual(d["required_scope"], "rnd")
        self.assertNotIn("required_level", d)
        self.assertIn("研发负责人", d["message"])

    def test_not_found_reveals_nothing(self):
        d = denial("NOT_FOUND", "b-999", self.blocks["b-412"])
        self.assertEqual(set(d), {"denied", "reason_code", "block_id"})

    def test_english_message(self):
        d = denial("LEVEL_TOO_LOW", "b-412", self.blocks["b-412"], lang="en")
        self.assertIn("management owner", d["message"])


class TestLabelForWrite(unittest.TestCase):
    def setUp(self):
        self.agents = load_agents()

    def test_level_follows_session_max(self):
        for level in (0, 1, 2, 3):
            with self.subTest(level=level):
                self.assertEqual(label_for_write(self.agents["finance"], level), (level, "finance"))

    def test_scope_is_write_scope(self):
        self.assertEqual(label_for_write(self.agents["intern"], 0), (0, "public"))

    def test_invalid_session_level_rejected(self):
        for bad in (-1, 4, "2", 2.0, True, None):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    label_for_write(self.agents["finance"], bad)


class TestCheckReviewAction(unittest.TestCase):
    def test_valid_actions_pass_through(self):
        for action in ("pass", "quarantine", "reject"):
            with self.subTest(action=action):
                self.assertEqual(
                    check_review_action({"action": action, "reason": "  理由  "}),
                    (action, "理由"),
                )

    def test_invalid_results_become_quarantine(self):
        cases = [
            None,
            "pass",
            ["pass"],
            {},
            {"reason": "缺 action"},
            {"action": "PASS", "reason": "大小写不对"},
            {"action": "approve", "reason": "不在三种之内"},
            {"action": "set_level", "level": 0, "reason": "试图改级别"},
        ]
        for result in cases:
            with self.subTest(result=result):
                action, reason = check_review_action(result)
                self.assertEqual(action, "quarantine")
                self.assertTrue(reason)

    def test_missing_reason_gets_default(self):
        self.assertEqual(check_review_action({"action": "pass"}), ("pass", "未提供理由"))
        self.assertEqual(check_review_action({"action": "pass", "reason": 123}), ("pass", "未提供理由"))

    def test_reason_truncated_to_200(self):
        _, reason = check_review_action({"action": "reject", "reason": "长" * 500})
        self.assertEqual(len(reason), 200)

    def test_extra_fields_ignored(self):
        # 审核函数多返回的字段（例如试图修改级别）一律忽略
        self.assertEqual(
            check_review_action({"action": "pass", "reason": "ok", "level": 0, "scope": "public"}),
            ("pass", "ok"),
        )


if __name__ == "__main__":
    unittest.main()
