"""Admin tool: show or clear an agent's taint (highest level read + blocks read).

Taint is stored in the database and survives idle time and service restarts, because an
agent may still hold what it read. Clear it only when the agent starts a new task with a
clean context (for example, a fresh agent process). reset.py clears everyone.

    python scripts/reset_taint.py --show            # list every agent's taint
    python scripts/reset_taint.py finance           # clear one agent
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.config import Config, load_agents  # noqa: E402
from server.db import Store  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("agent_id", nargs="?", help="agent to clear")
    parser.add_argument("--show", action="store_true", help="list taint for every agent")
    args = parser.parse_args()
    if not args.agent_id and not args.show:
        parser.error("give an agent_id or --show")

    cfg = Config.from_env()
    store = Store(cfg.db_path)
    if args.agent_id:
        before = store.get_taint(args.agent_id)
        store.reset_taint(args.agent_id)
        print(f"{args.agent_id}: L{before[0]} ({len(before[1])} blocks) -> cleared")
    if args.show:
        for agent in (a["agent_id"] for a in load_agents(cfg.agents_file)):
            level, ids = store.get_taint(agent)
            print(f"{agent}: L{level}  read={','.join(ids) or '-'}")


if __name__ == "__main__":
    main()
