---
name: sonar-local-ops
description: >-
  Operate local SonarQube for agents: create/bind projects, use the local
  token, start the Docker server, run scanner analysis, list issues, and
  resolve/accept findings. Use when working with local Sonar, sonar-scan,
  sonar-project, sonar-issues, commissioning a machine, or fixing issues
  Sonar found on a local project.
---

# Local SonarQube operations

Use this skill for the **local Docker SonarQube** stack (`http://127.0.0.1:9000`)
managed by **EasyScanPKG**. Local results are **not** a CI quality-gate pass.

## Bridge + env

```bash
[ -f ~/.config/sft/bridge.env ] && . ~/.config/sft/bridge.env
BRIDGE="${SFT_AGENT_BRIDGE:-${BRIDGE:-}}"
# Fall back to this checkout if bridge.env is missing:
# BRIDGE="$(cd "$(dirname "$0")/../.." && pwd)"   # when running from bin/
set -a
[ -f ~/.config/sft/sonar-local.env ] && . ~/.config/sft/sonar-local.env
[ -f ~/.config/sft/sonar.env ] && . ~/.config/sft/sonar.env
set +a
```

| File | Contents |
| --- | --- |
| `~/.config/sft/sonar-local.env` | Auto token + URL + local project key (`chmod 600`) |
| `~/.config/sft/sonar-local-admin.json` | Rotated local admin password (never commit) |
| `~/.config/sft/sonar.env` | Optional remote URL/token |
| `~/.config/sft/desktop.env` | Workspace for the EasyScan favorites icon |

**Never print full tokens in chat logs.** Refer to paths only.

## 1. Start / stop the server

```bash
"$BRIDGE/bin/sonar-local-up"      # Docker Postgres + SonarQube; bootstraps token
"$BRIDGE/bin/sonar-local-down"
"$BRIDGE/bin/sonar-desktop"       # up + MCP + open Cursor (daily / after reboot)
"$BRIDGE/bin/easyscan-check"      # one-click readiness gate
```

First boot can take several minutes. UI: http://127.0.0.1:9000

## 2. Token for API / scanner / MCP

After `sonar-local-up`, the user token is in `sonar-local.env` as `SONARQUBE_TOKEN`.

```bash
"$BRIDGE/bin/sonar-mcp-status" --workspace "$PWD" --json
```

MCP Cursor config must use `--network=host` so Docker can reach host Sonar
(EasyScanPKG template already sets this). Reload Cursor MCP after config changes.

## 3. Add a project to local Sonar

```bash
"$BRIDGE/bin/sonar-project" --local create 'local-my-app' \
  --name 'My App' \
  --workspace "$PWD"

"$BRIDGE/bin/sonar-project" --local list
"$BRIDGE/bin/sonar-project" --local bind "$PWD" 'local-my-app'
```

Keys should be URL-safe (`[A-Za-z0-9:_-]`). Prefer a `local-` prefix so they never
collide with remote project keys.

## 4. Run a scan

```bash
"$BRIDGE/bin/sonar-scan" --workspace "$PWD"
"$BRIDGE/bin/sonar-scan" --workspace "$PWD" --sources src,lib
"$BRIDGE/bin/sonar-scan" --workspace "$PWD" --project-key local-my-app --sources src
```

`sonar-scan` uses `sonarsource/sonar-scanner-cli` with `--network=host`, waits for
Compute Engine, then prints open issues.

## 5. Pull issues

```bash
"$BRIDGE/bin/sonar-issues" --local list
"$BRIDGE/bin/sonar-issues" --local list --severity CRITICAL,MAJOR --limit 50
"$BRIDGE/bin/sonar-issues" --local list --json | head
```

## 6. Resolve / accept issues

Prefer **code fixes**. Use `falsepositive` / `wontfix` / `accept` only with rationale.

```bash
"$BRIDGE/bin/sonar-issues" --local resolve <ISSUE_KEY>
```

## Language support (important)

| Language | On local Community image? | Notes |
| --- | --- | --- |
| Python / JS / TS / Java / C# / Go / … | Yes | Use `sonar-scan` |
| **C / C++ / Objective-C** | **Usually no** | Needs CFamily (Developer+ / Cloud). Build Wrapper is not vendored. |
| **Assembly (.s/.asm)** | **No** | No first-party analyzer |
| **Julia** | **No** | No official Sonar plugin |

```bash
"$BRIDGE/bin/sonar-languages" --json
```

## Agent workflow

1. `sonar-local-up` (or `sonar-desktop`)
2. `sonar-project --local create local-<name> --workspace "$PWD"`
3. `sonar-scan --workspace "$PWD" --sources <tree>`
4. `sonar-issues --local list --severity CRITICAL,MAJOR`
5. Fix code → re-scan
6. Summarize remaining CRITICAL/MAJOR with file:line

## Related skills

- `sonar-mcp-lifecycle` — MCP up/down, credentials, policy DB
- `sonar-agent-analysis` — end-of-task analyze via IDE/MCP
