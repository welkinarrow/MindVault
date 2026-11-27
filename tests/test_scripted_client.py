"""scripts/scripted_client.py 的单元测试：用假的 MCP 服务检查调用顺序和输出，不需要服务和模型。

运行（在仓库根目录）：
    python -m unittest tests.test_scripted_client -v
"""

import asyncio
import io
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import scripted_client  # noqa: E402


class FakeMCP:
    def __init__(self, results):
        self.results, self.calls = results, []

    def __call__(self, url, key):
        self.key = key
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def call(self, tool, args):
        self.calls.append((self.key, tool, args))
        return self.results(tool, args)


def results(tool, args):
    if tool == "memory_cite":
        return {"answer_id": "c-0001", "citations": args["block_ids"]}
    if tool == "memory_write":
        return {"block_id": "w-0001", "status": "pending", "level": 1, "scope": "sales", "exposure": "internal"}
    if args.get("block_id") == "b-802":
        return {"denied": True, "reason_code": "EXPOSURE_INTERNAL", "message": "内部"}
    return {"results": []}


def play(acts):
    fake = FakeMCP(results)
    keys = {a: f"key-{a}" for a, *_ in acts}
    with mock.patch.object(scripted_client, "Agent", fake), redirect_stdout(io.StringIO()) as out:
        asyncio.run(scripted_client.play(acts, keys, "http://x/mcp", lambda e: None))
    return fake.calls, out.getvalue()


class TestScenarios(unittest.TestCase):
    def test_scenario2_order(self):
        calls, out = play(scripted_client.SCENARIOS["scenario2"] + [scripted_client.SCENARIO2_WRITE])
        self.assertEqual([(t, a.get("query") or a.get("block_id") or a.get("block_ids") or a.get("title"))
                          for _, t, a in calls], [
            ("memory_read", "客户B 折扣"), ("memory_read", "b-602"), ("memory_read", "b-603"),
            ("memory_read", "b-601"), ("memory_cite", ["b-602", "b-603", "b-601"]), ("memory_write", "客户 B 2027 年续约")])
        self.assertTrue(all(k == "key-zhou-local" for k, _, _ in calls))
        self.assertIn("pending", out)

    def test_scenario1_cloud_refusal_is_red(self):
        _, out = play(scripted_client.SCENARIOS["scenario1"])
        self.assertIn("EXPOSURE_INTERNAL", out)
        self.assertIn(scripted_client.RED, out)

    def test_answers_cite_only_what_was_read_and_cited(self):
        """预写的回答里的出处，必须是这一幕读过、并且提交了引用的记忆。"""
        for name, acts in scripted_client.SCENARIOS.items():
            for agent, question, steps, answer in acts:
                tags = set(re.findall(r"\[(b-\d+)\]", answer))
                read = {a["block_id"] for t, a in steps if t == "memory_read"}
                cited = {b for t, a in steps if t == "memory_cite" for b in a["block_ids"]}
                with self.subTest(scenario=name, question=question):
                    self.assertLessEqual(tags, read & cited)


if __name__ == "__main__":
    unittest.main()
