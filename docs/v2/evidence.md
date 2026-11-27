# MindVault v2: Demo Day evidence

**Team:** Jack (A: interface, policy, local model and agent, trace view, tests and evaluation),
Joyce Chen (B: memory manager, demo data, human queue, T8–T9, slides),
Tripti Khetan (C: service v2, cloud assistant, held-out cases).
**Branch / tag:** `v2`, frozen as `demo-base` on 12/1. **Interface:** section 13 of [`docs/interfaces.md`](../interfaces.md).

Fill every **TODO** with what actually happened, including failures. Numbers come from the files named
next to them, not from memory. Transcripts in `runs/` may hold internal content: link redacted copies only.

## 1. What v2 claims, and where each claim is checked

| Claim | Checked by | Result |
| --- | --- | --- |
| A cloud-model agent cannot read internal memory, even with the same level and scope | T6, scenario 1 | TODO |
| Internal status propagates: a summary of internal content is internal | T6 | TODO |
| Internal writes and corrections never reach the cloud reviewer | T7 (`logs/review_usage.jsonl` unchanged, no REVIEW event, route=human) | TODO |
| Pending writes are invisible to every agent until a human approves | T7 | TODO |
| Citations: at most 10, no duplicates, only memories read in the session | T8 | TODO |
| Source chains are recorded and filtered by permission when traced | T9, scenario 2 | TODO |
| Memory manager suggestions change nothing until approved; downgrades must pass the code check | T10 | TODO |
| The local 8B model judges well enough to be useful, and never causes a wrong downgrade | Evaluation (section 3) | TODO |
| HW7–HW9 behaviour still holds | `scripts/verify.py` (T1–T5), unit tests | TODO |

## 2. Test results

Run after `reset.py` with the scenario 1 seeds and the service started:

```bash
python -m unittest discover -s tests      # unit tests, no model or service
python scripts/run_tests.py               # T6–T10
python scripts/verify.py                  # T1–T5, after the finance demo
```

| Suite | Date / commit | Pass | Fail | Skip | Notes |
| --- | --- | --- | --- | --- | --- |
| Unit tests | TODO | TODO | TODO | — | |
| T1–T5 (`verify.py`) | TODO | TODO | TODO | — | |
| T6, T7, T10 (A) | TODO | TODO | TODO | TODO | T7's control row must PASS (LLM key set), or "usage unchanged" proves nothing |
| T8, T9 (B) | TODO | TODO | TODO | TODO | |

Paste the full `run_tests.py` output into `docs/v2/evidence/run_tests.txt`.

## 3. Memory manager evaluation

- Pass bar: [`eval/criteria.md`](../../eval/criteria.md), committed at **TODO commit** before the first run.
- Held-out cases: **TODO n** cases written by C, never shown to B, now in `eval/cases_heldout/`.
- Command: `python eval/run_eval.py --judge TODO --cases eval/cases_heldout --runs 3 --cloud`

Copy the table from `eval/results/<run>/summary.md`:

| Metric | Run 1 | Run 2 | Run 3 | Bar |
| --- | --- | --- | --- | --- |
| Keep agreement | TODO | TODO | TODO | ≥ 80% |
| Duplicate / conflict agreement | TODO | TODO | TODO | ≥ 80% |
| Downgrade agreement before the code check | TODO | TODO | TODO | — |
| Downgrade agreement after the code check | TODO | TODO | TODO | ≥ 80% |
| False downgrades before / after the code check | TODO | TODO | TODO | after = 0 |
| Invalid output rate | TODO | TODO | TODO | ≤ 10% |
| Mean seconds per case | TODO | TODO | TODO | — |

**Cloud reviewer comparison** (external-only cases, keep/discard only): TODO.

If a run missed the bar, say so here, what changed in the prompt (tuning cases only), and keep both results.

## 4. The two demo scenarios on the real service

| Scenario | Runs | Client | Every step as in the runbook? | Notes |
| --- | --- | --- | --- | --- |
| 1: internal/external | TODO / 3 | local_agent / scripted | TODO | |
| 2: memory tracing | TODO / 3 | local_agent / scripted | TODO | answer_id, b-610 ID |

Screenshots to add in `docs/v2/evidence/`: the refused read in scenario 1, the trace view for scenario 2,
the trace view of a downgraded sample from the cloud key (hidden sources). TODO

Local model on the demo Mac (`scripts/local_model_up.sh`): warm-up TODO s, short request TODO s,
memory TODO GB. Measured on Jack's MacBook Air on 11/27: 3.1 s, 0.1 s, 6.2 GB.

## 5. Gaps between the design and this build

State these plainly on the "built / not yet built" slide:

1. In the design every write needs a super admin; here only internal writes do, and external writes are published after cloud review.
2. The maintenance agent is not built; the memory manager reviews internal writes only and is not tuned on feedback.
3. The service trusts the `model_location` registered for each key; it cannot verify which model is behind a key.
4. Internal corrections are not applied (NEEDS_HUMAN_REVIEW) until human approval of corrections exists. TODO: update if Q-7 changes this.
5. TODO: anything found in rehearsal.

## 6. How to reproduce

Follow [`docs/v2/demo-runbook.md`](demo-runbook.md), sections 0–3.

## 7. Contributions

TODO: one line per person, from the commit log.
