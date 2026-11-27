"""v2 的配置：模板里的演示 agent、model_location / role 的校验、agents.py 的新选项。

运行（在仓库根目录）：
    python -m unittest tests.test_config_v2 -v
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from server.config import ConfigError, scopes_from, validate_agents

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = json.loads((ROOT / "config" / "agents.example.json").read_text(encoding="utf-8"))

# 两个场景和 scripts/run_tests.py 依赖的 agent（interfaces.md 13.9）
EXPECTED = {
    "wang-cloud": (1, ["public", "rnd"], "rnd", "cloud", "business"),
    "wang-local": (1, ["public", "rnd"], "rnd", "local", "business"),
    "zhou-local": (1, ["public", "sales"], "sales", "local", "business"),
    "manager-local": (0, ["public"], "public", "local", "manager"),
    "manager-cloud": (0, ["public"], "public", "cloud", "manager"),
}


def agent(**extra):
    return {"agent_id": "x", "key": "k" * 32, "level": 1, "read_scopes": ["public"], "write_scope": "public", **extra}


class TestTemplate(unittest.TestCase):
    def test_demo_agents_present(self):
        agents = {a["agent_id"]: a for a in TEMPLATE["agents"]}
        for aid, (level, read, write, loc, role) in EXPECTED.items():
            with self.subTest(agent=aid):
                a = agents[aid]
                self.assertEqual((a["level"], a["read_scopes"], a["write_scope"], a["model_location"], a["role"]),
                                 (level, read, write, loc, role))

    def test_sales_scope_and_valid(self):
        self.assertIn("sales", TEMPLATE["scopes"])
        validate_agents(TEMPLATE["agents"], scopes_from(TEMPLATE), check_keys=False)


class TestValidation(unittest.TestCase):
    def test_missing_fields_are_fine(self):
        validate_agents([agent()], {"public"})

    def test_valid_values(self):
        for loc in ("local", "cloud"):
            for role in ("business", "manager", "admin"):
                validate_agents([agent(model_location=loc, role=role)], {"public"})

    def test_typos_rejected(self):
        for bad in ({"model_location": "lcoal"}, {"model_location": "Local"}, {"model_location": None},
                    {"role": "Business"}, {"role": "superuser"}):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                validate_agents([agent(**bad)], {"public"})


class TestAgentsCli(unittest.TestCase):
    def run_cli(self, path, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "agents.py"), "--file", str(path), *args],
                              capture_output=True, text=True, cwd=ROOT)

    def test_add_with_location_and_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agents.json"
            self.run_cli(path, "add-scope", "sales")
            r = self.run_cli(path, "add", "zhou-local", "--level", "1", "--read", "public,sales", "--write", "sales",
                             "--model-location", "local")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = self.run_cli(path, "add", "mgr", "--level", "0", "--read", "public", "--write", "public",
                             "--model-location", "local", "--role", "manager")
            self.assertEqual(r.returncode, 0, r.stderr)
            agents = {a["agent_id"]: a for a in json.loads(path.read_text(encoding="utf-8"))["agents"]}
            self.assertEqual((agents["zhou-local"]["model_location"], agents["zhou-local"]["role"]), ("local", "business"))
            self.assertEqual((agents["mgr"]["model_location"], agents["mgr"]["role"]), ("local", "manager"))
            listing = self.run_cli(path, "list").stdout
            self.assertIn("local  manager", listing)
            self.assertNotIn(agents["mgr"]["key"], listing)

    def test_defaults_and_bad_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agents.json"
            self.run_cli(path, "add", "a1", "--level", "0", "--read", "public", "--write", "public")
            a = json.loads(path.read_text(encoding="utf-8"))["agents"][0]
            self.assertEqual((a["model_location"], a["role"]), ("cloud", "business"))
            r = self.run_cli(path, "add", "a2", "--level", "0", "--read", "public", "--write", "public",
                             "--model-location", "lcoal")
            self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
