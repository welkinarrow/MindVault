"""Manage agents and scopes in config/agents.json without hand-editing it.

    python scripts/agents.py list                                  # agents and scopes, never keys
    python scripts/agents.py add-scope legal                       # declare a new scope
    python scripts/agents.py add counsel --level 2 --read public,legal --write legal
    python scripts/agents.py add zhou-local --level 1 --read public,sales --write sales --model-location local
    python scripts/agents.py add manager-local --level 0 --read public --write public --model-location local --role manager
    python scripts/agents.py new-key counsel                       # replace one agent's key
    python scripts/agents.py remove counsel

A new key is printed once: hand it to whoever runs that agent, privately. Restart the
service after any change; it reads agents.json at startup. Use --file (or AGENTS_FILE)
to work on another config file.
"""
import argparse
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.config import DEFAULT_SCOPES, ConfigError, scopes_from, validate_agents  # noqa: E402
from server.policy import MODEL_LOCATIONS, ROLES  # noqa: E402

NEW_FILE = {"scopes": ["public"], "agents": []}


def load(path: Path) -> dict:
    if not path.exists():
        return json.loads(json.dumps(NEW_FILE))
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, config: dict) -> None:
    """Validate the whole file, then replace it atomically. On any error the file is untouched."""
    validate_agents(config["agents"], scopes_from(config))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def find(config: dict, agent_id: str) -> dict:
    for a in config["agents"]:
        if a["agent_id"] == agent_id:
            return a
    raise ConfigError(f"no agent named {agent_id!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", type=Path,
                        default=Path(os.environ.get("AGENTS_FILE", "config/agents.json")))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("add-scope")
    p.add_argument("name")
    p = sub.add_parser("add")
    p.add_argument("agent_id")
    p.add_argument("--level", type=int, required=True, choices=(0, 1, 2, 3))
    p.add_argument("--read", required=True, help="comma-separated scopes the agent may read")
    p.add_argument("--write", required=True, help="the one scope its writes go to")
    p.add_argument("--model-location", choices=MODEL_LOCATIONS, default="cloud",
                   help="where the agent's model runs; only local agents may read internal memory (default cloud)")
    p.add_argument("--role", choices=ROLES, default="business",
                   help="business (default), manager (memory manager tools only) or admin")
    for name in ("new-key", "remove"):
        sub.add_parser(name).add_argument("agent_id")
    args = parser.parse_args()

    path = args.file if args.file.is_absolute() else ROOT / args.file
    try:
        config = load(path)
        if "scopes" not in config:  # older file: make the default scopes explicit before changing it
            config = {"scopes": sorted(DEFAULT_SCOPES), **config}

        if args.cmd == "list":
            print("scopes: " + ", ".join(config["scopes"]))
            for a in config["agents"]:
                print(f"  {a['agent_id']:<14} L{a['level']}  read={','.join(a['read_scopes'])}  "
                      f"write={a['write_scope']}  {a.get('model_location', 'cloud')}  {a.get('role', 'business')}")
            return
        if args.cmd == "add-scope":
            if args.name in config["scopes"]:
                raise ConfigError(f"scope {args.name!r} already exists")
            config["scopes"].append(args.name)
            save(path, config)
            print(f"added scope {args.name}")
            return
        if args.cmd == "add":
            if any(a["agent_id"] == args.agent_id for a in config["agents"]):
                raise ConfigError(f"agent {args.agent_id!r} already exists")
            key = secrets.token_hex(16)
            config["agents"].append({
                "agent_id": args.agent_id, "key": key, "level": args.level,
                "read_scopes": [s.strip() for s in args.read.split(",") if s.strip()],
                "write_scope": args.write, "model_location": args.model_location, "role": args.role,
            })
            save(path, config)
            print(f"added {args.agent_id}. Key (shown once, hand it over privately):\n{key}")
            return
        if args.cmd == "new-key":
            key = secrets.token_hex(16)
            find(config, args.agent_id)["key"] = key
            save(path, config)
            print(f"new key for {args.agent_id} (the old one stops working after a restart):\n{key}")
            return
        if args.cmd == "remove":
            config["agents"].remove(find(config, args.agent_id))
            save(path, config)
            print(f"removed {args.agent_id}")
    except (ConfigError, json.JSONDecodeError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
