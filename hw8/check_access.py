"""HW8 access check: finance can read b-701, intern gets LEVEL_TOO_LOW.

Run with the MindVault service up (LABEL_MODE=taint), after
SEED_FILE=hw8/seed_with_pricing.json python scripts/reset.py.
Prints PASS/FAIL per check; exit code 0 only if both pass. Prints no keys.
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


async def main() -> int:
    cfg = Config.from_env()
    keys = {a["agent_id"]: a["key"] for a in load_agents(cfg.agents_file)}
    print("agents:", sorted(keys))
    url = f"http://{cfg.host}:{cfg.port}/mcp"
    ok = True
    for who, expect in (("finance", "allow"), ("intern", "deny")):
        async with Agent(url, keys[who]) as agent:
            resp = await agent.call("memory_read", {"block_id": "b-701"})
        text = json.dumps(resp, ensure_ascii=False)
        denied = "LEVEL_TOO_LOW" in text
        passed = denied if expect == "deny" else ("1995" in text and not denied)
        ok = ok and passed
        print("PASS" if passed else "FAIL", who, "expects", expect, "->", text[:200])
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
