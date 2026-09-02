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
"$BRIDGE/bin/sonar-local-up"      # Docker Postgres + SonarQube; bootstraps token + prints UI admin login
"$BRIDGE/bin/sonar-local-down"
"$BRIDGE/bin/sonar-desktop"       # up + MCP + open Cursor (daily / after reboot)
"$BRIDGE/bin/easyscan-check"      # one-click readiness gate
```

First boot can take several minutes. UI: http://127.0.0.1:9000  
Login is **username `admin`** plus the password printed by `sonar-local-up` (also in `~/.config/sft/sonar-local-admin.json`).

## 2. Access token for API / scanner / MCP / agent skills

Local agent tokens live in `~/.config/sft/sonar-local.env` as `SONARQUBE_TOKEN`
(`chmod 600`). **Never print the token value into chat** — refer to the path only.

### Preferred: auto-mint from local Sonar

```bash
"$BRIDGE/bin/sonar-local-up"   # starts Docker Sonar + bootstraps token
# or explicitly:
"$BRIDGE/bin/sonar-credentials" --local --bootstrap --test --project-key local-my-app
"$BRIDGE/bin/sonar-credentials" --paths   # show env file paths (no secrets)
```

`--local --bootstrap` starts local Sonar if needed, generates a user token via
the API, writes `sonar-local.env`, and refreshes Cursor/Codex MCP env.

### Paste an existing local user token

Create a token in the local UI (**My Account → Security → Generate Tokens**)
or via API, then:

```bash
"$BRIDGE/bin/sonar-credentials" --local --cli --test
# or non-interactive:
"$BRIDGE/bin/sonar-credentials" --local \
  --url http://127.0.0.1:9000 \
  --token "$SONARQUBE_TOKEN" \
  --project-key local-my-app \
  --test
```

### Load into the current agent shell (without echoing)

```bash
set -a
[ -f ~/.config/sft/sonar-local.env ] && . ~/.config/sft/sonar-local.env
set +a
# Confirm presence only:
"$BRIDGE/bin/sonar-credentials" --paths --json
"$BRIDGE/bin/sonar-mcp-status" --workspace "$PWD" --json
```

Point a named context at the local token file (stores a **ref**, not the secret):

```bash
"$BRIDGE/bin/sonar-context" create local \
  --url http://127.0.0.1:9000 \
  --token-ref ~/.config/sft/sonar-local.env \
  --project-key local-my-app \
  --use
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

## 5b. Export checklist (agent work queue)

```bash
"$BRIDGE/bin/sonar-issues" --local export --workspace "$PWD" --refresh
# → .sft/issue-checklist.md (+ .json). Re-export after fixes.
# Done when open_count is 0 / checklist body says complete.
```

Treat unchecked markdown items as the work queue. Prefer code fixes over
`resolve`/`accept` transitions.

## 5c. Contexts (multiple GH / Sonar targets)

```bash
"$BRIDGE/bin/sonar-context" create myapp --project-key local-myapp --gh org/myapp --use
"$BRIDGE/bin/sonar-context" list
"$BRIDGE/bin/sonar-scan" --context myapp --workspace "$PWD" --sources src
"$BRIDGE/bin/sonar-issues" --context myapp export --workspace "$PWD" --refresh
```

## 5d. Quality profile XML

```bash
"$BRIDGE/bin/sonar-profile" --local list
"$BRIDGE/bin/sonar-profile" --local import /path/to/profile.xml \
  --bind-project local-myapp --set-default
```

Optional companion remediation hints: `templates/remediation.example.json`
(rule → fix guidance), referenced via `sonar-context create … --remediation PATH`
or `sonar-profile import … --remediation PATH`.

## 6. Resolve / accept issues

Prefer **code fixes**. Use `falsepositive` / `wontfix` / `accept` only with rationale.

```bash
"$BRIDGE/bin/sonar-issues" --local resolve <ISSUE_KEY>
```

## Language support (important)

| Language | On local Community image? | Notes |
| --- | --- | --- |
| Python / JS / TS / Java / C# / Go / … | Yes | Use `sonar-scan` |
| **C / C++** | **Yes via sonar-cxx** | Auto-installed on `sonar-local-up` from SonarOpenCommunity/sonar-cxx (`cxx` language). Not commercial CFamily/Build Wrapper. `sonar-languages --install-cxx` to force. Disable: `SFT_INSTALL_SONAR_CXX=0`. |
| **Objective-C** | **No** | Needs commercial CFamily |
| **Assembly (.s/.asm)** | **No** | No first-party analyzer |
| **Julia** | **No** | No official Sonar plugin |

```bash
"$BRIDGE/bin/sonar-languages" --json
"$BRIDGE/bin/sonar-languages" --local --install-cxx
```

## Agent workflow

1. `sonar-local-up` (or `sonar-desktop`)
2. `sonar-context create|use` (optional but recommended for multi-repo work)
3. `sonar-project --local create local-<name> --workspace "$PWD"`
4. Optional: `sonar-profile import … --bind-project local-<name>`
5. `sonar-scan --workspace "$PWD" --sources <tree>`
6. `sonar-issues export --workspace "$PWD" --refresh`
7. Fix from checklist → re-scan → re-export until `open_count=0`
8. Summarize remaining CRITICAL/MAJOR with file:line if any remain

## Related skills

- `easyscan-bootstrap` — activate Sonar / contexts in any agent environment
- `sonar-fix-queue` — checklist location + fix-until-empty loop
- `sonar-mcp-lifecycle` — MCP up/down, credentials, policy DB
- `sonar-agent-analysis` — end-of-task analyze via IDE/MCP
