import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from frontend import server
from server.db import Store
from tests.test_storage import build_db


class TestCorrectionPanel(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.db = Path(temp) / "memory.db"
        build_db(self.db)
        self.store = Store(self.db)
        self.events = Path(temp) / "events.jsonl"
        self.enterContext(patch.multiple(server, DB=self.db, EVENTS=self.events))
        self.enterContext(patch.dict(os.environ, {"FRONTEND_HOST": "127.0.0.1"}))
        self.client = self.enterContext(TestClient(server.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)))

    def write(self, title, content, sources):
        return self.store.insert_written_block(title=title, content=content, level=2, scope="finance",
                                              status="unverified", author="finance", review_action="pass",
                                              review_reason="test fixture", sources=sources)

    def test_latest_saved_correction_and_recorded_impact(self):
        note = self.write("Staffing note", "Remember the older 11% figure.", [])
        followup = self.write("Staffing follow-up", "Old summary.", [note, "b-101"])
        self.store.apply_correction(
            block_id=note, content="Use the current 8% figure.", level=2, corrected_by="finance",
            reason="Removed duplicate departures", review_action="pass", review_reason="Source supports 8%",
            source_versions={"b-101": 0},
        )
        result = self.client.get(f"/api/changes/{note}")
        self.assertEqual(result.status_code, 200)
        change = result.json()
        self.assertEqual(change["before"], "Remember the older 11% figure.")
        self.assertEqual(change["after"], "Use the current 8% figure.")
        self.assertEqual(change["reason"], "Removed duplicate departures")
        self.assertEqual(change["sources"], ["b-101"])
        self.assertEqual(change["affected"], [{"block_id": followup, "stale": True}])
        self.assertEqual(result.headers["cache-control"], "no-store")

        self.store.apply_correction(block_id=followup, content="Refreshed", level=2, corrected_by="finance",
                                    reason="Update", review_action="pass", review_reason="Up to date")
        self.store.apply_correction(block_id="b-101", content="New source revision", level=2, corrected_by="finance",
                                    reason="Later audit", review_action="pass", review_reason="Up to date")
        self.assertEqual(self.client.get(f"/api/changes/{note}").json()["affected"],
                         [{"block_id": followup, "stale": False}])
        self.store.apply_correction(block_id=note, content="Refreshed again", level=2, corrected_by="finance",
                                    reason="New audit", review_action="pass", review_reason="Up to date")
        self.assertEqual(self.client.get(f"/api/changes/{note}").json()["before"], "Use the current 8% figure.")

    def test_history_is_local_and_uses_no_store(self):
        for host, client in (("http://example.test", ("127.0.0.1", 50000)),
                             ("http://127.0.0.1", ("192.0.2.10", 50000))):
            with TestClient(server.app, base_url=host, client=client) as remote:
                self.assertEqual(remote.get("/api/changes/b-101").status_code, 403)
        with patch.dict(os.environ, {"FRONTEND_HOST": "0.0.0.0"}):
            self.assertEqual(self.client.get("/api/changes/b-101").status_code, 403)
        self.assertEqual(self.client.get("/api/changes/b-101", headers={"X-Forwarded-For": "127.0.0.1"}).status_code, 403)

    def test_saved_history_without_event_keeps_unknown_impact(self):
        self.store.apply_correction(block_id="b-101", content="Updated after an audit", level=2,
                                    corrected_by="finance", reason="Audit", review_action="pass",
                                    review_reason="Valid source")
        with self.store._conn() as conn:
            corrected_at = conn.execute("SELECT corrected_at FROM block_history WHERE block_id = 'b-101'").fetchone()[0]
            conn.execute("ALTER TABLE block_history DROP COLUMN flagged_ids")
        result = self.client.get("/api/changes/b-101")
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json()["affected"])
        snapshot = self.client.get("/api/blocks").json()["blocks"]
        self.assertTrue(next(block for block in snapshot if block["block_id"] == "b-101")["corrected"])
        self.assertEqual(self.client.get("/api/changes/b-001").status_code, 404)
        self.events.write_text(json.dumps({"event": "CORRECT", "result": "APPLIED", "block": "b-101",
                                           "agent": "finance", "reason": "Audit", "time": corrected_at,
                                           "flagged": []}) + "\n")
        self.assertEqual(self.client.get("/api/changes/b-101").json()["affected"], [])
        self.store = Store(self.db)
        self.assertEqual(self.client.get("/api/changes/b-101").json()["affected"], [])
