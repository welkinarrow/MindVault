# HW8: MindVault side of the Nanda Town integration

The Nanda Town integration (seller adapter, runs, PR) lives in a fork of
`projnanda/nandatown`. This folder holds what MindVault needs for it.

## Base

Branch `hw8-a`, started from `hw7-results` (9932208), the final HW7 system
(`LABEL_MODE`, `SEARCH_MODE`, events.jsonl, bench/probe.py).
Tests: `python -m pytest tests` (148 passed on Nov 9, 2026).

## Files

| File | Purpose |
| --- | --- |
| `b-701.json` | L2 finance price list: widget, 1995 cents each, 3990 for 2 |
| `build_seed.py` | Builds `seed_with_pricing.json` = `data/seed_blocks.json` + b-701 |
| `seed_with_pricing.json` | Seed for every HW8 run (11 blocks) |
| `check_access.py` | finance can read b-701; intern gets LEVEL_TOO_LOW |
| `check_session.py` | Taint after reconnect; duplicate writes |
| `mindvault.env.example` | Variable names for `~/.config/mindvault.env` (no real keys) |

## Run (two terminals, both with `source .venv/bin/activate` and `source ~/.config/mindvault.env`)

```bash
# 1. stop the service, then reset (terminal 1)
SEED_FILE=hw8/seed_with_pricing.json python scripts/reset.py

# 2. start the service (terminal 2)
LABEL_MODE=taint SEARCH_MODE=full RUN_ID=<run-id> SEED_FILE=hw8/seed_with_pricing.json python -m server.app

# 3. checks (terminal 1)
python hw8/check_access.py
python hw8/check_session.py
```

## Observed on Nov 9, 2026

- `check_access.py`: finance reads b-701 (`{"results": [...]}`); intern is denied
  (`{"denied": true, "reason_code": "LEVEL_TOO_LOW", "required_level": 2}`). Both PASS.
- Taint is per agent, not per connection: after reading b-701 and reconnecting,
  finance's write is labelled L2. It also carries over to the next run if the
  service is not restarted (sessions end after 30 min idle).
- `memory_write` has no idempotency key: the same note written twice gives two
  blocks (w-0002, w-0003), both L2 and both approved by the reviewer.
- Review is synchronous inside `memory_write`. A note with no business content
  ("No sources read yet.") was quarantined. A write with review takes about 1.5 s.

## Notes for the Town seller

- Client: `scripts/selftest.py` `Agent(url, key)`; `await agent.call("memory_read", {"block_id": "b-701"})`,
  `await agent.call("memory_write", {"title": ..., "content": ...})`.
  URL `http://{cfg.host}:{cfg.port}/mcp`, keys from `load_agents(Config.from_env().agents_file)`.
- Reset the database and restart the service before every run, or taint carries over.
- Write a concrete deal note (item, quantity, total, source b-701), or review may quarantine it.
- Reply to the Town buyer before writing to MindVault, then ack.
- Intern probe: reuse `bench/probe.py`.