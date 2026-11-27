"""scripts/local_llm.py 的单元测试。用一个假的 Ollama 服务，不需要真的装模型。

运行（在仓库根目录）：
    python -m unittest tests.test_local_llm -v
"""

import io
import json
import os
import socket
import sys
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import local_llm  # noqa: E402


class FakeOllama:
    """按顺序返回预设的 /api/chat 回复，并记下收到的请求。"""

    def __init__(self):
        self.replies = []      # 每项是 message dict
        self.requests = []
        self.models = ["mv-qwen3:latest", "qwen3:8b"]
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, obj, code=200):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/version":
                    self._send({"version": "0.40.18"})
                elif self.path == "/api/tags":
                    self._send({"models": [{"name": n} for n in fake.models]})
                else:
                    self._send({"error": "not found"}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(body)
                if not fake.replies:
                    self._send({"error": "no reply queued"}, 500)
                    return
                self._send({"message": fake.replies.pop(0)})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalLLMTestCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOllama()
        self._env = {k: os.environ.get(k) for k in ("LOCAL_LLM_URL", "LOCAL_LLM_MODEL", "LOCAL_LLM_TIMEOUT")}
        os.environ["LOCAL_LLM_URL"] = self.fake.url
        os.environ.pop("LOCAL_LLM_MODEL", None)
        os.environ["LOCAL_LLM_TIMEOUT"] = "5"

    def tearDown(self):
        self.fake.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestChat(LocalLLMTestCase):
    def test_request_shape(self):
        self.fake.replies.append({"role": "assistant", "content": "你好"})
        reply = local_llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(reply.content, "你好")
        sent = self.fake.requests[0]
        self.assertEqual(sent["model"], "mv-qwen3")
        self.assertIs(sent["think"], False)
        self.assertIs(sent["stream"], False)
        self.assertEqual(sent["keep_alive"], "24h")
        self.assertEqual(sent["options"]["temperature"], 0.0)
        self.assertNotIn("tools", sent)
        self.assertNotIn("format", sent)

    def test_tool_calls_dict_and_string_arguments(self):
        self.fake.replies.append({"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "memory_search", "arguments": {"query": "客户 B"}}},
            {"function": {"name": "memory_read", "arguments": "{\"block_id\": \"b-602\"}"}},
            {"function": {"name": "memory_cite", "arguments": "not json"}},
        ]})
        reply = local_llm.chat([{"role": "user", "content": "q"}], tools=[{"type": "function"}])
        self.assertEqual(reply.tool_calls[0], ("memory_search", {"query": "客户 B"}))
        self.assertEqual(reply.tool_calls[1], ("memory_read", {"block_id": "b-602"}))
        self.assertEqual(reply.tool_calls[2][0], "memory_cite")
        self.assertIn("_unparsed", reply.tool_calls[2][1])
        self.assertEqual(self.fake.requests[0]["tools"], [{"type": "function"}])
        self.assertEqual(reply.message["tool_calls"][0]["function"]["name"], "memory_search")

    def test_tool_message(self):
        self.assertEqual(local_llm.tool_message("memory_read", {"a": "中"}),
                         {"role": "tool", "tool_name": "memory_read", "content": "{\"a\": \"中\"}"})
        self.assertEqual(local_llm.tool_message("x", "plain")["content"], "plain")

    def test_connection_error(self):
        os.environ["LOCAL_LLM_URL"] = f"http://127.0.0.1:{free_port()}"
        with self.assertRaises(local_llm.LocalLLMError):
            local_llm.chat([{"role": "user", "content": "hi"}])

    def test_http_error(self):
        with self.assertRaises(local_llm.LocalLLMError):  # 没有预设回复时假服务返回 500
            local_llm.chat([{"role": "user", "content": "hi"}])


class TestChatJson(LocalLLMTestCase):
    def test_first_try(self):
        self.fake.replies.append({"content": '{"keep": true, "reason": "有用"}'})
        result = local_llm.chat_json([{"role": "user", "content": "q"}])
        self.assertTrue(result.ok)
        self.assertEqual(result.value, {"keep": True, "reason": "有用"})
        self.assertEqual(result.attempts, 1)
        self.assertEqual(self.fake.requests[0]["format"], "json")

    def test_schema_is_passed_as_format(self):
        schema = {"type": "object", "properties": {"keep": {"type": "boolean"}}}
        self.fake.replies.append({"content": '{"keep": false}'})
        local_llm.chat_json([{"role": "user", "content": "q"}], schema=schema)
        self.assertEqual(self.fake.requests[0]["format"], schema)

    def test_code_fence_is_stripped(self):
        self.fake.replies.append({"content": '```json\n{"keep": true}\n```'})
        self.assertEqual(local_llm.chat_json([{"role": "user", "content": "q"}]).value, {"keep": True})

    def test_retry_after_invalid_json(self):
        self.fake.replies += [{"content": "好的，我认为应该保存"}, {"content": '{"keep": true}'}]
        result = local_llm.chat_json([{"role": "user", "content": "q"}])
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 2)
        retry = self.fake.requests[1]["messages"]
        self.assertEqual(retry[-2], {"role": "assistant", "content": "好的，我认为应该保存"})
        self.assertIn("JSON", retry[-1]["content"])

    def test_validate_failure_triggers_retry(self):
        def validate(obj):
            if not isinstance(obj.get("keep"), bool):
                raise ValueError("keep 必须是 true 或 false")
            return obj["keep"]

        self.fake.replies += [{"content": '{"keep": "yes"}'}, {"content": '{"keep": false}'}]
        result = local_llm.chat_json([{"role": "user", "content": "q"}], validate=validate)
        self.assertTrue(result.ok)
        self.assertIs(result.value, False)
        self.assertIn("keep 必须是", self.fake.requests[1]["messages"][-1]["content"])

    def test_gives_up_without_raising(self):
        self.fake.replies += [{"content": "不是 JSON"}, {"content": "还不是"}]
        result = local_llm.chat_json([{"role": "user", "content": "q"}], retries=1)
        self.assertFalse(result.ok)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.raw, "还不是")
        self.assertIn("JSONDecodeError", result.error)

    def test_connection_error_is_not_raised(self):
        os.environ["LOCAL_LLM_URL"] = f"http://127.0.0.1:{free_port()}"
        result = local_llm.chat_json([{"role": "user", "content": "q"}])
        self.assertFalse(result.ok)
        self.assertIn("连不上", result.error)


class TestCheck(LocalLLMTestCase):
    def test_model_ready(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertTrue(local_llm.check())
        self.assertIn("已就绪", out.getvalue())

    def test_model_missing(self):
        self.fake.models = ["qwen3:8b"]
        with redirect_stdout(io.StringIO()) as out:
            self.assertFalse(local_llm.check())
        self.assertIn("local_model_up.sh", out.getvalue())

    def test_warmup(self):
        self.fake.replies += [{"content": "OK"}, {"content": "OK"}]
        with redirect_stdout(io.StringIO()) as out:
            self.assertTrue(local_llm.warmup())
        self.assertIn("已载入内存", out.getvalue())


if __name__ == "__main__":
    unittest.main()
