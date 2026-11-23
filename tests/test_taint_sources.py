"""Demo Day: taint survives idle time and restarts; every write records its sources.

Run (from the repo root):
    python -m unittest tests.test_taint_sources -v
"""
import json
import sqlite3

from server.service import MemoryService
from server.sessions import SessionStore
from tests.test_service import AGENTS, ServiceTestCase


class TestTaintPersists(ServiceTestCase):
    def test_taint_kept_after_idle(self):
        # The agent still holds b-101 in its context after 30 min idle, so its write must stay L2.
        self.svc.read("finance", block_id="b-101")
        self.clock.now += 1801
        self.assertEqual(self.svc.write("finance", "t", "c")["level"], 2)

    def test_taint_kept_after_service_restart(self):
        self.svc.read("finance", block_id="b-101")
        restarted = MemoryService(self.store, SessionStore(1800, self.clock), AGENTS, self.review)
        self.assertEqual(restarted.write("finance", "t", "c")["level"], 2)

    def test_admin_reset_clears_taint(self):
        self.svc.read("finance", block_id="b-101")
        self.store.reset_taint("finance")
        self.assertEqual(self.svc.write("finance", "t", "c")["level"], 0)

    def test_taint_is_per_agent(self):
        self.svc.read("finance", block_id="b-101")
        self.assertEqual(self.svc.write("intern", "t", "c")["level"], 0)

    def test_denied_read_adds_no_taint(self):
        self.svc.read("finance", block_id="b-412")  # L3, finance is L2: denied
        self.assertEqual(self.svc.write("finance", "t", "c")["level"], 0)


class TestSources(ServiceTestCase):
    def stored_sources(self, block_id):
        conn = sqlite3.connect(self.store.db_path)
        try:
            (raw,) = conn.execute("SELECT sources FROM blocks WHERE block_id = ?", (block_id,)).fetchone()
        finally:
            conn.close()
        return json.loads(raw)

    def test_write_records_blocks_read_in_order(self):
        self.svc.read("finance", block_id="b-101")
        self.svc.read("finance", block_id="b-001")
        self.svc.read("finance", block_id="b-101")  # repeat read is not duplicated
        r = self.svc.write("finance", "t", "c")
        self.assertEqual(r["sources"], ["b-101", "b-001"])
        self.assertEqual(self.stored_sources(r["block_id"]), ["b-101", "b-001"])
        self.assertEqual(self.store.get_sources(r["block_id"]), ["b-101", "b-001"])

    def test_sources_survive_idle(self):
        self.svc.read("finance", block_id="b-101")
        self.clock.now += 1801
        self.assertEqual(self.svc.write("finance", "t", "c")["sources"], ["b-101"])

    def test_denied_read_not_a_source(self):
        self.svc.read("finance", block_id="b-412")
        self.svc.read("finance", block_id="b-001")
        self.assertEqual(self.svc.write("finance", "t", "c")["sources"], ["b-001"])

    def test_nothing_read_means_no_sources(self):
        r = self.svc.write("finance", "t", "c")
        self.assertEqual(r["sources"], [])
        self.assertEqual(self.store.get_sources(r["block_id"]), [])

    def test_query_hits_are_sources(self):
        r = self.svc.read("finance", query="Q3 华东区 退款率 上升")
        hits = [b["block_id"] for b in r["results"]]
        self.assertEqual(self.svc.write("finance", "t", "c")["sources"], hits)

    def test_seed_blocks_have_no_sources(self):
        self.assertEqual(self.store.get_sources("b-101"), [])

    def test_reset_clears_sources(self):
        self.svc.read("finance", block_id="b-101")
        self.store.reset_taint("finance")
        self.assertEqual(self.svc.write("finance", "t", "c")["sources"], [])

    def test_write_event_has_sources(self):
        self.svc.read("finance", block_id="b-101")
        with self.assertLogs("memory", level="INFO") as cm:
            self.svc.write("finance", "t", "c")
        self.assertTrue(any("sources=b-101" in line for line in cm.output))


class TestTitlesModeSources(ServiceTestCase):
    def make_service(self, lang="zh"):
        return MemoryService(self.store, self.sessions, AGENTS, self.review, lang=lang, search_mode="titles")

    def test_listed_titles_are_not_sources(self):
        self.svc.read("finance", query="Q3 华东区 退款率 上升")
        r = self.svc.write("finance", "t", "c")
        self.assertEqual((r["level"], r["sources"]), (0, []))


class TestOldDatabaseUpgrade(ServiceTestCase):
    def test_store_adds_missing_table_and_column(self):
        from server.db import Store
        conn = sqlite3.connect(self.store.db_path)
        conn.execute("DROP TABLE agent_taint")
        conn.execute("ALTER TABLE blocks DROP COLUMN sources")
        conn.commit()
        conn.close()
        store = Store(self.store.db_path)
        store.add_taint("finance", "b-101", 2)
        self.assertEqual(store.get_taint("finance"), (2, ["b-101"]))
