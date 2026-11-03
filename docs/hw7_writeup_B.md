# HW7 write-up: B's sections (draft for A)

Square brackets mark numbers and decisions that are still open; fill them in when C's results and
the scope decision are in. Everything else matches the code on `hw7-c` (commit 27d81d8) and the
frozen checks in `bench/` (commit 11878f2).

## 1. Claim and baseline

**Claim.** MindVault labels every write with the highest level its author read in the current
session ("session taint"), computed by the service. We claim that this keeps facts from L1+ blocks
away from an L0 reader more reliably than letting the writing agent choose the label itself, at
little cost in answer correctness. The main metric is the **leak rate**: the share of runs in which
a fixed L0 probe receives any canary fact from an L1+ block. Secondary metrics are answer
correctness (every required fact appears in the finance agent's answer), over-restriction (public
tasks whose conclusion the L0 probe cannot read), wall time, tool calls, and reviewer tokens.

**Baseline.** The baseline is the same system with one switch flipped: `LABEL_MODE=self`. In this
mode `memory_write` accepts a `level` from 0 to 3 chosen by the agent, and the agent's instructions
replace one sentence ("do not set a level; the service decides") with "choose the level yourself,
0–3, by how sensitive the content is". This is how most shared agent memories work today, so the
baseline is the realistic alternative rather than a strawman. Everything else is held fixed: the
same Hermes agent on `claude-sonnet-5-5` with `max_turns: 15`, the same 10 seed blocks (plus the
injection block b-320 for case c09 only), the same reviewer (`claude-sonnet-4-6`), the same three
tools, the same questions, and a fresh database and service process for every run.

**How results are checked.** The probe is a script, not an LLM: with the L0 key it reads every new
`w-` block by ID and runs three fixed keyword searches, so the result does not depend on what any
agent says it did. Leaks and correctness are case-insensitive substring matches against
`bench/answer_key.json` (32 canaries over 8 L1+ blocks), which was committed before the first full
run at [commit hash]. C hand-checked [n] of the correctness scores.

## 2. Limitations (where the system fails or stops helping)

- **Over-labeling from search.** A keyword search returns the full content of up to five hits, and
  every hit raises the session's taint. Before any run, our pre-check showed that the words of every
  public question (c06, c07 and fresh f03) already pull L1 or L2 blocks into the results. Under taint
  the public answer is then labelled L1+ and the L0 reader cannot see it, even though it contains
  nothing sensitive. [Measured over-restriction: taint X% vs self Y%.]
- **Scope can hide the effect of the label.** Writes always take the writer's scope (`finance`), and
  the L0 probe can only read `public`. Under the current rules the probe cannot read any finance
  write in either condition, whatever its level. [State the team's decision: e.g. "for the benchmark
  the probe agent was given read access to the finance scope at L0, so that only the level decides"
  — or report that the leak comparison was not separable under these rules.]
- **Taint follows reading, not use.** A block the agent glanced at but did not use still raises the
  label. The rule is safe but blunt; it trades availability for safety.
- **Reworded leaks are not caught.** Canaries are exact strings ("4.8%", "61%", "charged twice"). A
  paraphrase ("the refund rate more than doubled") leaks the fact but is not counted, so the
  measured leak rate is a lower bound in both conditions.
- **The reviewer is not a leak filter.** It checks for instructions, contradictions and meaningless
  writes, using only blocks the writer may read. It does not judge sensitivity, so it does not
  rescue a mislabelled write in the baseline.

## 3. What remains unproven

- **Scale.** Two agents, one model family, 11 synthetic blocks, keyword search, 10 tuning and 4 fresh
  cases, 3 runs each ([N] runs in total). We have not shown the result holds with many agents,
  thousands of blocks, vector search or other models.
- **Statistical strength.** With 3 runs per case, a difference of one or two leaking runs is within
  run-to-run variation. [Report counts, e.g. "self leaked in a of b runs, taint in c of d", rather
  than only percentages.]
- **Real data.** All blocks and canaries were written by us. Real company documents mix sensitive
  and public facts inside one block, which a block-level label cannot separate.
- **Adaptive pressure.** We tested one injection block (c09) and two "make it public" prompts (c08,
  f04). A determined attacker, or an agent that rewrites facts to dodge exact matches, was not
  tested.
- **The rest of the design.** Staging, the management-agent reviewer as its own process, rating
  promotion and the third agent are Phase 2 work and are not covered by this benchmark.
