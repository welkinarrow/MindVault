"""scripts/local_agent.py 的单元测试。模型和 MCP 服务都用假的，只测 agent 循环本身。

运行（在仓库根目录）：
    python -m unittest tests.test_local_agent -v
"""

import asyncio
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import local_agent  # noqa: E402
from local_llm import LocalLLMError, Reply  # noqa: E402


class FakeMCP:
    """记下调用，按工具名返回预设结果。"""

    def __init__(self, results):
        self.results, self.calls = results, []

    def __call__(self, url, key):  # 代替 Agent(url, key)
        self.key = key
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def call(self, tool, args):
        self.calls.append((tool, args))
        result = self.results.get((tool, tuple(sorted(args))), self.results.get(tool))
        if isinstance(result, Exception):
            raise result
        return result


def tool_reply(name, args):
    return Reply(content="", tool_calls=[(name, args)],
                 message={"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]})


def text_reply(text):
    return Reply(content=text, message={"role": "assistant", "content": text})


B602 = {"results": [{"block_id": "b-602", "level": 1, "scope": "sales", "exposure": "internal", "content": "85 折"}]}


def run(replies, mcp, max_steps=8):
    events = []
    with mock.patch.object(local_agent, "chat", side_effect=replies), \
            mock.patch.object(local_agent, "Agent", mcp), redirect_stdout(io.StringIO()) as out:
        answer = asyncio.run(local_agent.run("zhou-local", "secret-key", "http://x/mcp", "客户 B 为什么是 85 折？",
                                             max_steps, 60, "zh", events.append))
    return answer, events, out.getvalue()


class TestLoop(unittest.TestCase):
    def test_search_read_cite_answer(self):
        mcp = FakeMCP({"memory_read": B602, "memory_cite": {"answer_id": "c-0001", "citations": ["b-602"]}})
        answer, events, out = run([
            tool_reply("memory_search", {"query": "客户B 折扣"}),
            tool_reply("memory_read", {"block_id": "b-602"}),
            tool_reply("memory_cite", {"block_ids": ["b-602"], "answer": "补偿折扣 [b-602]"}),
            text_reply("补偿折扣 [b-602]"),
        ], mcp)
        self.assertEqual(answer, "补偿折扣 [b-602]")
        self.assertEqual(mcp.calls, [
            ("memory_read", {"query": "客户B 折扣"}),
            ("memory_read", {"block_id": "b-602"}),
            ("memory_cite", {"block_ids": ["b-602"], "answer": "补偿折扣 [b-602]"}),
        ])
        self.assertNotIn("reminder", [e["event"] for e in events])
        self.assertNotIn("secret-key", out + repr(events))

    def test_reminds_once_when_citation_forgotten(self):
        mcp = FakeMCP({"memory_read": B602})
        replies = [tool_reply("memory_read", {"block_id": "b-602"}), text_reply("先回答"), text_reply("还是没引用")]
        answer, events, _ = run(replies, mcp)
        self.assertEqual(answer, "还是没引用")
        self.assertEqual([e["event"] for e in events].count("reminder"), 1)

    def test_refusal_is_passed_back_to_model(self):
        denied = {"denied": True, "reason_code": "EXPOSURE_INTERNAL", "block_id": "b-802", "message": "内部"}
        mcp = FakeMCP({"memory_read": denied, "memory_cite": {"answer_id": "c-1", "citations": []}})
        replies = [tool_reply("memory_read", {"block_id": "b-802"}),
                   tool_reply("memory_cite", {"block_ids": ["b-801"]}), text_reply("读不到部署手册")]
        seen = []

        def fake_chat(messages, **kwargs):
            seen.append(list(messages))  # 复制一份：agent 之后还会往同一个列表里追加
            return replies.pop(0)

        with mock.patch.object(local_agent, "chat", side_effect=fake_chat), \
                mock.patch.object(local_agent, "Agent", mcp), redirect_stdout(io.StringIO()) as out:
            asyncio.run(local_agent.run("wang-cloud", "k", "u", "q", 8, 60, "zh", lambda e: None))
        second_call_messages = seen[1]
        self.assertEqual(second_call_messages[-1]["role"], "tool")
        self.assertIn("EXPOSURE_INTERNAL", second_call_messages[-1]["content"])
        self.assertIn(local_agent.RED, out.getvalue())

    def test_missing_argument_and_unknown_tool_go_back_to_model(self):
        mcp = FakeMCP({})
        replies = [tool_reply("memory_read", {}), tool_reply("memory_delete", {"block_id": "b-1"}), text_reply("x"),
                   text_reply("y")]
        _, events, _ = run(replies, mcp)
        codes = [e["result"]["error"]["code"] for e in events if e["event"] == "tool"]
        self.assertEqual(codes, ["INVALID_INPUT", "UNKNOWN_TOOL"])
        self.assertEqual(mcp.calls, [])

    def test_server_error_becomes_tool_result(self):
        mcp = FakeMCP({"memory_cite": RuntimeError("Unknown tool: memory_cite")})
        replies = [tool_reply("memory_cite", {"block_ids": ["b-1"]}), text_reply("x"), text_reply("y")]
        _, events, _ = run(replies, mcp)
        self.assertEqual(events[0]["result"]["error"]["code"], "RuntimeError")

    def test_retries_model_once(self):
        mcp = FakeMCP({"memory_cite": {"answer_id": "c-1", "citations": ["b-1"]}})
        replies = [LocalLLMError("超时"), tool_reply("memory_cite", {"block_ids": ["b-1"]}), text_reply("好")]
        answer, _, _ = run(replies, mcp)
        self.assertEqual(answer, "好")

    def test_gives_up_after_max_steps(self):
        mcp = FakeMCP({"memory_read": B602})
        replies = [tool_reply("memory_read", {"block_id": "b-602"})] * 3
        with self.assertRaises(RuntimeError):
            run(replies, mcp, max_steps=3)


class TestSummarize(unittest.TestCase):
    def test_lines(self):
        self.assertEqual(local_agent.summarize("memory_read", B602),
                         ("1 条：b-602(L1/sales/internal)", False))
        self.assertTrue(local_agent.summarize("memory_read", {"denied": True, "reason_code": "X"})[1])
        line, refused = local_agent.summarize("memory_write", {"block_id": "w-0012", "status": "pending",
                                                               "level": 1, "scope": "sales", "exposure": "internal"})
        self.assertIn("pending", line)
        self.assertFalse(refused)
        self.assertTrue(local_agent.summarize("memory_write", {"block_id": None, "status": None})[1])


if __name__ == "__main__":
    unittest.main()
