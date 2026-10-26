"""search.py 的单元测试。

运行（在仓库根目录）：
    python -m unittest tests.test_search -v
"""

import json
import unittest
from pathlib import Path

from server.search import score, search, tokenize

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"  # 测试专用数据，与 data/ 下的演示数据无关


def load_agents() -> dict:
    config = json.loads((ROOT / "config" / "agents.example.json").read_text(encoding="utf-8"))
    return {a["agent_id"]: a for a in config["agents"]}


def load_blocks(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def ids(blocks) -> list[str]:
    return [b["block_id"] for b in blocks]


class TestTokenize(unittest.TestCase):
    def test_english_basics(self):
        self.assertEqual(
            tokenize("Why did the Q3 refunds rise?"),
            {"q3", "refund", "rise"},
        )

    def test_short_tokens_dropped(self):
        # “v5.2” 里的 “2” 太短，会匹配所有含数字 2 的内容
        self.assertEqual(tokenize("v5.2 release"), {"v5", "release"})

    def test_plural_s_only_for_longer_words(self):
        self.assertEqual(tokenize("bus gas refunds"), {"bus", "gas", "refund"})

    def test_chinese_bigrams(self):
        self.assertEqual(
            tokenize("华东区退款率"),
            {"华东", "东区", "区退", "退款", "款率"},
        )

    def test_chinese_stopwords_removed_before_bigrams(self):
        # 不去停用词会产生“的退”“升了”“为什”等垃圾词
        tokens = tokenize("华东区的退款率为什么上升了")
        self.assertEqual(tokens, {"华东", "东区", "退款", "款率", "上升"})

    def test_single_chinese_char_dropped(self):
        self.assertEqual(tokenize("支付失败 9月"), {"支付", "付失", "失败"})

    def test_mixed_language(self):
        self.assertEqual(tokenize("Q3华东区 refunds"), {"q3", "华东", "东区", "refund"})

    def test_only_stopwords_gives_empty(self):
        self.assertEqual(tokenize("为什么 the 了"), frozenset())


class TestScore(unittest.TestCase):
    def block(self, text):
        return {"title": "", "content": text}

    def test_prefix_match_not_substring(self):
        tokens = tokenize("rate")
        self.assertEqual(score(tokens, self.block("refund rate rose")), 1)
        self.assertEqual(score(tokens, self.block("handled separately")), 0)

    def test_prefix_matches_plural_in_text(self):
        self.assertEqual(score(tokenize("refund"), self.block("customers asked for refunds")), 1)

    def test_case_insensitive(self):
        self.assertEqual(score(tokenize("ANDROID"), self.block("Android app v5.2")), 1)

    def test_counts_distinct_tokens_once(self):
        self.assertEqual(score(tokenize("refund"), self.block("refund refund refund")), 1)

    def test_title_is_searched(self):
        self.assertEqual(score(tokenize("退款"), {"title": "退款政策", "content": "无关内容"}), 1)


class TestSearchExpectedResults(unittest.TestCase):
    """预期结果抄自 tests/fixtures/README.md 的“预期检索结果”两张表。"""

    CASES_ZH = [
        ("finance", "Q3 华东区 退款率 上升",
         ["b-101", "b-102", "b-207", "b-318", "b-001"], ["b-412"]),
        ("finance", "华东区的退款率为什么上升了",
         ["b-101", "b-102", "b-207", "b-318", "b-001"], ["b-412"]),
        ("finance", "支付失败 9月",
         ["b-207", "b-208", "b-318", "b-001", "b-002"], ["b-412", "b-413", "b-501"]),
        ("intern", "华东区退款率为什么上升",
         ["b-001"], ["b-101", "b-102", "b-207", "b-208", "b-318", "b-412"]),
    ]
    CASES_EN = [
        ("finance", "Q3 East China refund rate increase",
         ["b-101", "b-102", "b-207", "b-318", "b-001"], ["b-412"]),
        ("finance", "payment failure September",
         ["b-207", "b-002", "b-101", "b-208", "b-318"], ["b-412", "b-413", "b-501"]),
        ("intern", "East China refund rate",
         ["b-001"], ["b-101", "b-102", "b-207", "b-208", "b-318", "b-412"]),
    ]

    def setUp(self):
        self.agents = load_agents()

    def run_cases(self, seed_file, cases):
        blocks = load_blocks(seed_file)
        for agent_id, query, expected, expected_filtered in cases:
            with self.subTest(agent=agent_id, query=query):
                r = search(query, blocks, self.agents[agent_id])
                self.assertEqual(ids(r.results), expected)
                self.assertEqual(ids(b for b, _ in r.filtered), expected_filtered)

    def test_chinese_data(self):
        self.run_cases("seed_blocks_zh.json", self.CASES_ZH)

    def test_english_data(self):
        self.run_cases("seed_blocks_en.json", self.CASES_EN)

    def test_demo_blocks_always_found_by_finance(self):
        # 演示的关键：财务 agent 的常见问法都能同时检索到三个演示 block
        blocks = load_blocks("seed_blocks_zh.json")
        for query in ("Q3 华东区 退款率 上升", "华东区退款率为什么上升", "华东 退款 原因"):
            with self.subTest(query=query):
                found = set(ids(search(query, blocks, self.agents["finance"]).results))
                self.assertLessEqual({"b-101", "b-207", "b-318"}, found)


class TestSearchBehaviour(unittest.TestCase):
    def setUp(self):
        self.agents = load_agents()
        self.blocks = load_blocks("seed_blocks_zh.json")

    def test_filtered_reason_codes(self):
        r = search("支付失败 9月", self.blocks, self.agents["finance"])
        self.assertEqual(
            {b["block_id"]: code for b, code in r.filtered},
            {"b-412": "LEVEL_TOO_LOW", "b-413": "LEVEL_TOO_LOW", "b-501": "SCOPE_MISMATCH"},
        )

    def test_never_returns_unreadable_blocks(self):
        for agent_id in ("finance", "intern"):
            for query in ("华东 退款", "支付", "管理层 预算", "架构 回调"):
                with self.subTest(agent=agent_id, query=query):
                    for b in search(query, self.blocks, self.agents[agent_id]).results:
                        self.assertLessEqual(b["level"], self.agents[agent_id]["level"])
                        self.assertIn(b["scope"], self.agents[agent_id]["read_scopes"])

    def test_quarantined_never_returned(self):
        blocks = self.blocks + [{
            "block_id": "w-0001", "title": "华东区退款率", "content": "华东区退款率上升",
            "level": 0, "scope": "public", "status": "quarantined", "author": "intern",
        }]
        r = search("华东区 退款率", blocks, self.agents["intern"])
        self.assertNotIn("w-0001", ids(r.results))
        self.assertIn(("w-0001", "NOT_FOUND"), [(b["block_id"], c) for b, c in r.filtered])

    def test_limit(self):
        r = search("Q3 华东区 退款率 上升", self.blocks, self.agents["finance"], limit=3)
        self.assertEqual(ids(r.results), ["b-101", "b-102", "b-207"])

    def test_scores_attached_and_descending(self):
        r = search("Q3 华东区 退款率 上升", self.blocks, self.agents["finance"])
        scores = [b["_score"] for b in r.results]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(s >= 1 for s in scores))

    def test_does_not_mutate_input(self):
        before = json.dumps(self.blocks, sort_keys=True)
        search("华东 退款", self.blocks, self.agents["finance"])
        self.assertEqual(json.dumps(self.blocks, sort_keys=True), before)

    def test_no_match_returns_empty(self):
        r = search("天气 预报", self.blocks, self.agents["finance"])
        self.assertEqual((r.results, r.filtered), ([], []))

    def test_stopwords_only_returns_empty(self):
        r = search("为什么 the", self.blocks, self.agents["finance"])
        self.assertEqual(r.results, [])


if __name__ == "__main__":
    unittest.main()
