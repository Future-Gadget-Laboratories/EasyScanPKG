---
name: sonar-fix-queue
description: >-
  Work Sonar findings from the EasyScanPKG issue checklist until empty.
  Use after a scan, when asked to clean Sonar issues, or to find the list
  of things to fix in the current workspace.
---

# Sonar fix queue (checklist until empty)

## Where is the list of things to fix?

| Artifact | Path |
| --- | --- |
| **Primary checklist** | `<workspace>/.sft/issue-checklist.md` |
| JSON twin | `<workspace>/.sft/issue-checklist.json` |
| Critical-only (optional) | `<workspace>/.sft/issue-checklist-critical.md` |
| Human playbook | `EasyScanPKG/docs/FIX_QUEUE.md` |
| Rule → how to fix | `EasyScanPKG/templates/remediation.easyscanpkg.json` |
| Sonar UI | `http://127.0.0.1:9000/project/issues?id=<project_key>&resolved=false` |

If the checklist is missing or stale, **regenerate** (do not invent issues):

```bash
[ -f ~/.config/sft/bridge.env ] && . ~/.config/sft/bridge.env
BRIDGE="${SFT_AGENT_BRIDGE:-${BRIDGE:?}}"

"$BRIDGE/bin/sonar-scan" --workspace "$PWD" --sources <dirs> \
  --project-key "${SONARQUBE_PROJECT_KEY:-local-$(basename "$PWD")}"

"$BRIDGE/bin/sonar-issues" --local export --workspace "$PWD" --refresh
```

Header fields `open_count` / `resolved` are authoritative.  
**Done when `open_count` is 0** (checklist says complete).

## Activation prerequisite

If Sonar is not UP, run skill **`easyscan-bootstrap`** first.

## Agent loop

1. Read `.sft/issue-checklist.md` (or critical-only export).
2. Sort remaining unchecked items: **BLOCKER → CRITICAL → MAJOR → MINOR**.
3. For each item:
   - Open `file:line`
   - Look up `rule` in remediation JSON for guidance
   - **Fix the code** (preferred). Avoid `resolve`/`accept` unless justified in the PR/commit message.
4. After a batch (or each BLOCKER/CRITICAL):
   ```bash
   "$BRIDGE/bin/sonar-scan" --workspace "$PWD" --sources <same dirs> \
     --project-key <key>
   "$BRIDGE/bin/sonar-issues" --local export --workspace "$PWD" --refresh
   ```
5. Stop when export prints `resolved=True` / `Done — checklist empty.`

### Critical-only focus

```bash
"$BRIDGE/bin/sonar-issues" --local export --workspace "$PWD" \
  --severity BLOCKER,CRITICAL --refresh \
  --output .sft/issue-checklist-critical.md \
  --json-output .sft/issue-checklist-critical.json
```

## Multi-repo / context

```bash
"$BRIDGE/bin/sonar-context" use <name>
"$BRIDGE/bin/sonar-issues" --context <name> export --workspace "$PWD" --refresh
```

## Quality profile XML (custom rules)

```bash
"$BRIDGE/bin/sonar-profile" --local import /path/to/profile.xml \
  --bind-project <key> --remediation "$BRIDGE/templates/remediation.easyscanpkg.json"
```

## Do not

- Claim CI quality-gate pass from local Community results
- Print full `SONARQUBE_TOKEN` values into chat
- Mark checklist boxes by hand — always re-`export --refresh` from Sonar
