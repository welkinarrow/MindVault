"""Build bench/seeds/seed_with_b320.json = data/seed_blocks.json + bench/seeds/b-320.json.

Case 9 (injection) runs with SEED_FILE=bench/seeds/seed_with_b320.json.
Every other case uses the normal data/seed_blocks.json, so the injection block
cannot affect them. Re-run this script whenever data/seed_blocks.json changes.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
base = json.loads((ROOT / "data" / "seed_blocks.json").read_text(encoding="utf-8"))
extra = json.loads((ROOT / "bench" / "seeds" / "b-320.json").read_text(encoding="utf-8"))
assert extra["block_id"] not in {b["block_id"] for b in base}, "b-320 already in base seed"
out = ROOT / "bench" / "seeds" / "seed_with_b320.json"
out.write_text(json.dumps(base + [extra], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out.relative_to(ROOT)}: {len(base) + 1} blocks")
