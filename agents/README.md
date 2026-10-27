# Part C: Hermes agents

Two Hermes profiles call the memory service over MCP: `finance` (L2) runs the demo task,
`intern` (L0) shows the new conclusion can't be read. Tested with Hermes Agent v0.22.5.

| File | Purpose |
| --- | --- |
| `finance/SOUL.md`, `intern/SOUL.md` | Each agent's instructions (copied into its Hermes profile) |
| `run.sh` | Runs one agent on one task; passes the LLM key for that launch only |
| `../scripts/verify.py` | Result check: prints PASS / FAIL per item |

## One-time setup

1. Install Hermes: `curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`
2. Create the profiles: `hermes profile create finance --no-alias --no-skills` (same for `intern`).
3. Copy `agents/<name>/SOUL.md` to `~/.hermes/profiles/<name>/SOUL.md`.
4. Replace `~/.hermes/profiles/<name>/config.yaml` with the block below. Put that agent's key
   from `config/agents.json` after `Bearer`. These files hold keys, so they stay out of the repo.

```yaml
model:
  default: claude-sonnet-5-5
  provider: anthropic
_config_version: 51
memory:
  memory_enabled: false
  user_profile_enabled: false
  nudge_interval: 0
skills:
  creation_nudge_interval: 0
agent:
  max_turns: 15
  disabled_toolsets: [web, browser, terminal, file, code_execution, vision, image_gen, tts, skills, todo, memory, session_search, connections, clarify, delegation, cronjob, computer_use]
mcp_servers:
  mindvault:
    url: "http://127.0.0.1:8000/mcp"
    headers:
      Authorization: "Bearer <agent key>"
    tools:
      include: [memory_read, memory_write, memory_rate]
      resources: false
      prompts: false
```

5. Check: `hermes -p intern tools list` shows no enabled built-in toolsets, and only
   `mindvault [include only: memory_read, memory_write, memory_rate]` under MCP servers.
6. Create an env file (default `~/.config/mindvault.env`, outside the repo) that exports
   `ANTHROPIC_API_KEY`, plus `LLM_PROVIDER=anthropic`, `LLM_MODEL=claude-sonnet-4-6`,
   `LLM_API_KEY` and `MEMORY_LANG=en` for the service. Point `MINDVAULT_ENV` at it if it lives elsewhere.

Q-1 answer: Hermes sends custom headers for URL-based MCP servers, so no URL-key middleware is needed.

## Demo run

```bash
source ~/.config/mindvault.env
python scripts/reset.py                 # service stopped
python -m server.app                    # terminal 2, leave running
agents/run.sh finance "Why did the Q3 refund rate in East China go up? Answer using shared memory, write your conclusion back, and rate the material you used."
agents/run.sh intern "Read memory block w-0001, then search shared memory for: East China refund rate. Report exactly what you get."
python scripts/verify.py                # expect ALL PASS
```

Restart the service before every run: sessions live in memory, and verify.py reads the
session's reads from `logs/server.log` after the last `[START]`.

## Notes

- **Reviewer model:** `claude-sonnet-5-5` rejects the `temperature` parameter that
  `review/reviewer.py` sends (HTTP 400, so every write is quarantined). Use
  `claude-sonnet-4-6` for the reviewer. The agents themselves run fine on `claude-sonnet-5-5`.
- **Hermes and the key:** Hermes ignores `ANTHROPIC_API_KEY` from the shell, so `run.sh`
  passes it with `--api-key` (launch only, never saved to the profile).
- **Tool discovery:** Hermes exposes MCP tools as deferred tools (`mcp__mindvault__memory_read`,
  reached through `tool_search` / `tool_call`). The agents still find and call them on their own.
