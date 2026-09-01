---
name: easyscan-bootstrap
description: >-
  Activate EasyScanPKG / local SonarQube in any Cursor or Codex agent
  environment. Use when starting Sonar, commissioning a machine, switching
  projects/contexts, or when an agent needs analysis before coding.
---

# EasyScanPKG bootstrap (activate Sonar)

Use this skill in **any agent environment** that has (or can clone) EasyScanPKG.
It brings up local Sonar, binds the workspace, and points you at the fix list.

## 0. Locate the bridge

```bash
[ -f ~/.config/sft/bridge.env ] && . ~/.config/sft/bridge.env
BRIDGE="${SFT_AGENT_BRIDGE:-${BRIDGE:-}}"
# Fallback: path to the EasyScanPKG checkout
: "${BRIDGE:?Set BRIDGE to your EasyScanPKG root}"
export BRIDGE SFT_AGENT_BRIDGE="$BRIDGE"
```

First-time on a machine:

```bash
cd "$BRIDGE"
chmod +x commission.sh install.sh bin/*
./commission.sh --workspace "$PWD" --skip-remote-creds --no-prompt
"$BRIDGE/bin/install-skills.sh"
"$BRIDGE/bin/easyscan-check --require-local"
```

## 1. Start Sonar (local Docker Community)

```bash
"$BRIDGE/bin/sonar-local-up"          # Postgres + SonarQube :9000 + token
# or daily launcher:
"$BRIDGE/bin/sonar-desktop"           # up + MCP + skills + open Cursor
```

UI: http://127.0.0.1:9000  
Token file (never print): `~/.config/sft/sonar-local.env`

### If the local token is missing / rejected

```bash
"$BRIDGE/bin/sonar-credentials" --paths                  # where tokens live
"$BRIDGE/bin/sonar-credentials" --local --bootstrap --test
# or paste a UI-generated token:
"$BRIDGE/bin/sonar-credentials" --local --cli --test
set -a; . ~/.config/sft/sonar-local.env; set +a
```

## 2. Named context (multi-project)

```bash
"$BRIDGE/bin/sonar-context" create <name> \
  --url http://127.0.0.1:9000 \
  --token-ref ~/.config/sft/sonar-local.env \
  --project-key local-<repo> \
  --gh <org>/<repo> \
  --remediation "$BRIDGE/templates/remediation.easyscanpkg.json" \
  --use

"$BRIDGE/bin/sonar-project" --context <name> create local-<repo> \
  --workspace "$PWD"
```

Switch later: `"$BRIDGE/bin/sonar-context" use <name>`

## 3. Optional MCP / IDE wiring

```bash
"$BRIDGE/bin/sonar-mcp-up" --workspace "$PWD" --json
"$BRIDGE/bin/sonar-ide-port" --diagnose
```

## 4. Verify

```bash
"$BRIDGE/bin/easyscan-check" --require-local
curl -s http://127.0.0.1:9000/api/system/status   # expect "UP"
```

## Nested Docker / cloud VMs

If containers fail to talk (Postgres timeout) or overlay mount errors:

- Prefer Docker **vfs** storage or a non-overlay data-root
- `sysctl -w net.bridge.bridge-nf-call-iptables=0`
- `sysctl -w vm.max_map_count=524288`

## Next

- Scan + fix queue → skill **`sonar-fix-queue`**
- Local ops details → **`sonar-local-ops`**
- MCP lifecycle → **`sonar-mcp-lifecycle`**
- End-of-task file analyze → **`sonar-agent-analysis`**
