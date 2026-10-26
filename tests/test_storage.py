"""config.py、db.py、sessions.py 的单元测试。

运行（在仓库根目录）：
    python -m unittest tests.test_storage -v
"""

import json
import os
import re
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from server.config import Config, ConfigError, find_agent, load_agents
from server.db import Store
from server.sessions import SessionStore

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"  # 测试专用数据，与 data/ 下的演示数据无关


def build_db(path: Path) -> None:
    """按 schema.sql 建表并导入测试专用的中文数据（tests/fixtures/seed_blocks_zh.json）。"""
    blocks = json.loads((FIXTURES / "seed_blocks_zh.json").read_text(encoding="utf-8"))
    conn = sqlite3.connect(path)
    conn.executescript((ROOT / "server" / "schema.sql").read_text(encoding="utf-8"))
    conn.executemany(
        "INSERT INTO blocks (block_id, title, content, level, scope, status, author, created_at) "
        "VALUES (:block_id, :title, :content, :level, :scope, :status, :author, '2026-10-25T00:00:00+00:00')",
        blocks,
    )
    conn.commit()
    conn.close()


def write_kwargs(**overrides) -> dict:
    kw = dict(title="结论", content="内容", level=2, scope="finance", status="unverified",
              author="finance", review_action="pass", review_reason="ok")
    kw.update(overrides)
    return kw


# ===================================================================== config


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            c = Config.from_env()
        self.assertEqual((c.host, c.port, c.lang, c.review_timeout, c.session_idle_seconds),
                         ("127.0.0.1", 8000, "zh", 30, 1800))
        self.assertEqual(c.db_path, ROOT / "data" / "memory.db")
        self.assertEqual(c.agents_file, ROOT / "config" / "agents.json")

    def test_overrides(self):
        env = {"MEMORY_PORT": "9000", "MEMORY_LANG": "en", "MEMORY_DB": "/tmp/x.db"}
        with mock.patch.dict(os.environ, env, clear=True):
            c = Config.from_env()
        self.assertEqual((c.port, c.lang, c.db_path), (9000, "en", Path("/tmp/x.db")))

    def test_invalid_values(self):
        for env in ({"MEMORY_LANG": "fr"}, {"MEMORY_PORT": "abc"}, {"REVIEW_TIMEOUT": "1.5"},
                    {"SESSION_IDLE_SECONDS": "0"}, {"SESSION_IDLE_SECONDS": "-5"}):
            with self.subTest(env=env), mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ConfigError):
                    Config.from_env()


