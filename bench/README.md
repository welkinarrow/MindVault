# HW7 benchmark data (owner: B)

| File | Purpose |
| --- | --- |
| `cases.json` | 10 tuning cases: 5 ordinary, 2 public, 3 hard (pressure, injection, mixed) |
| `fresh/cases.json` | 4 held-out rewordings of c01, c03, c06, c08. **Run only for the retest** |
| `answer_key.json` | Required facts per case, canaries for every L1+ block, the leak and over-restriction rules, and the fixed intern probe queries |
| `seeds/b-320.json` | Injection block used only by case c09 |
| `seeds/seed_with_b320.json` | Base seed + b-320; built by `build_seeds.py`. c09 runs with `SEED_FILE` set to this |
| `check_answer_key.py` | Sanity checks; must print PASS before the key is committed |
| `run_bench.py` | (C) Runs condition x case x run; one folder per run in `bench/results/<run_id>/` |
| `probe.py` | (A; written on A's behalf by Claude, 2026-11-03) L0 intern probe: reads every new `w-` block by id and runs the 3 fixed queries; saves raw responses to `probe.json`, decides nothing |
| `score.py` | (A; written on A's behalf by Claude, 2026-11-03) Applies the answer-key rules to every run folder; writes `results/results.csv` and `results/results_summary.csv` |

Each case's `question` is sent to the finance agent as is. `seed_file` is the `SEED_FILE`
to use for `scripts/reset.py`. Matching is case-insensitive substring (see `answer_key.json`).

**Freeze rule:** once `answer_key.json` and the case files are committed, they do not change.
Record that commit hash in the HW7 PDF as the pre-registered success checks. Any later change
is a new version, reported as such.

Pre-check (`python bench/check_answer_key.py`): a search with the words of every public case
(c06, c07, f03) already returns L1/L2 blocks, so under `LABEL_MODE=taint` we expect those
conclusions to be over-labeled. This is the predicted limit; the benchmark has to confirm it.

## Scoring (`score.py`)

```bash
python bench/score.py                    # all runs
python bench/score.py --tags full fresh  # only some tags
```

`results.csv` columns used by the write-up and the frontend: `condition`, `tag`, `leak`, `correct`,
`over_restricted`, `wall_time_s`, `tool_calls`, plus `leaked_blocks`, `write_levels`, `facts_hit`
and reviewer token counts. Empty means "not applicable / not measurable", never 0:
`leak` is empty when `probe.json` is missing or has errors; `over_restricted` is empty for cases
that are not public and for runs with no stored, non-quarantined finance write.

Two choices the answer key does not fix (also in the `score.py` docstring, report them):
correctness is matched against the whole `transcript.txt`, and quarantined writes do not count
as over-restricted.

Before the full run, check that `probe.log` in a pilot folder shows `scopes=['public', 'finance']`.
If it prints a WARNING, `config/agents.json` on the bench machine still has the old intern scope.
