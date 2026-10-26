"""Run review() on the samples in review/samples.json and print PASS/FAIL.

Usage (from the repo root, with LLM_API_KEY and LLM_MODEL set):
    python3 review/run_samples.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from reviewer import review  # noqa: E402


def main():
    with open(os.path.join(ROOT, "data", "seed_blocks.json"), encoding="utf-8") as f:
        seed = {b["block_id"]: b for b in json.load(f)}
    with open(os.path.join(HERE, "samples.json"), encoding="utf-8") as f:
        samples = json.load(f)

    failures = 0
    for s in samples:
        context = [{k: seed[i][k] for k in ("block_id", "title", "content", "level", "scope")}
                   for i in s["context_ids"]]
        result = review(s["title"], s["content"], context)
        ok = result["action"] in s["expected"]
        failures += not ok
        print("%-4s  %-9s  got=%-10s  expected=%s" % (
            "PASS" if ok else "FAIL", s["id"], result["action"], "/".join(s["expected"])))
        print("      reason: " + result["reason"])

    print("\n%d/%d samples as expected" % (len(samples) - failures, len(samples)))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
