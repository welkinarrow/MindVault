#!/usr/bin/env bash
# The recorded demo, start to finish, with no human input after launch.
# Run it with the service already running (fresh: SEED_FILE=data/seed_blocks_hr.json reset.py, then restart the service).
#   agents/demo.sh
set -euo pipefail
cd "$(dirname "$0")/.."

TASK="Why did voluntary attrition among Austin engineers go up in Q3? Answer using shared memory, write your conclusion back, and rate the material you used."
INTERN_TASK="Read memory block w-0001, then search shared memory for: Austin attrition exit interview. Report exactly what you get."

say() { printf '\n\033[1;36m%s\033[0m\n' "$1"; sleep 1.5; }
cmd() { printf '\033[1;32m$\033[0m %s\n' "$1"; sleep 1; }

clear
say "1) HR agent (L2) gets ONE instruction. Every tool call after that is its own."
cmd "agents/run.sh hr \"$TASK\""
agents/run.sh hr "$TASK" | grep -v '^session_id:'

say "2) Check: the intern agent (L0) tries to read the new conclusion"
cmd "agents/run.sh intern \"$INTERN_TASK\""
agents/run.sh intern "$INTERN_TASK" | grep -v '^session_id:'

say "3) Check: verify.py"
cmd "DEMO_AGENT=hr python scripts/verify.py"
source "${MINDVAULT_ENV:-$HOME/.config/mindvault.env}"
DEMO_AGENT=hr .venv/bin/python scripts/verify.py || true
sleep 4
