"""MindVault control room: read-only backend for the frontend (owner: B).

Serves one page (frontend/static/) and a small JSON API. It only READS:
  - the SQLite database, opened with mode=ro, so it can never change a block;
  - logs/events.jsonl (I-6), streamed to the page over Server-Sent Events;
  - bench/results/ and frontend/samples/ for replay mode and the HW7 panel.
It makes no permission decisions and never sees an agent key (keys are stripped
from the agent config before anything leaves this process).

Run from the repo root (no extra packages: Starlette and uvicorn already come with mcp):
    python frontend/server.py                 # http://127.0.0.1:8100
    FRONTEND_PORT=9000 python frontend/server.py

Environment: MEMORY_DB, AGENTS_FILE (same defaults as the service), FRONTEND_HOST, FRONTEND_PORT.
"""

import asyncio
import csv
import json
import os
import sqlite3
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
SAMPLES = Path(__file__).resolve().parent / "samples"
RESULTS = ROOT / "bench" / "results"
EVENTS = ROOT / "logs" / "events.jsonl"
USAGE = ROOT / "logs" / "review_usage.jsonl"


def env_path(name: str, default: str) -> Path:
    p = Path(os.environ.get(name, default))
    return p if p.is_absolute() else ROOT / p


DB = env_path("MEMORY_DB", "data/memory.db")
AGENTS_FILE = env_path("AGENTS_FILE", "config/agents.json")
AGENTS_EXAMPLE = ROOT / "config" / "agents.example.json"
DEFAULT_SEED = ROOT / "data" / "seed_blocks.json"


# --------------------------------------------------------------------------- helpers

def read_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # a half-written last line; the stream will pick it up later
    return out


def last_run(events: list[dict]) -> list[dict]:
    """Events since the last START: the service run that is (or was last) live."""
    for i in range(len(events) - 1, -1, -1):
        if events[i].get("event") == "START":
            return events[i:]
    return events


def safe_child(base: Path, name: str) -> Path | None:
    """Resolve base/name and refuse anything that escapes base."""
    p = (base / name).resolve()
    return p if base.resolve() in p.parents or p == base.resolve() else None


def agents_public() -> list[dict]:
    """Agent config with keys removed."""
    src = AGENTS_FILE if AGENTS_FILE.exists() else AGENTS_EXAMPLE
    try:
        agents = json.loads(src.read_text(encoding="utf-8"))["agents"]
    except (OSError, KeyError, json.JSONDecodeError):
        return []
    return [{k: v for k, v in a.items() if k != "key"} for a in agents]


def db_blocks(db: Path) -> list[dict]:
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        blocks = [dict(r) for r in con.execute("SELECT * FROM blocks ORDER BY block_id")]
        ratings = {}
        for r in con.execute("SELECT block_id, agent_id, accuracy, reason FROM ratings"):
            ratings.setdefault(r["block_id"], []).append(dict(r))
    except sqlite3.Error:
        return []
    finally:
        con.close()
    for b in blocks:
        b["ratings"] = ratings.get(b["block_id"], [])
    return blocks


