"""HW7 benchmark harness (owner: C).

For each condition x case x run:
  reset.py (the case's seed file) -> start the service with LABEL_MODE and RUN_ID
  -> agents/run.sh with the matching profile -> intern probe (bench/probe.py)
  -> stop the service -> copy this run's logs and the database to bench/results/<run_id>/

Usage (from the repo root, .venv active, service NOT running):
    python bench/run_bench.py --tag pilot --runs 1            # pilot: 10 cases x 2 conditions x 1
    python bench/run_bench.py --tag full --runs 3             # full: 60 runs
    python bench/run_bench.py --tag fresh --cases bench/fresh/cases.json --runs 3
    python bench/run_bench.py --tag t --only c01,c06 --conditions self --runs 1
    SEARCH_MODE=titles python bench/run_bench.py --tag retest --conditions taint --runs 3
                                                              # HW7 change (interfaces v0.9)

Each run gets a fresh database and a fresh service, because sessions (and so taint)
only end after 30 minutes idle. Every run adds one row to bench/results/runs.csv;
bench/score.py turns the run folders into results.csv.

Needs: the Hermes profiles finance, finance-self and intern (agents/README.md) and
MINDVAULT_ENV (default ~/.config/mindvault.env). Keys are read from that file and
passed to the service process only; nothing here prints or saves them.
"""

import argparse
import csv
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "bench" / "results"
LOGS = ROOT / "logs"
LOG_FILES = ("server.log", "events.jsonl", "review_usage.jsonl")
PYTHON = str(ROOT / ".venv" / "bin" / "python")
PROFILES = {"taint": "finance", "self": "finance-self"}
FROZEN_FILES = ("bench/cases.json", "bench/fresh/cases.json", "bench/answer_key.json")
AGENT_TIMEOUT = 600      # seconds for one Hermes run
START_TIMEOUT = 30       # seconds for the service to open its port
CSV_FIELDS = ("run_id", "tag", "condition", "case", "run", "profile", "agent_exit", "wall_time_s",
              "tool_calls", "searches", "reads_by_id", "writes", "rate_calls", "new_blocks",
              "probe", "checks_commit", "code_commit", "started_at")


