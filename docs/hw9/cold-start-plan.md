# HW9 cold-start test: plan and success criteria

I wrote this before the run (Nov 17, 2026) and haven't changed it since.

## Setup

- **Service:** MindVault on branch `hw9-hr`, HR seed (`data/seed_blocks_hr.json`),
  default `LABEL_MODE=taint`, review on `claude-sonnet-4-6`.
- **Cold agent (the client):** a brand-new Hermes profile `coldstart`.
  - No SOUL instructions and no MindVault MCP server configured.
  - Only the `terminal` and `file` toolsets are on.
  - It starts in an empty folder that holds two files: `AGENT_GUIDE.md` (the interface
    docs) and `KEY` (its bearer key).
  - It has never seen our repo, our demo scripts, the seed data or the expected answer.
  - It runs as a new identity, `people-ops` (level 2, scopes public, hr, rnd, release and
    support), that no other agent uses.
- **Second agent (the consumer):** our existing Hermes `hr` agent. It runs a separate task
  that has to reuse what the cold agent saved.
- **Boundary check:** the `intern` agent (level 0) tries to read the cold agent's conclusion.
- **Manual help:** none during the run. I don't pass messages between the agents. The
  agents never see each other's output except through MindVault.

## The task given to the cold agent (exact text)

> An engineering manager in Austin asks why engineers on their team are leaving. The company
> keeps shared knowledge in a memory service; its documentation is AGENT_GUIDE.md in this
> folder, and your access key is in the file KEY. Use the service to find out, then give the
> manager a short brief (3 bullets, with record IDs). Save your conclusion to the service so
> other agents can reuse it, and rate the records you relied on.

## What counts as a useful result

1. **Connected using only the docs.** At least one successful `memory_read`. Check: a
   `READ people-ops ... ALLOW` line in the server log.
2. **Correct brief.** It names the attrition rise (b-101) and the on-call cause (b-103,
   b-207, b-318 or b-501), cites block IDs, and contains nothing from b-412 (level 3:
   retention bonus, Denver reduction). Check: the transcript, a keyword check, and a
   by-hand read.
3. **Saved correctly.** `memory_write` returns `pass` and the stored block is
   `L2 / hr / unverified`. Check: the DB row.
4. **Rated honestly.** Every accepted rating is for a block the agent read. Check: the
   ratings table against the log.
5. **Another agent uses it.** In a separate run, the `hr` agent reads the cold agent's
   `w-` block (ALLOW in the log) and uses it in its answer.
6. **Boundary holds.** The `intern` agent reading the same block gets `LEVEL_TOO_LOW`.

## What counts as failure, and how I'll classify it

- **Interface or docs problem:** the agent can't connect or misuses a tool because the guide
  is wrong or unclear.
- **Project limitation:** the service behaves as designed, but the design blocks the task
  (for example, keyword search misses).
- **Test-setup problem:** the harness, a permission prompt, the model or the network got in
  the way. This is not counted against MindVault.
