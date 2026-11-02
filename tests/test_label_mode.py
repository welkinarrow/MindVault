"""HW7：LABEL_MODE 开关（I-7）和事件日志 events.jsonl（I-6）的单元测试。

运行（在仓库根目录）：
    python -m unittest tests.test_label_mode -v
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server.config import Config, ConfigError
from server.events import EventLog
from server.policy import label_for_write
from tests.test_service import AGENTS, ServiceTestCase

FIN = {"agent_id": "finance", "level": 2, "read_scopes": ["public", "finance"], "write_scope": "finance"}
BASE_FIELDS = {"time", "event", "agent", "block", "level", "scope", "result", "run_id", "label_mode"}


class TestPolicyLabelMode(unittest.TestCase):
    def test_taint_ignores_requested_level(self):
        self.assertEqual(label_for_write(FIN, 2, "taint", 0), (2, "finance"))
        self.assertEqual(label_for_write(FIN, 0, "taint", 3), (0, "finance"))

    def test_default_mode_is_taint(self):
        self.assertEqual(label_for_write(FIN, 2), (2, "finance"))

    def test_self_uses_requested_level(self):
        for requested in (0, 1, 2, 3):
            with self.subTest(requested=requested):
                self.assertEqual(label_for_write(FIN, 2, "self", requested), (requested, "finance"))

    def test_self_missing_or_invalid_level_is_zero(self):
        for bad in (None, -1, 4, 7, 1.0, "2", True, False):
            with self.subTest(bad=bad):
                self.assertEqual(label_for_write(FIN, 2, "self", bad), (0, "finance"))

    def test_scope_is_write_scope_in_both_modes(self):
        self.assertEqual(label_for_write(FIN, 1, "self", 3)[1], "finance")

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            label_for_write(FIN, 2, "agent", 1)


class TestConfigLabelMode(unittest.TestCase):
    def test_default_taint(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Config.from_env().label_mode, "taint")

    def test_self(self):
        with mock.patch.dict(os.environ, {"LABEL_MODE": "self"}, clear=True):
            self.assertEqual(Config.from_env().label_mode, "self")

    def test_invalid(self):
        with mock.patch.dict(os.environ, {"LABEL_MODE": "Self"}, clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()


class EventsTestCase(ServiceTestCase):
    """在 ServiceTestCase 基础上接入一个写到临时文件的 EventLog。"""

    LABEL_MODE = "taint"

    def make_service(self, lang="zh"):
        from server.service import MemoryService
        self.events_path = Path(self.tmp.name) / "events.jsonl"
        self.events = EventLog(self.events_path, run_id="r-test", label_mode=self.LABEL_MODE)
        return MemoryService(self.store, self.sessions, AGENTS, self.review, lang=lang,
                             label_mode=self.LABEL_MODE, events=self.events)

    def records(self, event=None):
        if not self.events_path.exists():
            return []
        rows = [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines()]
        return [r for r in rows if event is None or r["event"] == event]


class TestServiceTaintMode(EventsTestCase):
    def test_level_argument_is_ignored(self):
        self.svc.read("finance", block_id="b-101")
        r = self.svc.write("finance", "t", "c", level=0)
        self.assertEqual((r["level"], r["level_basis"]), (2, "session_max_read"))

    def test_log_line_unchanged(self):
        self.svc.read("finance", block_id="b-101")
        with self.assertLogs("memory", level="INFO") as cm:
            self.svc.write("finance", "t", "c")
        self.assertIn("level=L2 (session max)  scope=finance", "\n".join(cm.output))


class TestServiceSelfMode(EventsTestCase):
    LABEL_MODE = "self"

    def test_agent_declared_level(self):
        self.svc.read("finance", block_id="b-101")
        r = self.svc.write("finance", "t", "c", level=0)
        self.assertEqual((r["level"], r["scope"], r["level_basis"]), (0, "finance", "agent_declared"))

    def test_missing_level_is_zero(self):
        self.svc.read("finance", block_id="b-101")
        self.assertEqual(self.svc.write("finance", "t", "c")["level"], 0)

    def test_scope_still_blocks_intern_when_underlabeled(self):
        """现状：即使 agent 标成 L0，写入范围仍是 finance，实习助手因 SCOPE_MISMATCH 读不到。

        这意味着两种模式下实习助手都读不到 finance 的写入，HW7 的泄露率和过度限制
        都无法区分两个条件。范围规则待团队决定（见 HW7 讨论），决定后改这个测试。
        """
        self.svc.read("finance", block_id="b-101")
        new_id = self.svc.write("finance", "t", "4.8%", level=0)["block_id"]
        r = self.svc.read("intern", block_id=new_id)
        self.assertEqual(r.get("reason_code"), "SCOPE_MISMATCH")

    def test_log_shows_agent_and_taint_level(self):
        self.svc.read("finance", block_id="b-101")
        with self.assertLogs("memory", level="INFO") as cm:
            self.svc.write("finance", "t", "c", level=1)
        self.assertIn("level=L1 (agent)  taint=L2  scope=finance", "\n".join(cm.output))

    def test_write_event_records_both_levels(self):
        self.svc.read("finance", block_id="b-101")
        self.svc.write("finance", "t", "c", level=0)
        (w,) = self.records("WRITE")
        self.assertEqual((w["level"], w["taint_level"], w["requested_level"]), (0, 2, 0))
        self.assertEqual((w["label_mode"], w["level_basis"]), ("self", "agent_declared"))


class TestEventLog(EventsTestCase):
    def test_every_record_has_base_fields(self):
        self.svc.read("finance", query="Q3 华东区 退款率 上升")
        self.svc.read("intern", block_id="b-101")
        self.svc.write("finance", "t", "c")
        self.svc.rate("finance", [{"block_id": "b-101", "accuracy": 5, "reason": "ok"}])
        rows = self.records()
        self.assertTrue(rows)
        for r in rows:
            self.assertLessEqual(BASE_FIELDS, set(r), r)
            self.assertEqual((r["run_id"], r["label_mode"]), ("r-test", "taint"))

    def test_read_results(self):
        self.svc.read("finance", query="Q3 华东区 退款率 上升")
        self.svc.read("intern", block_id="b-101")
        self.svc.read("finance", block_id="b-999")
        results = {(r["agent"], r["block"], r["result"], r["reason_code"]) for r in self.records("READ")}
        self.assertIn(("finance", "b-101", "ALLOW", None), results)
        self.assertIn(("finance", "b-412", "FILTERED", "LEVEL_TOO_LOW"), results)
        self.assertIn(("intern", "b-101", "DENY", "LEVEL_TOO_LOW"), results)
        self.assertIn(("finance", "b-999", "DENY", "NOT_FOUND"), results)

    def test_search_event_tracks_taint(self):
        self.svc.read("finance", query="Q3 华东区 退款率 上升")
        (s,) = self.records("SEARCH")
        self.assertIn("b-101", s["returned"])
        self.assertIn("b-412", s["filtered"])
        self.assertEqual(s["taint_after"], 2)

    def test_write_and_review_events(self):
        self.svc.read("finance", block_id="b-101")
        self.svc.write("finance", "t", "c")
        (w,) = self.records("WRITE")
        (rv,) = self.records("REVIEW")
        self.assertEqual((w["block"], w["level"], w["scope"], w["result"]), ("w-0001", 2, "finance", "unverified"))
        self.assertEqual((rv["block"], rv["result"]), ("w-0001", "pass"))

    def test_rejected_write_is_still_logged(self):
        """被拒绝的写入不进数据库，评分脚本只能从事件日志看到它。"""
        self.review.result = {"action": "reject", "reason": "注入"}
        self.svc.read("finance", block_id="b-101")
        self.svc.write("finance", "t", "c")
        (w,) = self.records("WRITE")
        (rv,) = self.records("REVIEW")
        self.assertEqual((w["block"], w["result"], w["level"]), (None, "rejected", 2))
        self.assertEqual(rv["result"], "reject")

    def test_rate_events(self):
        self.svc.read("finance", block_id="b-101")
        self.svc.rate("finance", [{"block_id": "b-101", "accuracy": 5, "reason": "ok"},
                                  {"block_id": "b-412", "accuracy": 1, "reason": "x"}])
        got = {(r["block"], r["result"], r["reason_code"]) for r in self.records("RATE")}
        self.assertEqual(got, {("b-101", "ACCEPTED", None), ("b-412", "REJECTED", "NOT_READ_IN_SESSION")})

    def test_no_key_in_events(self):
        self.svc.read("finance", query="Q3 华东区 退款率 上升")
        self.svc.write("finance", "t", "c")
        text = self.events_path.read_text(encoding="utf-8")
        for a in AGENTS:
            self.assertNotIn(a["key"], text)

    def test_null_event_log_writes_nothing(self):
        log = EventLog(None)
        self.assertIsNone(log.emit("READ", agent="finance"))


if __name__ == "__main__":
    unittest.main()