def load_env() -> dict[str, str]:
    """The shell environment plus everything MINDVAULT_ENV exports."""
    env_file = os.environ.get("MINDVAULT_ENV", str(Path.home() / ".config" / "mindvault.env"))
    if not Path(env_file).exists():
        sys.exit(f"Env file not found: {env_file} (set MINDVAULT_ENV)")
    out = subprocess.run(["bash", "-c", 'set -a; source "$1"; env -0', "_", env_file],
                         capture_output=True, check=True).stdout
    env = dict(item.split("=", 1) for item in out.decode().split("\0") if "=" in item)
    env.setdefault("MEMORY_LANG", "en")
    return env


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def port_open(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def log_sizes() -> dict[str, int]:
    return {name: (LOGS / name).stat().st_size if (LOGS / name).exists() else 0 for name in LOG_FILES}


def copy_new_log_lines(before: dict[str, int], out: Path) -> None:
    """Copy only what this run appended to each log file."""
    for name, offset in before.items():
        src = LOGS / name
        if src.exists():
            with open(src, "rb") as f:
                f.seek(offset)
                (out / name).write_bytes(f.read())


def count_tool_calls(events_file: Path) -> dict[str, int]:
    """Finance-agent tool calls, from this run's events.jsonl (the probe runs as intern)."""
    counts: Counter = Counter()
    rate_times = set()
    if events_file.exists():
        for line in events_file.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            if e.get("agent") != "finance":
                continue
            if e["event"] == "READ" and e.get("via") != "id":
                continue  # a search logs one READ per hit; the SEARCH line is the tool call
            counts[e["event"]] += 1
            if e["event"] == "RATE":
                rate_times.add(e["time"][:19])  # one memory_rate call logs one line per rating
    return {"searches": counts["SEARCH"], "reads_by_id": counts["READ"], "writes": counts["WRITE"],
            "rate_calls": len(rate_times),
            "tool_calls": counts["SEARCH"] + counts["READ"] + counts["WRITE"] + len(rate_times)}


def run_one(case: dict, condition: str, n: int, tag: str, env: dict, host: str, port: int,
            commits: dict) -> dict:
    run_id = f"{tag}-{condition}-{case['id']}-r{n}"
    out = RESULTS / run_id
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    run_env = {**env, "SEED_FILE": case["seed_file"], "LABEL_MODE": condition, "RUN_ID": run_id}
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    subprocess.run([PYTHON, "scripts/reset.py"], cwd=ROOT, env=run_env, check=True,
                   stdout=subprocess.DEVNULL)
    before = log_sizes()
    service = subprocess.Popen([PYTHON, "-m", "server.app"], cwd=ROOT, env=run_env,
                               stdout=subprocess.DEVNULL, stderr=open(out / "service.stderr", "w"))
    try:
        deadline = time.time() + START_TIMEOUT
        while not port_open(host, port):
            if service.poll() is not None or time.time() > deadline:
                raise RuntimeError(f"service did not start, see {out / 'service.stderr'}")
            time.sleep(0.3)

        profile = PROFILES[condition]
        t0 = time.monotonic()
        with open(out / "transcript.txt", "w") as transcript:
            try:
                agent = subprocess.run(["agents/run.sh", profile, case["question"]], cwd=ROOT,
                                       env=run_env, stdout=transcript, stderr=subprocess.STDOUT,
                                       timeout=AGENT_TIMEOUT)
                agent_exit = agent.returncode
            except subprocess.TimeoutExpired:
                agent_exit = "timeout"
        wall = round(time.monotonic() - t0, 1)

        probe_script = ROOT / "bench" / "probe.py"
        if probe_script.exists():
            probe = subprocess.run([PYTHON, str(probe_script), "--out", str(out / "probe.json")],
                                   cwd=ROOT, env=run_env, capture_output=True, text=True)
            (out / "probe.log").write_text(probe.stdout + probe.stderr)
            probe_status = "ok" if probe.returncode == 0 else f"exit {probe.returncode}"
        else:
            probe_status = "missing"
    finally:
        service.terminate()
        try:
            service.wait(timeout=10)
        except subprocess.TimeoutExpired:
            service.kill()

    copy_new_log_lines(before, out)
    db = ROOT / run_env.get("MEMORY_DB", "data/memory.db")
    shutil.copy2(db, out / "memory.db")
    new_blocks = len(subprocess.run(
        ["sqlite3", str(out / "memory.db"), "SELECT block_id FROM blocks WHERE block_id LIKE 'w-%'"],
        capture_output=True, text=True).stdout.split())

    row = {"run_id": run_id, "tag": tag, "condition": condition, "case": case["id"], "run": n,
           "profile": profile, "agent_exit": agent_exit, "wall_time_s": wall,
           **count_tool_calls(out / "events.jsonl"), "new_blocks": new_blocks, "probe": probe_status,
           "checks_commit": commits["checks"], "code_commit": commits["code"], "started_at": started_at}
    # search_mode (HW7 change, v0.9; added on A's behalf) goes in meta.json only, so runs.csv keeps its header
    (out / "meta.json").write_text(json.dumps({**row, "search_mode": run_env.get("SEARCH_MODE", "full"),
                                               "case_def": case}, indent=2) + "\n")
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True, help="prefix for run ids, e.g. pilot, full, fresh")
    ap.add_argument("--cases", default="bench/cases.json")
    ap.add_argument("--conditions", nargs="+", default=["taint", "self"], choices=sorted(PROFILES))
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", help="comma-separated case ids")
    args = ap.parse_args()

    cases = json.loads((ROOT / args.cases).read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    env = load_env()
    host, port = env.get("MEMORY_HOST", "127.0.0.1"), int(env.get("MEMORY_PORT", "8000"))
    if port_open(host, port):
        sys.exit(f"Something is already listening on {host}:{port}. Stop the service first.")

    if git("status", "--porcelain", "--", *FROZEN_FILES):
        print("WARNING: cases or answer key have uncommitted changes; checks_commit will not match them.")
    commits = {"checks": git("log", "-1", "--format=%h", "--", *FROZEN_FILES), "code": git("rev-parse", "--short", "HEAD")}

    RESULTS.mkdir(parents=True, exist_ok=True)
    index = RESULTS / "runs.csv"
    new_index = not index.exists()
    total = len(cases) * len(args.conditions) * args.runs
    print(f"{total} runs  tag={args.tag}  checks={commits['checks']}  code={commits['code']}  "
          f"search_mode={env.get('SEARCH_MODE', 'full')}")

    done = 0
    with open(index, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new_index:
            writer.writeheader()
        # runs outermost, so a stopped job still covers every case and condition evenly
        for n in range(1, args.runs + 1):
            for case in cases:
                for condition in args.conditions:
                    done += 1
                    try:
                        row = run_one(case, condition, n, args.tag, env, host, port, commits)
                    except Exception as e:  # keep going; one broken run should not stop the night
                        print(f"[{done}/{total}] {args.tag}-{condition}-{case['id']}-r{n}  ERROR {e}")
                        continue
                    writer.writerow(row)
                    f.flush()
                    print(f"[{done}/{total}] {row['run_id']}  {row['wall_time_s']}s  "
                          f"calls={row['tool_calls']}  writes={row['writes']}  probe={row['probe']}"
                          f"  exit={row['agent_exit']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
