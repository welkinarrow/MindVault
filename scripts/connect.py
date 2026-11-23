"""Print the setup that connects an agent's tool (Claude Code, Codex, Cursor) to MindVault.

    python scripts/connect.py counsel                       # all clients
    python scripts/connect.py counsel --client codex
    python scripts/connect.py counsel --url https://mindvault.example.com/mcp

The output contains the agent's key: give it only to whoever runs that agent.
The URL defaults to MEMORY_HOST/MEMORY_PORT from the environment (127.0.0.1:8000).
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.config import ConfigError, load_agents  # noqa: E402

CLIENTS = ("claude-code", "codex", "cursor")


def default_url() -> str:
    host = os.environ.get("MEMORY_HOST", "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{os.environ.get('MEMORY_PORT', '8000')}/mcp"


def claude_code(url: str, key: str) -> str:
    return ("# Claude Code (tested): run once in the agent's project\n"
            f'claude mcp add --transport http mindvault {url} --header "Authorization: Bearer {key}"\n')


def codex(url: str, key: str) -> str:
    # Tested with codex-cli 0.155.1. Without the approval line, codex exec refuses every MindVault call.
    return ("# Codex (tested): add to ~/.codex/config.toml\n"
            "[mcp_servers.mindvault]\n"
            f'url = "{url}"\n'
            'bearer_token_env_var = "MINDVAULT_KEY"\n'
            'default_tools_approval_mode = "approve"\n'
            "\n# and set the key where Codex runs\n"
            f"export MINDVAULT_KEY={key}\n")


def cursor(url: str, key: str) -> str:
    config = {"mcpServers": {"mindvault": {"url": url, "headers": {"Authorization": f"Bearer {key}"}}}}
    return ("# Cursor (not tested yet): add to ~/.cursor/mcp.json or .cursor/mcp.json in the project\n"
            + json.dumps(config, indent=2) + "\n")


RENDER = {"claude-code": claude_code, "codex": codex, "cursor": cursor}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("agent_id")
    parser.add_argument("--client", choices=CLIENTS + ("all",), default="all")
    parser.add_argument("--url", default=None, help="MindVault's MCP URL as the agent will reach it")
    parser.add_argument("--file", type=Path, default=Path(os.environ.get("AGENTS_FILE", "config/agents.json")))
    args = parser.parse_args()

    path = args.file if args.file.is_absolute() else ROOT / args.file
    try:
        keys = {a["agent_id"]: a["key"] for a in load_agents(path)}
    except ConfigError as e:
        sys.exit(f"error: {e}")
    if args.agent_id not in keys:
        sys.exit(f"error: no agent named {args.agent_id!r} (see scripts/agents.py list)")

    url = args.url or default_url()
    clients = CLIENTS if args.client == "all" else (args.client,)
    print(f"# Connects as agent {args.agent_id!r}. This contains its key: keep it private.\n")
    print("\n".join(RENDER[c](url, keys[args.agent_id]) for c in clients))


if __name__ == "__main__":
    main()
