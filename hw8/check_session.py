"""HW8 session check: does taint survive a reconnect, and is memory_write idempotent?

Run on a fresh service (reset.py, then start server.app with LABEL_MODE=taint).
1. finance writes before reading anything        -> expect level 0
2. finance reads b-701, then disconnects          (simulates a seller crash)
3. finance reconnects and writes                  -> level 2 if sessions are per agent
4. finance writes the same note again             -> a second staging_id means no idempotency
Prints the raw write responses. Prints no keys.
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from selftest import Agent  # noqa: E402
from server.config import Config, load_agents  # noqa: E402

NOTE = {"title": "HW8 session check: deal note",
        "content": "Quoted 2 widgets to the Town buyer, total 3990 cents."}


def show(label: str, resp: dict) -> None:
    print(f"{label}: {json.dumps(resp, ensure_ascii=False)[:300]}")


async def main() -> int:
    cfg = Config.from_env()
    key = {a["agent_id"]: a["key"] for a in load_agents(cfg.agents_file)}["finance"]
    url = f"http://{cfg.host}:{cfg.port}/mcp"

    async with Agent(url, key) as fin:
        show("1 write before any read", await fin.call("memory_write", {
            "title": "HW8 session check: before read", "content": "No sources read yet."}))
        read = await fin.call("memory_read", {"block_id": "b-701"})
        print("2 read b-701:", "ok" if "results" in read else read)

    async with Agent(url, key) as fin:  # new connection = restarted seller
        show("3 write after reconnect", await fin.call("memory_write", NOTE))
        show("4 same write again", await fin.call("memory_write", NOTE))
    return 0


sys.exit(asyncio.run(main()))
