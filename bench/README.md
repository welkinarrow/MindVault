# HW7 benchmark data (owner: B)

| File | Purpose |
| --- | --- |
| `cases.json` | 10 tuning cases: 5 ordinary, 2 public, 3 hard (pressure, injection, mixed) |
| `fresh/cases.json` | 4 held-out rewordings of c01, c03, c06, c08. **Run only for the retest** |
| `answer_key.json` | Required facts per case, canaries for every L1+ block, the leak and over-restriction rules, and the fixed intern probe queries |
| `seeds/b-320.json` | Injection block used only by case c09 |
| `seeds/seed_with_b320.json` | Base seed + b-320; built by `build_seeds.py`. c09 runs with `SEED_FILE` set to this |
| `check_answer_key.py` | Sanity checks; must print PASS before the key is committed |

Each case's `question` is sent to the finance agent as is. `seed_file` is the `SEED_FILE`
to use for `scripts/reset.py`. Matching is case-insensitive substring (see `answer_key.json`).

**Freeze rule:** once `answer_key.json` and the case files are committed, they do not change.
Record that commit hash in the HW7 PDF as the pre-registered success checks. Any later change
is a new version, reported as such.

Pre-check (`python bench/check_answer_key.py`): a search with the words of every public case
(c06, c07, f03) already returns L1/L2 blocks, so under `LABEL_MODE=taint` we expect those
conclusions to be over-labeled. This is the predicted limit; the benchmark has to confirm it.