class TestAgents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "agents.json"
        self.agents = [
            {"agent_id": "finance", "key": "a" * 32, "level": 2,
             "read_scopes": ["public", "finance"], "write_scope": "finance"},
            {"agent_id": "intern", "key": "b" * 32, "level": 0,
             "read_scopes": ["public"], "write_scope": "public"},
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, agents):
        self.path.write_text(json.dumps({"agents": agents}), encoding="utf-8")

    def test_valid(self):
        self.write(self.agents)
        self.assertEqual(len(load_agents(self.path)), 2)

    def test_missing_file(self):
        with self.assertRaisesRegex(ConfigError, "gen_keys"):
            load_agents(self.path)

    def test_template_key_rejected(self):
        # 直接把 agents.example.json 当成 agents.json 用的情况
        self.agents[0]["key"] = "<run scripts/gen_keys.py>"
        self.write(self.agents)
        with self.assertRaisesRegex(ConfigError, "gen_keys"):
            load_agents(self.path)

    def test_invalid_entries(self):
        cases = {
            "重复密钥": lambda a: a[1].update(key="a" * 32),
            "重复 ID": lambda a: a[1].update(agent_id="finance"),
            "级别为 bool": lambda a: a[0].update(level=True),
            "非法范围": lambda a: a[0].update(read_scopes=["hr"]),
            "空范围": lambda a: a[0].update(read_scopes=[]),
            "非法可写范围": lambda a: a[0].update(write_scope="hr"),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                agents = json.loads(json.dumps(self.agents))
                mutate(agents)
                self.write(agents)
                with self.assertRaises(ConfigError):
                    load_agents(self.path)

    def test_find_agent(self):
        found = find_agent(self.agents, "a" * 32)
        self.assertEqual(found["agent_id"], "finance")
        self.assertNotIn("key", found, "返回的 agent 不能带密钥")
        for bad in (None, "", "c" * 32, "a" * 31):
            with self.subTest(key=bad):
                self.assertIsNone(find_agent(self.agents, bad))

    def test_real_example_template_is_rejected(self):
        with self.assertRaises(ConfigError):
            load_agents(ROOT / "config" / "agents.example.json")


# ===================================================================== db


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "memory.db"
        build_db(self.db_path)
        self.store = Store(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_db(self):
        with self.assertRaisesRegex(FileNotFoundError, "reset.py"):
            Store(Path(self.tmp.name) / "nope.db")

    def test_all_blocks(self):
        blocks = self.store.all_blocks()
        self.assertEqual(len(blocks), 10)
        self.assertEqual(blocks[0]["block_id"], "b-001")
        self.assertIs(type(blocks[0]["level"]), int)
        self.assertEqual(set(blocks[0]), {"block_id", "title", "content", "level", "scope", "status", "author"})

    def test_get_block(self):
        self.assertEqual(self.store.get_block("b-412")["level"], 3)
        self.assertIsNone(self.store.get_block("b-999"))

    def test_written_block_ids_are_sequential(self):
        self.assertEqual(self.store.insert_written_block(**write_kwargs()), "w-0001")
        self.assertEqual(self.store.insert_written_block(**write_kwargs()), "w-0002")
        # 换一个 Store 实例（相当于服务重启）后继续编号
        self.assertEqual(Store(self.db_path).insert_written_block(**write_kwargs()), "w-0003")

    def test_written_block_fields(self):
        bid = self.store.insert_written_block(**write_kwargs(status="quarantined", review_action="quarantine"))
        b = self.store.get_block(bid)
        self.assertEqual((b["level"], b["scope"], b["status"], b["author"]), (2, "finance", "quarantined", "finance"))
        conn = sqlite3.connect(self.db_path)
        action, created = conn.execute(
            "SELECT review_action, created_at FROM blocks WHERE block_id = ?", (bid,)).fetchone()
        conn.close()
        self.assertEqual(action, "quarantine")
        self.assertRegex(created, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")

    def test_written_block_constraints(self):
        for kw in (write_kwargs(status="rejected"), write_kwargs(level=4), write_kwargs(review_action="reject")):
            with self.subTest(kw=kw), self.assertRaises(sqlite3.IntegrityError):
                self.store.insert_written_block(**kw)
        # 失败的写入不能占用编号
        self.assertEqual(self.store.insert_written_block(**write_kwargs()), "w-0001")

    def test_concurrent_writes_get_unique_ids(self):
        results, errors = [], []

        def worker():
            try:
                results.append(Store(self.db_path).insert_written_block(**write_kwargs()))
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [f"w-{i:04d}" for i in range(1, 21)])

    def test_ratings(self):
        rate = lambda sid, bid="b-101": self.store.insert_rating(
            session_id=sid, agent_id="finance", block_id=bid, accuracy=5, reason="ok")
        self.assertIsNone(rate("s1"))
        self.assertEqual(rate("s1"), "ALREADY_RATED")
        self.assertIsNone(rate("s2"))
        with self.assertRaises(sqlite3.IntegrityError):
            rate("s1", bid="b-999")  # 外键：block 不存在

    def test_rating_out_of_range_raises(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.insert_rating(session_id="s", agent_id="finance", block_id="b-101", accuracy=6, reason="x")


# ===================================================================== sessions


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestSessions(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = SessionStore(idle_seconds=100, clock=self.clock)

    def test_new_session_defaults(self):
        s, created = self.store.current("finance")
        self.assertTrue(created)
        self.assertTrue(s.session_id.startswith("finance-"))
        self.assertEqual((self.store.max_level(s), s.read_block_ids), (0, set()))

    def test_same_session_while_active(self):
        s1, _ = self.store.current("finance")
        self.clock.now += 99
        s2, created = self.store.current("finance")
        self.assertIs(s1, s2)
        self.assertFalse(created)

    def test_idle_timeout_starts_new_session(self):
        s1, _ = self.store.current("finance")
        self.store.record_read(s1, {"block_id": "b-101", "level": 2})
        self.clock.now += 101
        s2, created = self.store.current("finance")
        self.assertTrue(created)
        self.assertNotEqual(s1.session_id, s2.session_id)
        self.assertEqual(self.store.max_level(s2), 0)
        self.assertFalse(self.store.has_read(s2, "b-101"))

    def test_idle_measured_from_last_activity(self):
        s1, _ = self.store.current("finance")
        for _ in range(5):
            self.clock.now += 90
            s, created = self.store.current("finance")
            self.assertFalse(created)
        self.assertIs(s, s1)

    def test_max_level_only_goes_up(self):
        s, _ = self.store.current("finance")
        for level, bid, expected in ((1, "b-207", 1), (2, "b-101", 2), (0, "b-001", 2), (1, "b-318", 2)):
            self.store.record_read(s, {"block_id": bid, "level": level})
            self.assertEqual(self.store.max_level(s), expected)

    def test_has_read(self):
        s, _ = self.store.current("finance")
        self.store.record_read(s, {"block_id": "b-101", "level": 2})
        self.assertTrue(self.store.has_read(s, "b-101"))
        self.assertFalse(self.store.has_read(s, "b-207"))

    def test_agents_are_isolated(self):
        a, _ = self.store.current("finance")
        b, _ = self.store.current("intern")
        self.store.record_read(a, {"block_id": "b-101", "level": 2})
        self.assertEqual(self.store.max_level(b), 0)
        self.assertNotEqual(a.session_id, b.session_id)

    def test_session_ids_unique(self):
        ids = set()
        for _ in range(50):
            s, _ = self.store.current("finance")
            ids.add(s.session_id)
            self.clock.now += 101
        self.assertEqual(len(ids), 50)

    def test_invalid_idle(self):
        with self.assertRaises(ValueError):
            SessionStore(idle_seconds=0)


if __name__ == "__main__":
    unittest.main()
