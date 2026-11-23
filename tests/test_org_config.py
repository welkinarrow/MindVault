"""Product config: scopes come from agents.json, and scripts/agents.py manages agents.

Run (from the repo root):
    python -m unittest tests.test_org_config -v
"""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from server.config import DEFAULT_SCOPES, ConfigError, load_agents, load_scopes
from server.db import Store
from server.policy import denial
from tests.test_storage import build_db

ROOT = Path(__file__).resolve().parent.parent


def agent(aid, key_char, read, write, level=1):
    return {"agent_id": aid, "key": key_char * 32, "level": level, "read_scopes": read, "write_scope": write}


class TempConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "agents.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")


class TestScopes(TempConfig):
    def test_custom_scopes_accepted(self):
        self.write({"scopes": ["public", "legal", "sales-emea"],
                    "agents": [agent("counsel", "a", ["public", "legal"], "legal")]})
        self.assertEqual(load_agents(self.path)[0]["write_scope"], "legal")
        self.assertEqual(load_scopes(self.path), {"public", "legal", "sales-emea"})

    def test_no_scopes_key_keeps_old_defaults(self):
        self.write({"agents": [agent("fin", "a", ["public", "finance"], "finance")]})
        self.assertEqual(load_scopes(self.path), DEFAULT_SCOPES)
        load_agents(self.path)

    def test_missing_file_gives_defaults(self):
        self.assertEqual(load_scopes(self.path), DEFAULT_SCOPES)

    def test_undeclared_scope_rejected(self):
        # a typo in an agent's scopes must not silently create a new scope
        self.write({"scopes": ["public", "legal"],
                    "agents": [agent("counsel", "a", ["public", "lgeal"], "legal")]})
        with self.assertRaisesRegex(ConfigError, "lgeal"):
            load_agents(self.path)

    def test_public_required(self):
        self.write({"scopes": ["legal"], "agents": [agent("counsel", "a", ["legal"], "legal")]})
        with self.assertRaisesRegex(ConfigError, "public"):
            load_agents(self.path)

    def test_bad_scope_names(self):
        for bad in ("Legal", "has space", "", "x" * 33, 7, "semi;colon"):
            with self.subTest(bad=bad):
                self.write({"scopes": ["public", bad], "agents": [agent("a", "a", ["public"], "public")]})
                with self.assertRaises(ConfigError):
                    load_agents(self.path)


class TestDenialForCustomScope(unittest.TestCase):
    def test_unknown_scope_gets_generic_owner(self):
        block = {"block_id": "b-1", "level": 1, "scope": "legal"}
        self.assertIn("the legal owner", denial("SCOPE_MISMATCH", "b-1", block, lang="en")["message"])
        self.assertIn("legal", denial("SCOPE_MISMATCH", "b-1", block, lang="zh")["message"])


class TestStoreAcceptsCustomScopes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "memory.db"

    def tearDown(self):
        self.tmp.cleanup()

    def insert(self, store, scope):
        return store.insert_written_block(title="t", content="c", level=1, scope=scope, status="unverified",
                                          author="counsel", review_action="pass", review_reason="ok")

    def test_new_db_has_no_scope_check(self):
        build_db(self.db)
        self.assertEqual(self.insert(Store(self.db), "legal"), "w-0001")

    def test_old_db_with_scope_check_is_upgraded_without_losing_rows(self):
        old_schema = (ROOT / "server" / "schema.sql").read_text(encoding="utf-8").replace(
            "scope         TEXT NOT NULL,",
            "scope         TEXT NOT NULL CHECK (scope IN ('public', 'finance')),",
        )
        self.assertIn("CHECK (scope IN", old_schema)
        conn = sqlite3.connect(self.db)
        conn.executescript(old_schema)
        conn.execute("INSERT INTO blocks (block_id, title, content, level, scope, status, author, created_at) "
                     "VALUES ('b-1', 't', 'c', 0, 'public', 'verified', 'seed', '2026-11-01T00:00:00+00:00')")
        conn.execute("INSERT INTO ratings (session_id, agent_id, block_id, accuracy, reason, created_at) "
                     "VALUES ('s', 'a', 'b-1', 5, 'r', '2026-11-01T00:00:00+00:00')")
        conn.commit()
        conn.close()
        store = Store(self.db)
        self.assertEqual(self.insert(store, "legal"), "w-0001")
        self.assertEqual(store.get_block("b-1")["title"], "t")
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0], 1)
        conn.close()


class TestAgentsCli(TempConfig):
    def run_cli(self, *args, ok=True):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "agents.py"), "--file", str(self.path), *args],
                           capture_output=True, text=True, cwd=ROOT)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        else:
            self.assertNotEqual(r.returncode, 0, r.stdout)
        return r

    def data(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_fresh_setup(self):
        self.run_cli("add-scope", "legal")
        r = self.run_cli("add", "counsel", "--level", "2", "--read", "public,legal", "--write", "legal")
        d = self.data()
        self.assertEqual(d["scopes"], ["public", "legal"])
        key = d["agents"][0]["key"]
        self.assertIn(key, r.stdout, "the new key is printed once so it can be handed over")
        self.assertEqual(len(load_agents(self.path)), 1)

    def test_list_never_prints_keys(self):
        self.run_cli("add", "bot", "--level", "0", "--read", "public", "--write", "public")
        r = self.run_cli("list")
        self.assertIn("bot", r.stdout)
        self.assertNotIn(self.data()["agents"][0]["key"], r.stdout)

    def test_add_rejects_undeclared_scope_and_leaves_file_alone(self):
        self.run_cli("add", "bot", "--level", "0", "--read", "public", "--write", "public")
        before = self.path.read_text(encoding="utf-8")
        r = self.run_cli("add", "x", "--level", "1", "--read", "public,legal", "--write", "legal", ok=False)
        self.assertIn("legal", r.stderr)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_add_duplicate_rejected(self):
        self.run_cli("add", "bot", "--level", "0", "--read", "public", "--write", "public")
        self.run_cli("add", "bot", "--level", "0", "--read", "public", "--write", "public", ok=False)

    def test_new_key_changes_only_that_agent(self):
        self.run_cli("add", "a1", "--level", "0", "--read", "public", "--write", "public")
        self.run_cli("add", "a2", "--level", "0", "--read", "public", "--write", "public")
        old = {a["agent_id"]: a["key"] for a in self.data()["agents"]}
        self.run_cli("new-key", "a1")
        new = {a["agent_id"]: a["key"] for a in self.data()["agents"]}
        self.assertNotEqual(old["a1"], new["a1"])
        self.assertEqual(old["a2"], new["a2"])

    def test_remove(self):
        self.run_cli("add", "a1", "--level", "0", "--read", "public", "--write", "public")
        self.run_cli("remove", "a1")
        self.assertEqual(self.data()["agents"], [])
        self.run_cli("remove", "a1", ok=False)

    def test_existing_file_without_scopes_keeps_defaults(self):
        self.write({"agents": [agent("fin", "a", ["public", "finance"], "finance")]})
        self.run_cli("add-scope", "legal")
        self.assertEqual(set(self.data()["scopes"]), DEFAULT_SCOPES | {"legal"})
        self.assertEqual(len(load_agents(self.path)), 1)


if __name__ == "__main__":
    unittest.main()