def seed_blocks(seed: Path) -> list[dict]:
    try:
        blocks = json.loads(seed.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    for b in blocks:
        b.setdefault("ratings", [])
    return blocks


# --------------------------------------------------------------------------- replay sources

def list_sources() -> list[dict]:
    """Everything the page can replay: past service runs, benchmark runs, bundled samples."""
    sources = []
    for f in sorted(SAMPLES.glob("*.jsonl")):
        meta = {}
        meta_file = f.with_suffix(".meta.json")
        if meta_file.exists():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        sources.append({"id": f"sample:{f.name}", "kind": "sample",
                        "label": meta.get("label", f.stem), "note": meta.get("note", "")})
    if RESULTS.exists():
        for d in sorted(RESULTS.iterdir()):
            if (d / "events.jsonl").exists():
                meta = {}
                if (d / "meta.json").exists():
                    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                q = (meta.get("case_def") or {}).get("question", "")
                sources.append({"id": f"bench:{d.name}", "kind": "bench", "label": d.name, "note": q})
    runs = {}
    for e in read_jsonl(EVENTS):
        if e.get("event") == "START":
            key = e.get("run_id") or e.get("time")
            runs[key] = e.get("time")
    for run_id, t in runs.items():
        sources.append({"id": f"log:{run_id}", "kind": "log", "label": f"logs: {run_id}", "note": t})
    return sources


def load_source(source: str) -> dict:
    """Events plus the starting board for one replay source."""
    kind, _, name = source.partition(":")
    seed = DEFAULT_SEED
    if kind == "sample":
        path = safe_child(SAMPLES, name)
        if path is None or not path.exists():
            return {"error": "unknown sample"}
        events = read_jsonl(path)
        meta_file = path.with_suffix(".meta.json")
        if meta_file.exists():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if meta.get("seed_file"):
                seed = ROOT / meta["seed_file"]
    elif kind == "bench":
        folder = safe_child(RESULTS, name)
        if folder is None or not (folder / "events.jsonl").exists():
            return {"error": "unknown run"}
        events = read_jsonl(folder / "events.jsonl")
        if (folder / "meta.json").exists():
            case = json.loads((folder / "meta.json").read_text(encoding="utf-8")).get("case_def", {})
            if case.get("seed_file"):
                seed = ROOT / case["seed_file"]
    elif kind == "log":
        events, keep = [], False
        for e in read_jsonl(EVENTS):
            if e.get("event") == "START":
                keep = (e.get("run_id") or e.get("time")) == name
            if keep:
                events.append(e)
    else:
        return {"error": "unknown source"}
    # Written blocks appear on the board as their WRITE events replay, so start from the seed only.
    return {"events": events, "blocks": seed_blocks(seed)}


# --------------------------------------------------------------------------- HW7 results

def as_bool(v: str) -> float | None:
    v = (v or "").strip().lower()
    if v in ("1", "true", "yes", "y"):
        return 1.0
    if v in ("0", "false", "no", "n"):
        return 0.0
    try:
        return float(v)
    except ValueError:
        return None


def hw7_results() -> dict:
    """Summarise bench/results/results.csv (from score.py) per condition.

    Recognised columns: condition, case, run, leak, correct, over_restricted, wall_time_s,
    tool_calls, tag. Missing columns are simply left out of the summary.
    """
    path = RESULTS / "results.csv"
    source = "results.csv"
    if not path.exists():
        path, source = RESULTS / "runs.csv", "runs.csv (not scored yet)"
    if not path.exists():
        return {"available": False}
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    summary: dict[str, dict] = {}
    for r in rows:
        cond = r.get("condition") or "?"
        tag = r.get("tag") or ""
        key = f"{cond}" if not tag else f"{cond}|{tag}"
        s = summary.setdefault(key, {"condition": cond, "tag": tag, "runs": 0, "_sums": {}, "_counts": {}})
        s["runs"] += 1
        for col in ("leak", "correct", "over_restricted", "wall_time_s", "tool_calls"):
            val = as_bool(r.get(col, ""))
            if val is not None:
                s["_sums"][col] = s["_sums"].get(col, 0) + val
                s["_counts"][col] = s["_counts"].get(col, 0) + 1
    out = []
    for s in summary.values():
        means = {c: round(s["_sums"][c] / s["_counts"][c], 3) for c in s["_sums"]}
        out.append({"condition": s["condition"], "tag": s["tag"], "runs": s["runs"], **means})
    out.sort(key=lambda s: (s["tag"], s["condition"] != "taint"))
    return {"available": True, "source": source, "rows": out, "raw_rows": len(rows)}


def usage_totals(run_id: str | None = None) -> dict:
    rows = [r for r in read_jsonl(USAGE) if run_id is None or r.get("run_id") == run_id]
    if not rows:
        return {"calls": 0}
    lat = [r.get("latency_ms") or 0 for r in rows]
    return {"calls": len(rows),
            "input_tokens": sum(r.get("input_tokens") or 0 for r in rows),
            "output_tokens": sum(r.get("output_tokens") or 0 for r in rows),
            "avg_latency_ms": round(sum(lat) / len(lat)),
            "errors": sum(1 for r in rows if r.get("error"))}


# --------------------------------------------------------------------------- routes

async def index(request: Request):
    return FileResponse(STATIC / "index.html")


async def api_config(request: Request):
    return JSONResponse({"agents": agents_public(), "db": DB.name,
                         "events_file": str(EVENTS.relative_to(ROOT))})


async def api_live(request: Request):
    """Snapshot for live mode: current blocks and the events of the latest service run."""
    events = last_run(read_jsonl(EVENTS))
    run_id = events[0].get("run_id") if events else None
    return JSONResponse({"blocks": db_blocks(DB), "events": events, "run_id": run_id,
                         "usage": usage_totals(run_id)})


async def api_blocks(request: Request):
    return JSONResponse({"blocks": db_blocks(DB)})


async def api_stream(request: Request):
    """Tail logs/events.jsonl and push each new line as an SSE message."""
    start = request.query_params.get("from")

    async def gen():
        pos = int(start) if start and start.isdigit() else (EVENTS.stat().st_size if EVENTS.exists() else 0)
        buf = ""
        idle = 0.0
        yield f": connected at {pos}\n\n"
        while True:
            if await request.is_disconnected():
                return
            size = EVENTS.stat().st_size if EVENTS.exists() else 0
            if size < pos:  # file was cleared or replaced
                pos, buf = 0, ""
            if size > pos:
                with open(EVENTS, encoding="utf-8") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                buf += chunk
                *lines, buf = buf.split("\n")
                for line in lines:
                    if line.strip():
                        yield f"data: {line.strip()}\n\n"
                idle = 0.0
            else:
                idle += 0.25
                if idle >= 15:
                    yield ": keep-alive\n\n"
                    idle = 0.0
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def api_sources(request: Request):
    return JSONResponse({"sources": list_sources()})


async def api_replay(request: Request):
    data = load_source(request.query_params.get("source", ""))
    return JSONResponse(data, status_code=404 if "error" in data else 200)


async def api_results(request: Request):
    return JSONResponse(hw7_results())


async def api_usage(request: Request):
    return JSONResponse(usage_totals(request.query_params.get("run_id")))


app = Starlette(routes=[
    Route("/", index),
    Route("/api/config", api_config),
    Route("/api/live", api_live),
    Route("/api/blocks", api_blocks),
    Route("/api/stream", api_stream),
    Route("/api/sources", api_sources),
    Route("/api/replay", api_replay),
    Route("/api/results", api_results),
    Route("/api/usage", api_usage),
    Mount("/static", StaticFiles(directory=STATIC), name="static"),
])


if __name__ == "__main__":
    host = os.environ.get("FRONTEND_HOST", "127.0.0.1")
    port = int(os.environ.get("FRONTEND_PORT", "8100"))
    print(f"MindVault control room: http://{host}:{port}  (db={DB.name}, events={EVENTS.relative_to(ROOT)})")
    uvicorn.run(app, host=host, port=port, log_level="warning")
