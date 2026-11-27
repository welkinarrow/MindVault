# MindVault

A shared memory service for multiple AI agents, with built-in permissions and review.

Agents read and write a common pool of memory "blocks" through three MCP tools. The service decides what each agent may see, works out how sensitive each new write is, sends every write through an LLM review step, and lets agents rate the blocks they used.

> **Status:** HW6 MVP implemented and tested (`python -m unittest discover -s tests`). HW7 adds a baseline switch (`LABEL_MODE`), a structured event log and the benchmark data in `bench/`. Interface contract: [docs/interfaces_en.md](docs/interfaces_en.md) (v0.7).

## How it works

```
 Hermes agents (finance, intern)
          │  MCP over Streamable HTTP + Bearer key
          ▼
 ┌──────────────────────────────┐
 │  Memory MCP service          │
 │  memory_read / write / rate  │──► review()  ──► LLM API
 │  permission checks           │
 └──────────────┬───────────────┘
                ▼
          SQLite (data/memory.db)
```

- **Levels and scopes.** Every block has a level (`0` Public … `3` Confidential) and a scope (`public`, `finance`, `support`, `release`, `rnd`, `mgmt`). An agent can read a block only if `block.level <= agent.level`, the block's scope is in the agent's `read_scopes`, and the block is not quarantined.
- **Session taint.** A write gets the highest level the agent successfully read in the same session, so confidential information can't be passed down to a lower level. Agents cannot set the level or scope themselves.
- **Review.** Before a write is stored, `review()` checks it against up to 3 related blocks. The result is `pass` (stored as `unverified`), `quarantine` (stored, but no agent can read it), or `reject` (not stored). If review fails for any reason, the write is quarantined.
- **Ratings.** An agent can rate the accuracy (1–5) of blocks it read in the current session, at most once per block.

## The MCP tools

| Tool | Purpose |
| --- | --- |
| `memory_read` | Keyword search (`query`) or direct lookup (`block_id`) |
| `memory_write` | Save a task conclusion (`title`, `content`); returns the review result |
| `memory_rate` | Rate the accuracy of blocks read in this session |
| `memory_correct` | Fix a block in your own scope (`block_id`, `content`, `reason`). Reviewed first; the old version is kept, and every block built on it is flagged `stale` for readers |

Denials and bad input come back as normal tool results with a `reason_code` (for example `LEVEL_TOO_LOW` or `SCOPE_MISMATCH`), not as protocol errors. Inputs, outputs, and error codes are specified in [interfaces_en.md §4](docs/interfaces_en.md).

## Repository layout (planned)

| Path | Owner | Contents |
| --- | --- | --- |
| `server/` | A | `app.py` (MCP service), `policy.py`, `search.py`, `db.py` |
| `review/` | B | `reviewer.py`, `prompt.md`, `samples.json` |
| `data/` | B | `seed_blocks.json` (10 seed blocks) |
| `config/` | A | `agents.example.json` (committed), `agents.json` (contains keys, **not committed**) |
| `agents/` | C | Hermes setup notes, task prompts, skill |
| `scripts/` | A, C | `reset.py` (rebuilds the DB from seed data), `verify.py` (checks the behavior rules) |
| `docs/` | — | Interface contract |

## Quick start

**On your machine** (macOS or Linux, Python 3.10+):

```bash
./install.sh        # creates .venv, installs, makes .env, one "assistant" agent and an empty memory
# put LLM_API_KEY and LLM_MODEL in .env (the write reviewer uses them)
./run.sh            # starts the service on http://127.0.0.1:8000/mcp
```

`./install.sh --demo` sets up the demo agents and seed data instead. Running it again only
creates what is missing; it never replaces your agents or memory.

**With Docker:**

```bash
cp .env.example .env      # then set LLM_API_KEY and LLM_MODEL
docker compose up -d
docker compose logs mindvault                              # first start prints the agent keys
docker compose exec mindvault python scripts/agents.py list
```

Agents, keys and memory live in the `mindvault-data` volume.

## Connect an agent

MindVault is a standard MCP server (streamable HTTP, Bearer key). Print the setup for an agent:

```bash
.venv/bin/python scripts/connect.py assistant          # Claude Code, Codex and Cursor
.venv/bin/python scripts/connect.py assistant --client codex --url https://your-host/mcp
```

