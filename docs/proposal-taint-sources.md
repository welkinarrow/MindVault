# Proposal: keep taint across idle time and restarts, and record sources on every write

Branch `demo-taint-sources` (from `hw9-hr`). Not merged. `interfaces_en.md` is not edited yet;
if the team agrees, §3.1 (sessions) and §4 (memory_write output) need the updates listed at the end.

## 1. The hole

Taint lived only in the in-memory session, and a session ended after 30 minutes idle or when
the service restarted. The agent still holds what it read, but its next write was labelled L0.

Reproduced on `hw9-hr` with the test fixtures (finance reads b-318, an L1 block):

| What the agent does | Label on its next write |
| --- | --- |
| Writes right away | L1 |
| Waits 31 minutes, then writes | **L0** |
| Service restarts, then it writes | **L0** |

Any long-running agent, or a crash and restart, could pass confidential facts down to L0.
The HW7 result (taint 0 of 42) still stands: every benchmark run used a fresh service and
finished well inside 30 minutes, so it never hit this path.

## 2. The fix

- New table `agent_taint`: one row per agent with the highest level read and the block IDs
  read, in order. Every allowed read updates it (`Store.add_taint`); writes take the label
  from it (`Store.get_taint`).
- Idle time still starts a new session, so ratings still need a fresh read, but it no longer
  lowers taint.
- Only two things clear taint: `scripts/reset.py` (rebuilds the DB, as before) and the new
  admin tool `scripts/reset_taint.py <agent_id>`. Agents cannot clear it.
- Older databases are upgraded on startup (column and table added), so nobody has to rebuild.

Same check after the fix: L1, L1, L1. Also run live on the real service: finance read b-318,
the service was restarted, finance wrote w-0002, which was labelled L2 with its sources, and
the intern was denied with `LEVEL_TOO_LOW`.

**Cost:** an agent's taint now only goes up until an admin resets it, so a long-lived agent
gets stricter over time. That's the safe direction. The real answer is for the orchestrator
that starts each agent task to reset it; that's future work.

**Rehearsals:** restarting the service no longer clears taint. Run `scripts/reset.py` (which
we already do before every run) or `scripts/reset_taint.py <agent_id>`.

## 3. Sources (step one of "Time and sources" in the Memory Layer Design)

- New column `blocks.sources`: a JSON list of block IDs the author read before writing,
  in first-read order. Seed blocks have none.
- `memory_write` now returns `sources`, the WRITE event in `events.jsonl` carries `sources`,
  and the service log line ends with `sources=b-101,b-318`.
- Blocks that were denied, or only listed by title (`SEARCH_MODE=titles`), are not sources,
  because their content never reached the agent.
- Sources are not returned by `memory_read` yet. A reader could otherwise see IDs of blocks
  above their level. The transparency view needs that filter first.

This is what the next steps build on: flag every derived block when a source is corrected,
and show the chain in the control room.

## 4. Tests

- `tests/test_taint_sources.py`: 15 new tests (idle, restart, admin reset, per agent, denied
  reads, sources order, titles mode, old database upgrade).
- `tests/test_service.py::test_new_session_after_idle` previously asserted the leak
  (level 0 after idle). It now asserts level 2.
- All 163 tests pass. `scripts/selftest.py` passes against a live service.

## 5. Spec changes needed if we merge

- §3.1 Sessions: idle starts a new session for ratings; taint is per agent, persistent, and
  cleared only by an admin.
- §4 memory_write output: add `sources` (list of block IDs).
- §7 Schema: `blocks.sources`, table `agent_taint`.
- §8 Log format: WRITE line gains `sources=...` at the end.

---

# Part 2 (branch `product-config`, on top of this one): configurable scopes and agent setup

**Problem.** The seven scopes were hardcoded in four places (`schema.sql` CHECK, `config.py`,
`reset.py`, `gen_keys.py`), so a company couldn't add its own (legal, sales…) without editing
SQL. Adding an agent meant hand-editing `agents.json` and making up a key.

**Change.**
- `agents.json` can list `"scopes"` (lowercase names, must include `public`). Without the list,
  the original seven apply, so existing files keep working unchanged. Agents and seed blocks are
  checked against it, so a typo like `lgeal` is an error, not a new scope.
- The scope CHECK is gone from `schema.sql`. Older databases are rebuilt without it on startup;
  every block and rating is kept (unit test, plus a copy of `data/memory.db`: 12 of 12 blocks kept).
- New `scripts/agents.py`: `list` (never prints keys), `add-scope`, `add` (generates and prints the
  key once), `new-key`, `remove`. It validates the whole file before an atomic write, so a bad
  command leaves the file untouched.
- Denials for a custom scope say "request it from the legal owner".
- `config/agents.example.json` now lists the seven scopes explicitly.

**Checks.** 179 tests pass (16 new in `tests/test_org_config.py`). `selftest.py` passes with the
existing config. Live, with a new config built only through `scripts/agents.py`: `counsel` wrote
into a new `legal` scope, and `finance` was denied with `SCOPE_MISMATCH`.

**Spec changes if merged:** §6.1 (agents.json gains `scopes`), §7 (no CHECK on `blocks.scope`).
