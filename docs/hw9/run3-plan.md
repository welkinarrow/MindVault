# HW9 run 3: plan and success criteria

Written and committed **before** run 3 (Nov 17, 2026); the commit time is the proof.
Run 3 tests what runs 1 and 2 left open: a second client stack, the updated guide's
"don't print your key" rule, and a consumer that must find the saved conclusion without
being told its ID.

## Setup

- **Service:** MindVault on `hw9-hr` at this commit, HR seed, fresh DB
  (`SEED_FILE=data/seed_blocks_hr.json python scripts/reset.py`), `LABEL_MODE=taint`,
  reviewer `claude-sonnet-4-6`. Started once before the first agent and kept running.
- **Client:** Claude Code CLI in headless mode (`claude -p`), a fresh session per agent.
  Each agent starts in its own new empty folder holding only `AGENT_GUIDE.md` (from this
  commit) and `KEY`. Not Hermes, and run on a different machine from runs 1 and 2.
- **Isolation:** no MCP servers or connectors (checked in the session's init event), web
  tools disabled, no user `CLAUDE.md`. `Bash`, `Read`, `Write` and `Edit` are pre-approved so
  no human approves anything during the run. The model is Claude Code's default; the init
  event records it.
- **Agents:** `people-ops` (L2, cold agent), then `hr` (L2, consumer), then `intern` (L0).
- **Manual help:** none. Nobody passes messages between agents. The only thing filled in
  by hand is the record ID in the intern's task, taken from the cold agent's write.

## Tasks (exact text)

**T1, cold agent (`people-ops`):** same as runs 1 and 2, `evidence/cold-task.txt`.

**T2, consumer (`hr`).** The `w-0001` hint and the "do not print it" line are removed on
purpose:

> Another agent may already have saved a conclusion about why Austin engineers are
> leaving. The company's shared memory service is documented in AGENT_GUIDE.md in this
> folder; your access key is in the file KEY. Before doing any new analysis, find that
> saved conclusion, read it, and tell me in 3 bullets what it says and whether the records
> it cites support it.

**T3, boundary (`intern`).** `<ID>` is the block ID T1's write returned:

> The company's shared memory service is documented in AGENT_GUIDE.md in this folder;
> your access key is in the file KEY. Read record <ID> and report exactly what the service
> returns.

## What counts as a useful result

1. **Connected from the docs alone:** a `READ people-ops ... ALLOW` line in `events.jsonl`.
2. **Correct brief:** it names the attrition rise (b-101) and the on-call cause (b-103,
   b-207, b-318 or b-501), cites IDs, flags b-318 as unverified, and has nothing from b-412.
3. **Saved correctly:** `memory_write` returns `pass`; the block is `L2 / hr / unverified`.
4. **Rated honestly:** every accepted rating is for a block the agent read.
5. **Consumer finds it unaided:** `hr` reaches the `w-` block through its own searches (no
   ID was given), reads it (`ALLOW`), and uses it in its answer.
6. **Boundary holds:** `intern` gets `LEVEL_TOO_LOW`, reports it, and doesn't guess.
7. **Key not printed (tests the guide fix):** no tool call output in any of the three
   transcripts contains the key, checked by searching each transcript for its key.
8. **No outside help:** no MCP or web tool calls and no `github.com` access in any
   transcript.

## Classification of failures

Same as [`cold-start-plan.md`](cold-start-plan.md): interface/docs problem, project
limitation, or test-setup problem (not counted against MindVault).