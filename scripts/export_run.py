"""Export a Claude Code cold-start run as redacted evidence (HW9 run 3).

Usage (from the repo root, after the agents have finished):
    python scripts/export_run.py <raw_dir> <run_id> <out_dir> <agent> [<agent> ...]

<raw_dir> holds one `claude -p --output-format stream-json --verbose` transcript per
agent, named <agent>.jsonl. For each agent this writes <out_dir>/<agent>-transcript.md.
It also writes <out_dir>/events.jsonl (the service log lines tagged with <run_id>) and
<out_dir>/written-blocks.txt (every agent-written block in the DB).

Every agent key from config/agents.json, the per-run temp folder and the home folder
are replaced before anything is written; the script stops if a key survives.
"""

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_RESULT = 6000  # characters of each tool result kept in the transcript

raw_dir, run_id, out_dir, agents = Path(sys.argv[1]).expanduser(), sys.argv[2], Path(sys.argv[3]), sys.argv[4:]
if not agents:
    sys.exit(__doc__)
out_dir.mkdir(parents=True, exist_ok=True)

KEYS = [a["key"] for a in json.loads((ROOT / "config/agents.json").read_text())["agents"]]
TMP = re.compile(r"(/private)?/var/folders/[^\s\"'`]+?/T/tmp\.[A-Za-z0-9]+")
HOME = re.compile(r"/Users/[^/\s\"'`]+")


def clean(text: str) -> str:
    for k in KEYS:
        text = text.replace(k, "<KEY>")
    return HOME.sub("~", TMP.sub("<cold-folder>", text))


def result_text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict))


def fence(text: str, lang: str = "") -> str:
    ticks = "````" if "```" in text else "```"
    return f"{ticks}{lang}\n{text}\n{ticks}"


for agent in agents:
    lines = [json.loads(l) for l in (raw_dir / f"{agent}.jsonl").read_text().splitlines() if l.strip()]
    md = [f"# Run 3 transcript: Claude Code cold session, identity `{agent}`", "",
          "Exported from `claude -p --output-format stream-json`. Keys redacted as `<KEY>`,",
          "the agent's empty start folder as `<cold-folder>`. The exact task text is in",
          "[`run3-plan.md`](../../run3-plan.md).", ""]
    for e in lines:
        kind = e.get("type")
        if kind == "system" and e.get("subtype") == "init":
            md += ["## Session setup (init event)", "",
                   f"- Claude Code: `{e.get('claude_code_version')}`",
                   f"- Model: `{e.get('model')}`",
                   f"- MCP servers: `{e.get('mcp_servers')}`",
                   f"- Tools: {', '.join(e.get('tools') or [])}", ""]
        elif kind == "user" and isinstance(e.get("message", {}).get("content"), str):
            md += ["## Task given to the agent", "", fence(e["message"]["content"]), ""]
        elif kind in ("assistant", "user"):
            for c in e.get("message", {}).get("content") or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text" and c.get("text", "").strip():
                    md += [c["text"].strip(), ""]
                elif c.get("type") == "tool_use":
                    inp = c.get("input", {})
                    body = inp.get("command") or json.dumps(inp, indent=1, ensure_ascii=False)
                    md += [f"### Tool call: `{c.get('name')}`", fence(body), ""]
                elif c.get("type") == "tool_result":
                    r = result_text(c.get("content"))
                    if len(r) > MAX_RESULT:
                        r = r[:MAX_RESULT] + f"\n... [{len(r) - MAX_RESULT} more characters cut]"
                    md += ["<details><summary>Result</summary>", "", fence(r), "</details>", ""]
        elif kind == "result":
            md += ["## Final answer", "", e.get("result", ""), "",
                   f"_Turns: {e.get('num_turns')}, cost: ${e.get('total_cost_usd')}, "
                   f"time: {round((e.get('duration_ms') or 0) / 1000)} s._", ""]
    text = clean("\n".join(md))
    assert not any(k in text for k in KEYS), f"{agent}: a key survived redaction"
    (out_dir / f"{agent}-transcript.md").write_text(text)
    print("wrote", out_dir / f"{agent}-transcript.md")

events = [l for l in (ROOT / "logs/events.jsonl").read_text().splitlines()
          if l.strip() and json.loads(l).get("run_id") == run_id]
(out_dir / "events.jsonl").write_text(clean("\n".join(events)) + "\n")
print("wrote", out_dir / "events.jsonl", f"({len(events)} lines)")

db = sqlite3.connect(ROOT / "data/memory.db")
rows = db.execute("SELECT block_id, level, scope, status, author, review_action, title "
                  "FROM blocks WHERE author != 'seed' ORDER BY block_id").fetchall()
table = ["block_id|level|scope|status|author|review_action|title"] + ["|".join(map(str, r)) for r in rows]
(out_dir / "written-blocks.txt").write_text("\n".join(table) + "\n")
print("\n".join(table))