"""Build hw8/seed_with_pricing.json = data/seed_blocks.json + hw8/b-701.json.

HW8 (Nanda Town integration) runs with SEED_FILE=hw8/seed_with_pricing.json.
The extra block is the L2 finance price list the Town seller reads by ID.
Re-run this script whenever data/seed_blocks.json changes.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
base = json.loads((ROOT / "data" / "seed_blocks.json").read_text(encoding="utf-8"))
extra = json.loads((ROOT / "hw8" / "b-701.json").read_text(encoding="utf-8"))
assert extra["block_id"] not in {b["block_id"] for b in base}, "b-701 already in base seed"
out = ROOT / "hw8" / "seed_with_pricing.json"
out.write_text(json.dumps(base + [extra], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out.relative_to(ROOT)}: {len(base) + 1} blocks")
