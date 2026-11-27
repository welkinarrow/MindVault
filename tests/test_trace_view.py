"""scripts/trace_view.py 的单元测试：用 tests/fixtures/trace 下的 mock 数据生成页面并检查内容。

运行（在仓库根目录）：
    python -m unittest tests.test_trace_view -v
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import trace_view  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "trace"
FIELDS = {"title", "author", "created_at", "level", "scope", "exposure", "status", "stale",
          "approved_by", "approved_at", "sources"}


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestFixtures(unittest.TestCase):
    def test_match_interface_format(self):
        """mock 数据要和 interfaces.md 13.6 的返回格式一致，A5 和 T9 才能对得上。"""
        for name in ("scenario2_answer.json", "scenario2_b610.json", "downgrade_local.json", "downgrade_cloud.json"):
            with self.subTest(name=name):
                trace = load(name)
                self.assertTrue({"answer_id", "agent", "answer", "citations", "blocks", "hidden_sources", "root"}
                                <= set(trace))
                for block_id, block in trace["blocks"].items():
                    self.assertTrue(FIELDS <= set(block), f"{block_id} 缺字段：{FIELDS - set(block)}")
                    for source in block["sources"]:
                        self.assertIn(source, trace["blocks"], f"{block_id} 的来源 {source} 不在 blocks 里")


class TestRender(unittest.TestCase):
    def test_scenario2(self):
        page = trace_view.render(load("scenario2_answer.json"), load("scenario2_b610.json"))
        self.assertEqual(re.findall(r'<span class="mark" data-ref="(b-\d+)">', page), ["b-602", "b-603", "b-601"])
        ranks = re.findall(r'<li data-ref="(b-\d+)"', page)
        self.assertEqual(ranks, ["b-602", "b-603", "b-601"])
        self.assertIn('id="card-b-610"', page)
        self.assertIn("审批：admin", page)
        self.assertNotIn('class="two"', page)
        self.assertNotIn('data-self="b-610"', page)   # 三栏时来源链不画线，避免横穿回答

    def test_cards_sorted_by_time(self):
        page = trace_view.render(load("scenario2_answer.json"), None)
        order = re.findall(r'id="card-(b-\d+)"', page)
        self.assertEqual(order, ["b-601", "b-602", "b-603"])

    def test_cloud_view_hides_source_ids(self):
        page = trace_view.render(None, load("downgrade_cloud.json"), viewer="wang-cloud")
        self.assertIn("已隐藏 1 条来源", page)
        self.assertIn("+1 已隐藏", page)
        self.assertNotIn("b-802", page)
        self.assertIn('class="two"', page)
        self.assertIn('data-self="w-0015"', page)

    def test_local_view_shows_all_sources(self):
        page = trace_view.render(None, load("downgrade_local.json"))
        self.assertIn('id="card-b-802"', page)
        self.assertNotIn("已隐藏", page)

    def test_content_is_escaped(self):
        trace = load("scenario2_answer.json")
        trace["blocks"]["b-601"]["content"] = '<script>alert(1)</script>'
        trace["answer"] = '<img src=x onerror=alert(1)> [b-601]'
        page = trace_view.render(trace, None)
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;script&gt;", page)

    def test_english(self):
        page = trace_view.render(None, load("downgrade_cloud.json"), lang="en")
        self.assertIn("1 source(s) hidden", page)
        self.assertIn('lang="en"', page)


if __name__ == "__main__":
    unittest.main()
