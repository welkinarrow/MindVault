"""HW7 intern probe (owner: A; written on A's behalf by Claude on 2026-11-03, see PR #5).

Contract (from C's PR #3): run_bench.py calls
    python bench/probe.py --out <run_dir>/probe.json
after the finance agent finishes and while the service is still running. The
environment has RUN_ID, LABEL_MODE and SEED_FILE, plus the usual MEMORY_* / AGENTS_FILE.

What the probe does (bench/answer_key.json, "intern_probe"):
  1. Lists every w- block in the database (read-only), i.e. every write stored in this
     run, including quarantined ones. Rejected writes are never stored, so they cannot leak.
  2. Reads each of them by block_id with the INTERN key (L0, read_scopes public + finance
     since interfaces v0.8).
  3. Runs the three fixed intern queries.
  4. Saves every raw tool response to probe.json. It does NOT decide leaks; score.py does,
     so the decision rule can be re-applied to old runs without re-running agents.

The probe is a plain script, not an LLM, so the leak check does not depend on what any
agent claims. It prints no keys.

Exit code: 0 if every call completed (denials are normal results), 1 on a connection or
tool error (run_bench.py records probe="exit 1").
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.selftest import Agent  # noqa: E402  (MCP client used by selftest/verify)
from server.config import Config, ConfigError, load_agents  # noqa: E402

ANSWER_KEY = ROOT / "bench" / "answer_key.json"
PROBE_AGENT = "intern"


def new_blocks(db_path: Path) -> list[dict]:
    """All w- blocks in the run's database, opened read-only."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT block_id, level, scope, status, author, review_action FROM blocks "
            "WHERE block_id LIKE 'w-%' ORDER BY block_id").fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


async def probe(url: str, key: str, blocks: list[dict], queries: list[str]) -> tuple[dict, list[str]]:
    errors: list[str] = []
    by_id, searches = [], []
    async with Agent(url, key) as intern:
        for b in blocks:
            try:
                resp = await intern.call("memory_read", {"block_id": b["block_id"]})
            except Exception as e:  # keep probing the rest
                resp = None
                errors.append(f"read {b['block_id']}: {type(e).__name__}: {e}")
            by_id.append({**b, "response": resp})
        for q in queries:
            try:
                resp = await intern.call("memory_read", {"query": q})
            except Exception as e:
                resp = None
                errors.append(f"query {q!r}: {type(e).__name__}: {e}")
            searches.append({"query": q, "response": resp})
    return {"reads_by_id": by_id, "searches": searches}, errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="where to write probe.json")
    args = ap.parse_args()

    try:
        cfg = Config.from_env()
        keys = {a["agent_id"]: a["key"] for a in load_agents(cfg.agents_file)}
    except ConfigError as e:
        sys.exit(f"Config error: {e}")
    intern_cfg = next(a for a in load_agents(cfg.agents_file) if a["agent_id"] == PROBE_AGENT)

    key = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))
    queries = key["intern_probe"]["queries"]
    blocks = new_blocks(cfg.db_path)
    url = f"http://{cfg.host}:{cfg.port}/mcp"

    result, errors = asyncio.run(probe(url, keys[PROBE_AGENT], blocks, queries))
    out = {
        "run_id": os.environ.get("RUN_ID"),
        "label_mode": os.environ.get("LABEL_MODE", cfg.label_mode),
        "seed_file": os.environ.get("SEED_FILE"),
        "probed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # the probe's own permissions, so a reader can see the v0.8 scope decision was in effect
        "probe_agent": {"agent_id": PROBE_AGENT, "level": intern_cfg["level"],
                        "read_scopes": intern_cfg["read_scopes"]},
        "answer_key_version": key.get("version"),
        **result,
        "errors": errors,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    readable = sum(1 for r in result["reads_by_id"] if r["response"] and not r["response"].get("denied"))
    print(f"probe: {len(blocks)} new blocks, {readable} readable by {PROBE_AGENT}; "
          f"{len(queries)} queries; {len(errors)} errors; scopes={intern_cfg['read_scopes']}")
    if "finance" not in intern_cfg["read_scopes"]:
        print("WARNING: intern cannot read the finance scope; the v0.8 scope change is not in "
              "config/agents.json, so every finance write is hidden by SCOPE_MISMATCH.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
