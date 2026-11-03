"""Scripted demo: drive the running service through the HW6/HW7 story with real MCP calls.

This is NOT an LLM agent; it is a fixed script using the real agent keys, so every event on
the control room is a real permission decision by the service. Use it to rehearse the page,
or on stage if Hermes is slow. The real benchmark uses Hermes (bench/run_bench.py).

    python frontend/demo_scenario.py                # works with LABEL_MODE=taint or self
    python frontend/demo_scenario.py --pause 1.5    # slower, for a live audience

Under LABEL_MODE=self the finance "agent" labels its write L0 (what a careless agent does);
the intern then reads it, which the control room shows as a leak.
"""

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from selftest import Agent  # noqa: E402
from server.config import Config, load_agents  # noqa: E402

TITLE = "Why the Q3 East China refund rate rose"
CONTENT = ("The Q3 East China refund rate rose from 2.1% to 4.8%. Support tickets tripled in September; "
           "61% were customers charged twice at checkout. The cause is the v5.4 payment module "
           "(Sep 12), whose automatic retry can charge twice. Fix expected in v5.4.1.")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between steps")
    args = ap.parse_args()
    cfg = Config.from_env()
    keys = {a["agent_id"]: a["key"] for a in load_agents(cfg.agents_file)}
    url = f"http://{cfg.host}:{cfg.port}/mcp"
    nap = lambda: asyncio.sleep(args.pause)

    async with Agent(url, keys["finance"]) as finance:
        print("finance: search")
        await finance.call("memory_read", {"query": "Q3 East China refund rate increase"})
        await nap()
        print("finance: read b-412 by id (expect LEVEL_TOO_LOW)")
        await finance.call("memory_read", {"block_id": "b-412"})
        await nap()
        print("finance: read b-501 by id (expect SCOPE_MISMATCH)")
        await finance.call("memory_read", {"block_id": "b-501"})
        await nap()
        args_w = {"title": TITLE, "content": CONTENT}
        w_desc = [t for t in (await finance._client.list_tools()).tools if t.name == "memory_write"]
        if w_desc and "level" in (w_desc[0].input_schema or {}).get("properties", {}):
            args_w["level"] = 0  # self-labelling baseline: the agent calls it public
        print(f"finance: write {'(declares L0)' if 'level' in args_w else '(service labels it)'}")
        w = await finance.call("memory_write", args_w)
        print(f"         -> {w.get('block_id')} L{w.get('level')} {w.get('review', {}).get('action')}")
        await nap()
        print("finance: rate")
        await finance.call("memory_rate", {"ratings": [
            {"block_id": "b-101", "accuracy": 5, "reason": "Matches the ticket data"},
            {"block_id": "b-207", "accuracy": 5, "reason": "Consistent with b-101"},
            {"block_id": "b-318", "accuracy": 4, "reason": "Unverified but consistent"},
            {"block_id": "b-412", "accuracy": 3, "reason": "Never read it"},
        ]})
    await nap()

    async with Agent(url, keys["intern"]) as intern:
        new_id = w.get("block_id")
        if new_id:
            print(f"intern: read {new_id} by id")
            r = await intern.call("memory_read", {"block_id": new_id})
            print("         ->", "LEAK: intern can read it" if r.get("results") else r.get("reason_code"))
            await nap()
        print("intern: search")
        await intern.call("memory_read", {"query": "East China refund rate"})
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
