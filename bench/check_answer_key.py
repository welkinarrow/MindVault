"""Sanity checks for the HW7 cases and answer key. Run before committing them:
    python bench/check_answer_key.py

Fails (exit 1) if:
  - a canary appears in any L0 (public) block, so it could not mark a leak;
  - an L1+ block has no canary, or none of its canaries appears in its own text;
  - a case needs a block that is not in its seed file;
  - a required fact cannot be found in the case's needed blocks;
  - tuning and fresh case ids overlap.
Also prints, per case, the levels a search with the question's words would return
to the finance agent: a public case that pulls L1+ blocks will be over-labeled
under LABEL_MODE=taint.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server.search import search  # noqa: E402

FINANCE = {"agent_id": "finance", "level": 2,
           "read_scopes": ["public", "finance", "support", "release"], "write_scope": "finance"}


def load(p):
    return json.loads((ROOT / p).read_text(encoding="utf-8"))


def text(b):
    return f"{b['title']} {b['content']}".lower()


def main() -> int:
    key = load("bench/answer_key.json")
    tuning, fresh = load("bench/cases.json"), load("bench/fresh/cases.json")
    seeds = {}
    errors = []

    for case in tuning + fresh:
        seeds.setdefault(case["seed_file"], load(case["seed_file"]))
    all_blocks = {b["block_id"]: b for blocks in seeds.values() for b in blocks}

    for b in all_blocks.values():
        if b["level"] == 0:
            for bid, pats in key["canaries_by_block"].items():
                for p in pats:
                    if p.lower() in text(b):
                        errors.append(f"canary {p!r} ({bid}) appears in public block {b['block_id']}")
        else:
            pats = key["canaries_by_block"].get(b["block_id"])
            if not pats:
                errors.append(f"L{b['level']} block {b['block_id']} has no canary")
            elif not any(p.lower() in text(b) for p in pats):
                errors.append(f"no canary of {b['block_id']} appears in its own text")

    ids = [c["id"] for c in tuning + fresh]
    if len(ids) != len(set(ids)):
        errors.append("duplicate case ids between tuning and fresh sets")

    print(f"{'case':5} {'type':15} levels a question-word search returns to finance")
    for case in tuning + fresh:
        blocks = {b["block_id"]: b for b in seeds[case["seed_file"]]}
        missing = [x for x in case["needed_blocks"] if x not in blocks]
        if missing:
            errors.append(f"{case['id']}: needed blocks missing from seed: {missing}")
            continue
        corpus = " ".join(text(blocks[x]) for x in case["needed_blocks"])
        for group in case["required_facts"]:
            if not any(alt.lower() in corpus for alt in group):
                errors.append(f"{case['id']}: required fact {group} not in its needed blocks")
        q = case["question"].split(" Answer using")[0]
        hits = search(q, list(blocks.values()), FINANCE).results
        shown = ", ".join(f"{h['block_id']}(L{h['level']})" for h in hits) or "-"
        warn = "  <- public case pulls L1+" if case["expect_intern_readable"] and any(h["level"] for h in hits) else ""
        print(f"{case['id']:5} {case['type']:15} {shown}{warn}")

    if errors:
        print("\nFAIL")
        for e in errors:
            print("  -", e)
        return 1
    print(f"\nPASS: {len(tuning)} tuning + {len(fresh)} fresh cases, "
          f"{sum(map(len, key['canaries_by_block'].values()))} canaries over "
          f"{len(key['canaries_by_block'])} blocks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
