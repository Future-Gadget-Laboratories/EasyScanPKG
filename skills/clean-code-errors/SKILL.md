---
name: clean-code-errors
description: >-
  Download and use EasyScanPKG to clean Sonar errors from code. Use when
  an agent should install this skill, scan a workspace with the local
  Sonar server, and fix the issue checklist until it is empty. Local
  credentials only — do not use remote tokens.
---

# Clean code errors (local Sonar)

Use this skill to **install itself**, talk to a **local** EasyScanPKG Sonar
server, and **fix findings in code** until the checklist is empty.

Local results are not a CI quality-gate pass. Never print `SONARQUBE_TOKEN`
or admin passwords into chat.

## Install (any agent)

From an EasyScanPKG checkout:

```bash
./skills/clean-code-errors/install.sh
```

That copies this skill into:

| Agent | Directory |
| --- | --- |
| Cursor | `~/.cursor/skills/clean-code-errors` |
| Codex | `~/.codex/skills/clean-code-errors` |
| Other agents | `~/.agents/skills/clean-code-errors` |

One-shot download when the toolkit is not cloned yet:

```bash
git clone --depth 1 https://github.com/future-gadget-laboratories/easyscanpkg.git /tmp/easyscanpkg
/tmp/easyscanpkg/skills/clean-code-errors/install.sh
export BRIDGE=/tmp/easyscanpkg
```

The rest of the toolkit (`bin/`, `lib/`) must stay on disk. This skill drives
those commands; it does not reimplement the scanner.

## 1. Local server and local-only credentials

```bash
[ -f ~/.config/sft/bridge.env ] && . ~/.config/sft/bridge.env
BRIDGE="${SFT_AGENT_BRIDGE:-${BRIDGE:?Set BRIDGE to the EasyScanPKG root}}"

"$BRIDGE/bin/sonar-local-up"
"$BRIDGE/bin/sonar-local-credentials" bootstrap
"$BRIDGE/bin/sonar-local-credentials" list
"$BRIDGE/bin/sonar-local-credentials" use issues
```

The series lives in `~/.config/sft/credentials/` (mode 600):

| Name | Use |
| --- | --- |
| `agent` | API, MCP, project admin |
| `scanner` | SonarScanner analysis |
| `issues` | List, export, and resolve |

`store` rejects any URL that is not loopback (`127.0.0.1`, `localhost`, `::1`).
Do not pass remote tokens to this skill.

## 2. Scan and export the fix list

```bash
"$BRIDGE/bin/sonar-local-credentials" use scanner
"$BRIDGE/bin/sonar-scan" --workspace "$PWD" --sources <dirs> \
  --project-key "local-$(basename "$PWD")"

"$BRIDGE/bin/sonar-local-credentials" use issues
"$BRIDGE/bin/sonar-issues" --local export --workspace "$PWD" --refresh
```

Authoritative list: `<workspace>/.sft/issue-checklist.md`  
JSON twin: `<workspace>/.sft/issue-checklist.json`  
Header field `open_count` is the source of truth. Done when it is `0`.

If the checklist is missing, regenerate with the commands above. Do not invent issues.

## 3. Fix loop

1. Read unchecked items. Order: **BLOCKER → CRITICAL → MAJOR → MINOR**.
2. Open `file:line`. Look up `rule` in `$BRIDGE/templates/remediation.easyscanpkg.json`.
3. **Change the code.** Leave a short note in the commit or PR for anything you cannot fix safely.
4. Re-scan the same sources and `export --refresh`.
5. Stop when the export says the checklist is complete (`open_count` 0).

Do not tick checklist boxes by hand. `sonar-issues resolve` is only for a documented false positive or accepted risk — prefer a code fix.

## 4. EasyScanPKG self-scan

Stock exclusions hide `bin/`. When cleaning this repository:

```bash
"$BRIDGE/bin/sonar-scan" --workspace "$BRIDGE" --sources lib,bin,hooks \
  --project-key local-easyscanpkg \
  --exclusions '**/obj/**,**/node_modules/**,**/.git/**,**/__pycache__/**,**/.venv/**'
"$BRIDGE/bin/sonar-issues" --local export --workspace "$BRIDGE" \
  --project-key local-easyscanpkg --refresh
```

A post-scan count of `0` can be indexing lag. Re-query with `sonar-issues list` or `export --refresh`.
