#!/usr/bin/env bash
# Start MindVault with the settings in .env.
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Run ./install.sh first." >&2; exit 1; }
if [ -f .env ]; then set -a; . ./.env; set +a; fi
if [ -z "${LLM_API_KEY:-}" ] || [ -z "${LLM_MODEL:-}" ]; then
  echo "WARNING: LLM_API_KEY or LLM_MODEL is not set in .env. The reviewer will quarantine every write." >&2
fi
exec .venv/bin/python -m server.app
