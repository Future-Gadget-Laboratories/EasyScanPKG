---
name: sonar-mcp-lifecycle
description: >-
  Start, stop, and status the EasyScanPKG / SonarQube MCP stack for
  Cursor and Codex. Use when setting up Sonar MCP, spinning services up on
  demand, updating credentials, starting local Docker fallback, or managing
  the persistent policy DB.
---

# Sonar MCP lifecycle

## When to use

- User asks to start/stop/check Sonar MCP or SonarLint for agents
- First-time wiring of Cursor + Codex to Sonar
- Remote token missing, expired, or server unreachable
- Need local Docker SonarQube fallback

## Bridge location

```bash
[ -f ~/.config/sft/bridge.env ] && . ~/.config/sft/bridge.env
BRIDGE="${SFT_AGENT_BRIDGE:-${BRIDGE:-}}"
# If unset, use your EasyScanPKG checkout path.
```

## Install (new machine)

```bash
cd "$BRIDGE"
./install.sh --workspace /path/to/project --project-key your.project.key
# optional: --install-zenity for credential dialogs
```

## On-demand spin-up (preserves policy DB)

```bash
"$BRIDGE/bin/sonar-mcp-up" --workspace "$PWD"
```

Resolution order:
1. **Remote** — `~/.config/sft/sonar.env` URL + user token
2. **Prompt** — GUI dialog (zenity/kdialog/tkinter) if missing or rejected (expired token)
3. **Local fallback** — Docker SonarQube at `http://127.0.0.1:9000` + auto token in `sonar-local.env`
4. **Standalone** — IDE-only SonarLint rules if all else fails

Skip prompts in CI: `--no-prompt`. Skip local fallback: `--no-local-fallback`.

## Credentials

```bash
"$BRIDGE/bin/sonar-credentials" --test    # dialog + validate
"$BRIDGE/bin/sonar-local-up"              # local server only
"$BRIDGE/bin/sonar-local-down"
```

Never commit tokens. Policy DB stores URLs/prefs only — tokens live in env files (`chmod 600`).

## Client config

- Cursor: merged into `~/.cursor/mcp.json` on spin-up
- Codex: `~/.codex/config.toml` — source `~/.config/sft/sonar.env` before `codex`
- Hook: `~/.cursor/hooks.json` → `sonarqube_analysis_hook.py` (fail-open)

## Policy CLI

```bash
"$BRIDGE/bin/sonar-policy" show
"$BRIDGE/bin/sonar-policy" set connection fallback_to_local_server true
"$BRIDGE/bin/sonar-policy" bind "$PWD" 'your.project.key'
```

## Named contexts (multi-project)

```bash
"$BRIDGE/bin/sonar-context" create myapp --project-key local-myapp --gh org/myapp --use
"$BRIDGE/bin/sonar-context" list
"$BRIDGE/bin/sonar-context" bind myapp "$PWD" --project-key local-myapp
```

Contexts store URL + `token_ref` (path / `env:VAR`) — never raw tokens in `policy.db`.

## Verify

1. `sonar-mcp-up --json` shows `backend`: `remote` | `local` | `standalone`
2. With SonarQube for IDE open: `sonar-ide-port` prints port 64120–64130
3. Cursor chat: MCP `ping_system`
4. `easyscan-check` soft-reports active context + optional issue checklist

## Related

- Analysis workflow: `sonar-agent-analysis`
- Local projects/scan/issues: `sonar-local-ops`
- C# construction: `csharp-sonarqube`
