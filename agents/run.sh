#!/usr/bin/env bash
# Run one Hermes agent on one task against the local MindVault service.
#
# Usage:  agents/run.sh finance "Why did the Q3 refund rate in East China go up? ..."
#         agents/run.sh intern  "Read block w-0001"
#
# Needs: Hermes profiles set up as in agents/README.md, the service running,
# and MINDVAULT_ENV (default ~/.config/mindvault.env) exporting ANTHROPIC_API_KEY.
# The LLM key is passed for this launch only; it is never written to Hermes's files.
set -euo pipefail

profile="${1:?usage: agents/run.sh <finance|intern> \"task\"}"
task="${2:?usage: agents/run.sh <finance|intern> \"task\"}"

# shellcheck disable=SC1090
source "${MINDVAULT_ENV:-$HOME/.config/mindvault.env}"
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY is not set; check MINDVAULT_ENV}"

exec hermes -p "$profile" chat -Q --api-key "$ANTHROPIC_API_KEY" -q "$task"
