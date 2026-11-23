"""First-run setup. Creates whatever is missing and never overwrites anything.

    python scripts/init.py          # new install: one "assistant" agent, empty memory
    python scripts/init.py --demo   # the team's demo agents and seed data

Paths come from AGENTS_FILE (default config/agents.json) and MEMORY_DB (default
data/memory.db). Keys are printed once, when they are created. Used by install.sh and
by the Docker image on every start, so it must be safe to run again.
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.config import scopes_from, validate_agents  # noqa: E402

STARTER = {
    "scopes": ["public"],
    "agents": [{"agent_id": "assistant", "level": 0, "read_scopes": ["public"], "write_scope": "public"}],
}


def resolve(env_name: str, default: str) -> Path:
    path = Path(os.environ.get(env_name, default))
    return path if path.is_absolute() else ROOT / path


def create_agents(path: Path, demo: bool) -> None:
    config = (json.loads((ROOT / "config" / "agents.example.json").read_text(encoding="utf-8"))
              if demo else json.loads(json.dumps(STARTER)))
    for a in config["agents"]:
        a["key"] = secrets.token_hex(16)
    validate_agents(config["agents"], scopes_from(config))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    print(f"created {path}. Agent keys (shown once, keep them private):")
    for a in config["agents"]:
        print(f"  {a['agent_id']:<14} {a['key']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--demo", action="store_true", help="use the demo agents and seed data")
    args = parser.parse_args()

    agents_path = resolve("AGENTS_FILE", "config/agents.json")
    db_path = resolve("MEMORY_DB", "data/memory.db")

    if agents_path.exists():
        print(f"keeping existing {agents_path}")
    else:
        create_agents(agents_path, args.demo)

    if db_path.exists():
        print(f"keeping existing {db_path}")
    else:
        cmd = [sys.executable, str(ROOT / "scripts" / "reset.py")] + ([] if args.demo else ["--empty"])
        env = {**os.environ, "AGENTS_FILE": str(agents_path), "MEMORY_DB": str(db_path)}
        if args.demo:
            env.setdefault("SEED_FILE", str(ROOT / "data" / "seed_blocks.json"))
        sys.stdout.flush()  # keep our lines before reset.py's
        subprocess.run(cmd, env=env, check=True)


if __name__ == "__main__":
    main()
