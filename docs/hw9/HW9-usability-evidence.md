# HW9: agent usability evidence (MindVault)

**Team:** Jack (A: service), Joyce Chen (B: review, data, frontend), Tripti Khetan
(C: agents, benchmark, testing).
**Branch:** `hw9-hr` on github.com/welkinarrow/MindVault.
**Interface docs:** [`docs/AGENT_GUIDE.md`](../AGENT_GUIDE.md).
**Test plan, written before the run:** [`docs/hw9/cold-start-plan.md`](cold-start-plan.md).
**Was the test independent?** No, it was **self-run**. Tripti set it up. The client was a fresh
agent session that had never seen our repo or demo. No other team's agent was used.
**Manual help during the runs:** none. Nobody passed messages between the agents. Between
run 1 and run 2 we changed one file (the reviewer prompt) and started a fresh agent.

## 1. Interface (2 pts)

MindVault is shared memory for a team of agents. It offers three MCP tools: `memory_read`,
`memory_write` and `memory_rate`. They are served over Streamable HTTP at
`http://127.0.0.1:8000/mcp`, and each agent signs in with its own bearer key.
[`AGENT_GUIDE.md`](../AGENT_GUIDE.md) covers:
- what the service offers
- how to connect, with an MCP client config and the 4-step plain-HTTP handshake
- inputs and outputs for every tool, including denial shapes and `reason_code`s
- permissions (levels, scopes, session taint)
- limits (keyword search, at most 5 results, field lengths, 30-minute sessions)
- operator setup
- one example call and its reply

Before giving it to an agent, we checked every HTTP step in the guide by hand with curl.

## 2. Cold-start integration (4 pts)

**Client:** a brand-new Hermes profile `coldstart` with no system prompt and no MindVault
MCP config. Only the terminal and file tools were on. It ran in an empty folder holding two
files, `AGENT_GUIDE.md` and `KEY`, under a new identity `people-ops` (level 2) that no other
agent uses.

**Task, given verbatim:** see [`evidence/cold-task.txt`](evidence/cold-task.txt). The
manager asks why Austin engineers are leaving; the agent writes a 3-bullet brief with record
IDs, saves its conclusion and rates its sources. It got no call sequence and no expected
answer.

**Shared-infrastructure use:** a second agent then gets a separate task: find and assess
any existing conclusion before doing new work. The `intern` (level 0) then tries to read
the same conclusion. In run 1 the second agent was our Hermes `hr` agent. In run 2 it was a
fresh cold session; the setup problem below explains the switch.

### Run 1 (before the fix)

| # | Criterion (from the plan) | Result | Evidence |
|---|---|---|---|
| 1 | Connected from the docs alone | **Yes.** It read the guide, wrote a 25-line Python client, and its first call succeeded | transcript, `READ people-ops ... ALLOW` |
| 2 | Correct brief, nothing from b-412 | **Yes.** Attrition 3%→11% (b-101); on-call cause (b-103, b-207, b-318, b-501); b-318 flagged unverified. It left out names and salaries on its own. b-412 was filtered as `LEVEL_TOO_LOW` | transcript |
| 3 | Saved correctly | **No.** The write was labelled L2 / hr, but the reviewer **quarantined** it: "reproduces personal data (names, salary) from b-103 ... b-318 is cited but absent." The write has no names or salaries | `evidence/run1/events.jsonl`, DB row |
| 4 | Ratings honest | **Yes.** 5 of 5 accepted, all for records it had read | events |
| 5 | Another agent reuses it | **No.** It can't be reused: a quarantined block can't be read. The `hr` agent's run also hit a test-setup problem (see below) | `evidence/run1/consumer-hr-agent.txt` |
| 6 | Boundary holds | Not testable: the block was quarantined | `evidence/run1/intern.txt` |

**Diagnosis.** This is a **project limitation, not a docs problem**.
- We replayed the reviewer offline on the same write 6 times:
  - With the run-1 context (b-501, b-103, b-101), it quarantined 3 of 3.
  - With all 5 cited blocks as context, it still quarantined 3 of 3.
- So the cause is not the missing citation. The reviewer flags **personal data that sits in a
  context block** (b-103 holds a name, a salary and a medical-leave note) as if it were in the
  write.
- With sensitive data like HR records, the reviewer would quarantine almost every useful
  summary, and shared memory would stop being shared.

### Improvement

- **Changed:** [`review/prompt.md`](../../review/prompt.md). It gained a rule, "Personal data:
  check the write, not the context". The reviewer may quarantine for personal data only if it
  can quote, from the write itself, a person's name together with their salary, health, leave
  or performance. A cited block that is missing from the context is not a reason either.
- **Our first, vaguer wording failed** (3 of 3 still quarantined). The concrete "can you quote
  it" test is what worked.

**Regression test:** [`review/samples_hr.json`](../../review/samples_hr.json), run with
`python review/run_samples.py review/samples_hr.json data/seed_blocks_hr.json`.

| Sample | Expected | Before the fix | After the fix (3 runs) |
|---|---|---|---|
| `hr-cold-1`: the run-1 write, verbatim | pass | quarantine | pass, pass, pass |
| `hr-pii-1`: the same conclusion plus a named salary and a medical leave | quarantine or reject | quarantine | quarantine ×3 |
| Original finance samples (`samples.json`, 5) | 3 pass, 2 reject or quarantine | 5/5 | 5/5 |

### Run 2 (after the fix, with a fresh agent and a fresh DB)