Claude Code and Codex are tested end to end (read, write, rate, and a lower-level agent being
denied). Codex needs `default_tools_approval_mode = "approve"`, which the printed block includes;
without it, `codex exec` refuses every MindVault call. Cursor's config is printed but not tested yet.

## Agents and scopes

Scopes are listed in `config/agents.json` under `"scopes"` (must include `public`). A file
without that list uses the original seven. Manage both with `scripts/agents.py` instead of
editing the file by hand:

```bash
python scripts/agents.py add-scope legal
python scripts/agents.py add counsel --level 2 --read public,legal --write legal   # prints the key once
python scripts/agents.py list          # never prints keys
python scripts/agents.py new-key counsel
python scripts/agents.py remove counsel
```

Restart the service after a change. Older databases are upgraded automatically on startup.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `MEMORY_HOST` | `127.0.0.1` | Service bind address |
| `MEMORY_PORT` | `8000` | Service port (endpoint: `http://127.0.0.1:8000/mcp`) |
| `MEMORY_DB` | `data/memory.db` | SQLite database path |
| `AGENTS_FILE` | `config/agents.json` | Agent IDs, keys, levels, scopes |
| `LLM_API_KEY` | none (required) | Used by the review function |
| `LLM_MODEL` | to be decided | Used by the review function |
| `REVIEW_TIMEOUT` | `30` | Review call timeout in seconds |
| `LABEL_MODE` | `taint` | `taint` = session taint sets the level; `self` = HW7 baseline, the agent sets it |
| `RUN_ID` | none | Tags every line of `logs/events.jsonl` and `logs/review_usage.jsonl` |

Agents authenticate with `Authorization: Bearer <key>`, using a 32-character hex key from `config/agents.json`. Never commit keys. Copy `agents.example.json` to `agents.json` and fill in the keys locally.

## v2 for Demo Day (branch `v2`)

v2 adds internal/external memory, a local memory manager, citations and source tracing. The interface
is section 13 of [`docs/interfaces.md`](docs/interfaces.md) ([English](docs/interfaces_en.md)); the demo
steps are in [`docs/v2/demo-runbook.md`](docs/v2/demo-runbook.md).

| What | Command | Notes |
| --- | --- | --- |
| Local model (Ollama + qwen3:8b) | `scripts/local_model_up.sh` | Starts Ollama, creates `mv-qwen3` (8K context, temperature 0), warms it up and keeps it loaded for 24 h |
| Calling the local model from Python | `scripts/local_llm.py` | `chat()` and `chat_json()`; native `/api/chat` with thinking off. Usage at the top of the file |
| Lightweight local agent | `python scripts/local_agent.py --agent zhou-local --trace "<question>"` | Four tools (search, read, write, cite); `--trace` opens the trace view after answering |
| Scripted fallback client | `python scripts/scripted_client.py scenario1` (or `scenario2 [--write]`) | No model: fixed tool calls against the real service |
| Trace view | `python scripts/trace_view.py --agent zhou-local --answer c-0007 --block b-610` | Static HTML from `memory_trace`; mocks in `tests/fixtures/trace/` |
| End-to-end tests T6–T10 | `python scripts/run_tests.py` | Needs v2 service, scenario 1 seeds and the demo agents; T1–T5 remain `scripts/verify.py` |
| Memory manager evaluation | `python eval/run_eval.py --judge <module:function> --cases <dir>` | Pass bar in `eval/criteria.md`, committed before running |
| Unit tests | `python -m unittest discover -s tests` | No model or service needed |

The demo agents (`wang-cloud`, `wang-local`, `zhou-local`, `manager-local`, `manager-cloud`) are in
`config/agents.example.json` with `model_location` and `role`. To add them to an existing
`config/agents.json` without changing other keys, use `scripts/agents.py add … --model-location … --role …`.

## Contributing

[docs/interfaces_en.md](docs/interfaces_en.md) is the single source of truth for every handoff between components. If the code and the contract disagree, the contract wins. To change the contract, raise it in the group chat. Once all three members agree, A updates the file, bumps the version, and adds an entry to its change log.

Open questions (Hermes header support, LLM choice, timeouts) are tracked in [interfaces_en.md §11](docs/interfaces_en.md).
