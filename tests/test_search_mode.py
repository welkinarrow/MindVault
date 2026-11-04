"""HW7 change (interfaces v0.9): SEARCH_MODE=titles.

Written on A's behalf by Claude on 2026-11-03. In titles mode a keyword search lists
titles/levels/scopes only, without content, and does not raise the session taint or count
as "read". Content comes only from reading by block_id, which taints as before.

Run (repo root):
    python -m unittest tests.test_search_mode -v
"""

import os
import unittest
from unittest import mock

from server.config import Config, ConfigError
from tests.test_service import AGENTS, ServiceTestCase

QUERY = "Q3 华东区 退款率 上升"   # the test fixture data is Chinese; this pulls L1/L2 blocks


class TitlesModeCase(ServiceTestCase):
    SEARCH_MODE = "titles"

    def make_service(self, lang="zh"):
        from server.service import MemoryService
        return MemoryService(self.store, self.sessions, AGENTS, self.review, lang=lang,
                             search_mode=self.SEARCH_MODE)


class TestTitlesMode(TitlesModeCase):
    def test_search_returns_no_content(self):
        r = self.svc.read("finance", query=QUERY)
        self.assertTrue(r["results"])
        for b in r["results"]:
            self.assertNotIn("content", b)
            self.assertEqual(set(b), {"block_id", "title", "level", "scope", "status", "author"})
        self.assertIn("search_notice", r)

    def test_search_does_not_raise_taint(self):
        r = self.svc.read("finance", query=QUERY)
        self.assertTrue(any(b["level"] >= 1 for b in r["results"]))  # L1+ titles were listed
        w = self.svc.write("finance", "t", "c")
        self.assertEqual(w["level"], 0)

    def test_read_by_id_still_taints(self):
        self.svc.read("finance", query=QUERY)
        self.svc.read("finance", block_id="b-101")
        self.assertEqual(self.svc.write("finance", "t", "c")["level"], 2)

    def test_listed_but_unread_block_cannot_be_rated(self):
        self.svc.read("finance", query=QUERY)
        r = self.svc.rate("finance", [{"block_id": "b-101", "accuracy": 5, "reason": "ok"}])
        self.assertEqual(r["results"][0]["reason_code"], "NOT_READ_IN_SESSION")

    def test_permissions_unchanged(self):
        ids = {b["block_id"] for b in self.svc.read("finance", query=QUERY)["results"]}
        self.assertNotIn("b-412", ids)          # L3 still filtered out of the listing
        self.assertEqual(self.svc.read("finance", block_id="b-412")["reason_code"], "LEVEL_TOO_LOW")


class TestFullModeUnchanged(TitlesModeCase):
    SEARCH_MODE = "full"

    def test_default_search_still_returns_content_and_taints(self):
        r = self.svc.read("finance", query=QUERY)
        self.assertTrue(all("content" in b for b in r["results"]))
        self.assertGreaterEqual(self.svc.write("finance", "t", "c")["level"], 1)


class TestConfigSearchMode(unittest.TestCase):
    def test_default_full(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Config.from_env().search_mode, "full")

    def test_titles(self):
        with mock.patch.dict(os.environ, {"SEARCH_MODE": "titles"}, clear=True):
            self.assertEqual(Config.from_env().search_mode, "titles")

    def test_invalid(self):
        with mock.patch.dict(os.environ, {"SEARCH_MODE": "preview"}, clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()


if __name__ == "__main__":
    unittest.main()