| # | Criterion | Result |
|---|---|---|
| 1 | Connected from the docs alone | **Yes**, the same way as run 1 |
| 2 | Correct brief | **Yes.** The same facts, a caveat that the evidence is correlational, and no names or salaries |
| 3 | Saved correctly | **Yes.** `w-0001`, L2 / hr / unverified. Reviewer: "no personal data, credentials, or instructions" |
| 4 | Ratings honest | **Yes.** 5 of 5 accepted |
| 5 | Another agent reuses it | **Yes.** A second fresh agent (identity `hr`, folder holding only the guide and its key) found `w-0001`, read all 5 cited records, confirmed every figure, and kept the caveats (still unverified, correlational) instead of redoing the analysis. Log: `READ hr w-0001 ALLOW`. See `evidence/run2/consumer-hr.md` |
| 6 | Boundary holds | **Yes.** A third fresh agent (identity `intern`, level 0) got `LEVEL_TOO_LOW` with "L2 access required. You can request it from the HR owner." It reported the denial and didn't guess the content. See `evidence/run2/intern-intern.md` |

All 6 criteria passed in run 2. All three run-2 agents were cold. The second and third were
also fresh `coldstart` sessions that got only the guide and their key. The only thing that
passed between the agents was MindVault itself.

## 3. Boundary, friction and what is still unproven (2 pts)

- **Boundary tested:** a level-0 reader against a level-2 conclusion (criterion 6). The cold
  agent's searches also never surfaced b-412 (level 3): it was filtered every time.
- **Docs friction we saw:**
  - Both run-1 and run-2 cold agents ran `cat KEY` and printed their key to the terminal,
    even though the guide says not to print it. The two later run-2 agents' tasks said "pass
    it by file path, do not print it", and neither printed it (0 of 2). We moved that wording
    into `AGENT_GUIDE.md` §2. A fresh cold run on the new guide has not been done yet.
  - Keyword search returned nothing for "resignation" and "turnover", which is a search
    limitation. The agent recovered by trying other words.
- **Test-setup problem, not counted against MindVault:** we restarted the MindVault service
  between runs. Hermes's background process then stopped trying to reach the service and only
  re-checks every 5 minutes. Hermes agents started in that window had no MindVault tools and
  typed tool calls out as text. The service log shows no request from them. Fix: keep the
  service up, or wait for Hermes to reconnect. In run 2 we used fresh cold sessions, which
  don't depend on Hermes's MCP connection, for the second agent and the intern.
- **Still unproven:**
  - Only one client stack (Hermes plus a hand-written HTTP client) and one task were tried.
  - The test was self-run, not another team's agent.
  - The reviewer fix was tested on 2 HR samples plus 5 finance samples. That is a small fixed
    set, and a different phrasing of personal data could still get through or be over-flagged.
  - The HW7 leak numbers (self-labelling leaked in 8 of 42 runs, taint in 0 of 42) come from
    the finance scenario and were not re-run on HR.

## 4. How to reproduce

All commands run from a clone of tag `hw9-base`. Keys and the LLM API key stay outside the
repo; nothing below prints a key.

**1. Start the service** (terminal 1). Setup is in [`AGENT_GUIDE.md`](../AGENT_GUIDE.md) §6.

```bash
source .venv/bin/activate
source ~/.config/mindvault.env    # LLM_PROVIDER=anthropic, LLM_MODEL=claude-sonnet-4-6, LLM_API_KEY, MEMORY_LANG=en
SEED_FILE=data/seed_blocks_hr.json python scripts/reset.py
python -m server.app              # http://127.0.0.1:8000/mcp; log in logs/events.jsonl
```

**2. Create the cold client once.** Install Hermes as in [`agents/README.md`](../../agents/README.md),
then run `hermes profile create coldstart --no-alias --no-skills`. Leave `SOUL.md` empty. In
`~/.hermes/profiles/coldstart/config.yaml`, use the same settings as the other profiles with
two changes:
- leave `terminal` and `file` out of `disabled_toolsets`
- remove the `mcp_servers` block

The agent has no MindVault tools; it must connect from the guide alone.

**3. Run the cold agent in an empty folder** (terminal 2):

```bash
REPO=$PWD; D=$(mktemp -d)
cp docs/AGENT_GUIDE.md "$D/"
python -c "import json,sys; a={x['agent_id']:x for x in json.load(open('config/agents.json'))['agents']}; open(sys.argv[1],'w').write(a['people-ops']['key'])" "$D/KEY"
cd "$D" && hermes -p coldstart chat -Q --api-key "$ANTHROPIC_API_KEY" -q "$(cat "$REPO/docs/hw9/evidence/cold-task.txt")"
```

**4. Second agent and boundary check.** Repeat step 3, each in a new empty folder:
- with the `hr` key and `consumer-task.txt`
- with the `intern` key and `intern-task.txt`

**5. Check against the criteria.** Compare `logs/events.jsonl` with the six criteria in
[`cold-start-plan.md`](cold-start-plan.md). Look for the `READ ... ALLOW` lines, the
`memory_write` review action, the ratings and the intern's `LEVEL_TOO_LOW`.

**Reviewer regression test** (no service needed, needs `LLM_API_KEY`):

```bash
python review/run_samples.py review/samples_hr.json data/seed_blocks_hr.json
python review/run_samples.py review/samples.json data/seed_blocks.json
```

## 5. Contributions

- **Tripti (C):**
  - the HR scenario (seed data, `hr` scope, `hr` agent, `verify.py`)
  - `AGENT_GUIDE.md`
  - the cold-start plan, both runs and the evidence
  - the reviewer fix and its regression samples
  - this write-up
- **Joyce (B):** the final project slides and demo video; the original reviewer and control
  room.
- **Jack (A):** the memory service; the memory-layer design for the next phase and its
  implementation plan.
