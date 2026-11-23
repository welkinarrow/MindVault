"""scripts/connect.py prints working client setup for an agent.

Run (from the repo root):
    python -m unittest tests.test_connect -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    tomllib = None
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY = "c" * 32


class TestConnect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agents = Path(self.tmp.name) / "agents.json"
        self.agents.write_text(json.dumps({"scopes": ["public"], "agents": [
            {"agent_id": "bot", "key": KEY, "level": 0, "read_scopes": ["public"], "write_scope": "public"}]}))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, ok=True, **env):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "connect.py"), "--file", str(self.agents), *args],
                           capture_output=True, text=True, cwd=ROOT,
                           env={**{k: v for k, v in os.environ.items() if not k.startswith("MEMORY_")}, **env})
        self.assertEqual(r.returncode == 0, ok, r.stderr)
        return r.stdout + r.stderr

    def test_claude_code_command(self):
        out = self.run_cli("bot", "--client", "claude-code")
        self.assertIn(f'claude mcp add --transport http mindvault http://127.0.0.1:8000/mcp '
                      f'--header "Authorization: Bearer {KEY}"', out)

    @unittest.skipIf(tomllib is None, "needs Python 3.11+")
    def test_codex_block_parses_and_auto_approves(self):
        out = self.run_cli("bot", "--client", "codex", "--url", "https://mv.example.com/mcp")
        toml_part = out.split("[mcp_servers.mindvault]", 1)[1].split("\n\n", 1)[0]
        cfg = tomllib.loads("[mcp_servers.mindvault]" + toml_part)["mcp_servers"]["mindvault"]
        self.assertEqual(cfg, {"url": "https://mv.example.com/mcp", "bearer_token_env_var": "MINDVAULT_KEY",
                               "default_tools_approval_mode": "approve"})
        self.assertIn(f"export MINDVAULT_KEY={KEY}", out)

    def test_cursor_json(self):
        out = self.run_cli("bot", "--client", "cursor")
        cfg = json.loads(out[out.index("{"):])
        self.assertEqual(cfg["mcpServers"]["mindvault"]["headers"]["Authorization"], f"Bearer {KEY}")

    def test_url_from_environment(self):
        out = self.run_cli("bot", "--client", "claude-code", MEMORY_HOST="0.0.0.0", MEMORY_PORT="9000")
        self.assertIn("http://127.0.0.1:9000/mcp", out)

    def test_unknown_agent(self):
        self.assertIn("no agent named", self.run_cli("nobody", ok=False))


if __name__ == "__main__":
    unittest.main()
