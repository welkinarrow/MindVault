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

## Contributing

[docs/interfaces_en.md](docs/interfaces_en.md) is the single source of truth for every handoff between components. If the code and the contract disagree, the contract wins. To change the contract, raise it in the group chat. Once all three members agree, A updates the file, bumps the version, and adds an entry to its change log.

Open questions (Hermes header support, LLM choice, timeouts) are tracked in [interfaces_en.md §11](docs/interfaces_en.md).
