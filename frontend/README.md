# MindVault control room (owner: B)

One page that shows every decision the memory service makes, as it happens: which blocks each
agent reads, what permission filters out and why, how session taint rises, what level a write gets,
the review verdict, which ratings are accepted, and whether anything leaks to a lower level.

The page is **read-only**. It reads `logs/events.jsonl` (I-6), the SQLite database (opened
`mode=ro`), `bench/results/` and `frontend/samples/`. It makes no permission decisions and never
sees an agent key: keys are stripped from the agent config before the API returns it.

## Run it

No extra packages. Starlette and uvicorn already come with `mcp`, and the page needs no build step,
so nothing has to be installed on the demo laptop.

```bash
python frontend/server.py            # http://127.0.0.1:8100   (FRONTEND_PORT to change)
```

Start it before or after the service, in any order.

| Mode | How | What it shows |
| --- | --- | --- |
| **Live** (default) | Start the service, then run Hermes (`agents/run.sh finance "…"`) or the scripted demo below | Events stream in over Server-Sent Events. When the service restarts, the board reloads from the database |
| **Replay** | Press `R`, or open `/?mode=replay` | Any recording: bundled samples, every `bench/results/<run_id>/`, and each past service run in `logs/events.jsonl` |

Keyboard: `L` live, `R` replay, `Space` play/pause, `→` step, `Home` restart, `Esc` close the details panel.

### Scripted demo (fallback when Hermes or the LLM is slow)

```bash
python frontend/demo_scenario.py --pause 1.5
```

This makes the real MCP calls of the demo story with the real agent keys: search, denied L3 read,
denied wrong-scope read, write, ratings (one rejected), then the intern's attempts. It is a script,
not an LLM agent, but every decision on screen is made by the real service. Under `LABEL_MODE=self`
it declares the write L0, which is what the baseline is about.

`frontend/samples/scripted-{taint,self}.jsonl` are recordings of this script. They used a stub
reviewer because there was no LLM key in the recording environment. The meta file says so.

## What is on screen

| Area | Meaning |
| --- | --- |
| Stat row | Reads allowed; blocked (filtered from a search + denied by id); writes and how many were under-labelled; the writer's session taint; ratings accepted/rejected; leaks |
| Agents | Clearance, readable scopes, the live taint meter of the current session, counts, last action |
| Memory vault | Every block on its level floor (L3 at the top). Green pulse = read, amber = filtered from search, red shake = denied, blue drop-in = new write. Animated beams run from agent to block. Dashed = unverified, hatched = quarantined |
| View as | Locks the blocks that agent cannot read (display only; mirrors `server/policy.py`) |
| Event stream | One line per decision, in plain English, with the reason code and why |
| Taint chart | Each session's taint as a step line; squares are writes at the level they got (amber = below the taint) |
| HW7 panel | Leak rate, correctness and over-restriction for taint vs self, from `bench/results/results.csv` |
| LEAK alarm | Fires when an agent reads a written block whose writer had read material above that agent's clearance |

Click any block for its content, label basis, review reason, ratings and who can read it.

## For C: results.csv columns the panel reads

`condition` (taint/self), `tag`, `leak`, `correct`, `over_restricted` (0/1 or true/false per run),
plus optional `wall_time_s` and `tool_calls`. Any column that is missing is simply not drawn. Until
`results.csv` exists the panel falls back to `runs.csv` and shows run counts only.

## API (all GET, all read-only)

`/api/config` agents without keys · `/api/live` current blocks + events since the last START ·
`/api/blocks` · `/api/stream` SSE tail of events.jsonl · `/api/sources` replay list ·
`/api/replay?source=` events + starting board · `/api/results` HW7 summary · `/api/usage` reviewer tokens.

## Changes from the plan

The plan said FastAPI + React/Vite/Tailwind. The backend uses Starlette (FastAPI is built on it,
and it is already installed with `mcp`). The page is plain HTML/CSS/JS with no build step, so the
demo laptop needs no npm and no network. The layout, live stream and read-only rules are as planned.
