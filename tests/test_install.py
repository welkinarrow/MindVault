"""First-run setup: scripts/init.py and reset.py --empty.

Run (from the repo root):
    python -m unittest tests.test_install -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from server.config import load_agents
from server.db import Store

ROOT = Path(__file__).resolve().parent.parent


class TestInit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.agents, self.db = d / "cfg" / "agents.json", d / "data" / "memory.db"
        self.env = {**os.environ, "AGENTS_FILE": str(self.agents), "MEMORY_DB": str(self.db)}
        self.env.pop("SEED_FILE", None)

    def tearDown(self):
        self.tmp.cleanup()

    def init(self, *args):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "init.py"), *args],
                           env=self.env, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_fresh_install_has_one_agent_and_empty_memory(self):
        out = self.init()
        agents = load_agents(self.agents)
        self.assertEqual([a["agent_id"] for a in agents], ["assistant"])
        self.assertIn(agents[0]["key"], out, "the key is printed once when created")
        self.assertEqual(Store(self.db).all_blocks(), [])

    def test_rerun_changes_nothing_and_prints_no_keys(self):
        self.init()
        before = (self.agents.read_bytes(), self.db.read_bytes())
        out = self.init()
        self.assertEqual((self.agents.read_bytes(), self.db.read_bytes()), before)
        self.assertNotIn(load_agents(self.agents)[0]["key"], out)

    def test_existing_memory_is_never_replaced(self):
        self.init()
        Store(self.db).insert_written_block(title="t", content="c", level=0, scope="public", status="unverified",
                                            author="assistant", review_action="pass", review_reason="ok")
        self.agents.unlink()  # even if the config is recreated, the memory stays
        self.init()
        self.assertEqual(len(Store(self.db).all_blocks()), 1)

    def test_demo(self):
        self.init("--demo")
        self.assertIn("finance", [a["agent_id"] for a in load_agents(self.agents)])
        seed = json.loads((ROOT / "data" / "seed_blocks.json").read_text(encoding="utf-8"))
        self.assertEqual(len(Store(self.db).all_blocks()), len(seed))


if __name__ == "__main__":
    unittest.main()
