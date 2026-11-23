#!/usr/bin/env bash
# One-command install for MindVault. Safe to run again: it only creates what is missing.
#   ./install.sh          new install: one "assistant" agent, empty memory
#   ./install.sh --demo   the team's demo agents and seed data
set -euo pipefail
cd "$(dirname "$0")"

# 1. A Python that is 3.10 or newer (macOS ships 3.9 as python3)
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    PY="$cand"; break
  fi
done
if [ ! -x .venv/bin/python ]; then
  if [ -n "$PY" ]; then
    echo "==> creating .venv with $($PY --version)"
    "$PY" -m venv .venv
  elif command -v uv >/dev/null 2>&1; then
    echo "==> no Python 3.10+ found; creating .venv with uv"
    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python pip >/dev/null
  else
    echo "MindVault needs Python 3.10 or newer. Install it (for example: brew install python@3.12), then run ./install.sh again." >&2
    exit 1
  fi
fi

# 2. Dependencies
echo "==> installing dependencies"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

# 3. Settings file for the reviewer's LLM key (never committed)
if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> created .env: put your LLM_API_KEY and LLM_MODEL in it"
fi

# 4. Agents and memory
.venv/bin/python scripts/init.py "$@"

cat <<'MSG'

Done. Next:
  1. Edit .env and set LLM_API_KEY and LLM_MODEL (the reviewer needs them; without them every write is quarantined)
  2. Start the service:   ./run.sh
  3. Add scopes/agents:   .venv/bin/python scripts/agents.py --help
MSG
