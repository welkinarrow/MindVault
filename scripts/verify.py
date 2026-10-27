"""Result verification for the demo (HW6 plan, Section 5, Check 2).

Run it right after the finance agent has finished the demo task, with the
service still running (this terminal also needs .venv activated):
    python scripts/verify.py

Checks:
  1. The finance agent's new block is level L2, scope finance, status unverified.
  2. Every rating in that session is for a block the agent actually read
     (cross-checked against the READ ... ALLOW lines in logs/server.log).
  3. The conclusion is correct: it matches what b-101, b-207 and b-318 say.
  4. Rating b-412 with the finance key (never read) is refused: NOT_READ_IN_SESSION.
  5. Reading the new block with the intern key is denied: LEVEL_TOO_LOW.

Checks 1-3 read the database read-only (interfaces.md Section 7);
checks 4-5 call the service over MCP like a real agent.
Prints PASS or FAIL per item; exit code 0 only if everything passes.
"""

import asyncio
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from scripts.selftest import Agent
except ModuleNotFoundError as e:
    sys.exit(f"Missing dependency {e.name}. Activate the virtual env first: source .venv/bin/activate")

from server.config import Config, ConfigError, load_agents  # noqa: E402

LOG_FILE = ROOT / "logs" / "server.log"

# What the evidence blocks support (b-101 refund rise, b-207 payment complaints,
# b-318 the Sep 12 v5.4 payment-module release). Each group needs one match.
EVIDENCE_TERMS = {
    "refund rise (b-101)": ("refund",),
    "payment problem (b-207)": ("payment", "charge", "charged"),
    "September release (b-318)": ("5.4", "release", "sep 12", "september 12"),
}

failures = 0


def check(ok: bool, message: str, detail: object = None) -> bool:
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {message}")
    if not ok:
        failures += 1
        if detail is not None:
            print(f"        got: {str(detail)[:300]}")
    return ok


def reads_by_session(agent_id: str) -> dict[str, set[str]]:
    """Block IDs each session of agent_id successfully read, from the service log.

    Only the log since the last [START] counts; sessions are cleared on restart.
    """
    if not LOG_FILE.exists():
        return {}
    lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if "[START]" in line]
    lines = lines[starts[-1]:] if starts else lines

    sessions: dict[str, set[str]] = {}
    current = None
    for line in lines:
        m = re.search(rf"\[SESSION\]\s+{agent_id}\s+new session=(\S+)", line)
        if m:
            current = m.group(1)
            sessions[current] = set()
            continue
        m = re.search(rf"\[READ\]\s+{agent_id}\s+(\S+)\s+\S+\s+ALLOW", line)
        if m and current:
            sessions[current].add(m.group(1))
    return sessions


def check_database(db_path: Path) -> str | None:
    """Checks 1-3. Returns the new block's ID, or None if there is none."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        block = conn.execute(
            "SELECT * FROM blocks WHERE author = 'finance' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if block is None:
            check(False, "The finance agent wrote a new block")
            return None

        print(f"  New block: {block['block_id']}  \"{block['title']}\"")
        check(block["level"] == 2 and block["scope"] == "finance" and block["status"] == "unverified",
              f"1. New block is L{block['level']} / {block['scope']} / {block['status']}"
              " (expected L2 / finance / unverified)")

        rows = conn.execute(
            "SELECT session_id, block_id FROM ratings WHERE agent_id = 'finance' ORDER BY id"
        ).fetchall()
        if not rows:
            check(False, "2. The finance agent rated the blocks it used")
        else:
            session_id = rows[-1]["session_id"]
            rated = {r["block_id"] for r in rows if r["session_id"] == session_id}
            read = reads_by_session("finance").get(session_id)
            if read is None:
                check(False, f"2. Found the reads of session {session_id} in logs/server.log"
                             " (restart the service before each run)")
            else:
                check(rated <= read,
                      f"2. Rated {sorted(rated)}, all read in session {session_id}",
                      f"rated but not read: {sorted(rated - read)}")

        text = f"{block['title']} {block['content']}".lower()
        missing = [name for name, terms in EVIDENCE_TERMS.items()
                   if not any(t in text for t in terms)]
        check(not missing, "3. Conclusion matches the evidence: refund rise, payment problem, Sep 12 release",
              f"missing: {missing}")
        return block["block_id"]
    finally:
        conn.close()


async def check_service(url: str, keys: dict, new_id: str | None) -> None:
    """Checks 4-5, through MCP like a real agent."""
    try:
        async with Agent(url, keys["finance"]) as finance:
            r = await finance.call("memory_rate", {"ratings": [
                {"block_id": "b-412", "accuracy": 5, "reason": "verify.py: never read"}]})
            code = (r.get("results") or [{}])[0].get("reason_code")
            check(code == "NOT_READ_IN_SESSION", "4. Finance rating b-412 without reading it is refused", r)
    except Exception as e:
        check(False, f"4. Finance key call failed: {type(e).__name__}: {e}")

    if new_id is None:
        check(False, "5. No new block to test the intern against")
        return
    try:
        async with Agent(url, keys["intern"]) as intern:
            r = await intern.call("memory_read", {"block_id": new_id})
            check(r.get("reason_code") == "LEVEL_TOO_LOW", f"5. Intern reading {new_id} is denied: LEVEL_TOO_LOW", r)
    except Exception as e:
        check(False, f"5. Intern key call failed: {type(e).__name__}: {e}")


async def main() -> int:
    try:
        cfg = Config.from_env()
        keys = {a["agent_id"]: a["key"] for a in load_agents(cfg.agents_file)}
    except ConfigError as e:
        sys.exit(f"Config error: {e}")
    url = f"http://{cfg.host}:{cfg.port}/mcp"
    print(f"Verifying the demo run against {url}\n")

    new_id = check_database(cfg.db_path)
    await check_service(url, keys, new_id)

    print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILED'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
