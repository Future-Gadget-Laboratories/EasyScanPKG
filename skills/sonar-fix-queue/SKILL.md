---
name: sonar-fix-queue
description: >-
  Work findings from the EasyScanPKG issue checklist until empty.
  Use after easyscan-scan / sonar-scan, when asked to clean Sonar or
  multi-scanner issues, or to find the list of things to fix.
---

# Fix queue (checklist until empty)

## Where is the list of things to fix?

| Artifact | Path |
| --- | --- |
| **Primary checklist** | `<workspace>/.sft/issue-checklist.md` |
| JSON twin | `<workspace>/.sft/issue-checklist.json` |
| Critical-only (optional) | `<workspace>/.sft/issue-checklist-critical.md` |
| Human playbook | `EasyScanPKG/docs/FIX_QUEUE.md` |
| Rule → how to fix | `EasyScanPKG/templates/remediation.easyscanpkg.json` |
| Sonar UI | `http://127.0.0.1:9000/project/issues?id=<project_key>&resolved=false` |

Schema is `easyscan.issue-checklist/v2`. Each issue has a `source` field:
`sonar` | `clang-tidy` | `drmemory`. Header `sources_run` / `sources_skipped`
show which scanners participated.

If the checklist is missing or stale, **regenerate** (do not invent issues):

```bash
[ -f ~/.config/sft/bridge.env ] && . ~/.config/sft/bridge.env
BRIDGE="${SFT_AGENT_BRIDGE:-${BRIDGE:?}}"

# Preferred: all-in-one stage. Auto mode runs every installed scanner that
# applies to the files in this project; --plan shows the routing first.
"$BRIDGE/bin/easyscan-scan" --workspace "$PWD" --plan
"$BRIDGE/bin/easyscan-scan" --workspace "$PWD" --local \
  --project-key "${SONARQUBE_PROJECT_KEY:-local-$(basename "$PWD")}" \
  --sources <dirs>
# Missing tools are skipped with a hint — install them with:
#   "$BRIDGE/bin/easyscan-install-scanners" --workspace "$PWD"
# Dynamic analysis needs a target: --asan-command -- ./build/tests_asan

# Sonar-only fallback:
"$BRIDGE/bin/sonar-scan" --workspace "$PWD" --sources <dirs> \
  --project-key "${SONARQUBE_PROJECT_KEY:-local-$(basename "$PWD")}"
"$BRIDGE/bin/sonar-issues" --local export --workspace "$PWD" --refresh
```

Header fields `open_count` / `resolved` are authoritative.  
**Done when `open_count` is 0** (checklist says complete).

## Activation prerequisite

If Sonar is not UP and Sonar is among enabled scanners, run skill **`easyscan-bootstrap`** first.
clang-tidy needs `compile_commands.json`; drmemory needs a configured run command.

## Agent loop

1. Read `.sft/issue-checklist.md` (or critical-only export).
2. Sort remaining unchecked items: **BLOCKER → CRITICAL → MAJOR → MINOR**.
3. For each item:
   - Note `source` (`sonar` / `clang-tidy` / `drmemory`)
   - Open `file:line`
   - Look up `rule` in remediation JSON for guidance
   - **Fix the code** (preferred). Avoid `resolve`/`accept` unless justified in the PR/commit message (Sonar only).
4. After a batch (or each BLOCKER/CRITICAL), re-run the **same enabled scanners**:
   ```bash
   "$BRIDGE/bin/easyscan-scan" --workspace "$PWD" --local \
     --project-key <key> --sources <same dirs> \
     # plus the same --enable / --compile-commands / --drmemory-command flags
   ```
   Sonar-only:
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
"$BRIDGE/bin/easyscan-scan" --context <name> --workspace "$PWD" --local
```

## Scanner enable/disable

Defaults: every scanner is `auto` — it runs when the project has matching files
and the tool is installed. Check `sources_skipped` in the checklist for why a
scanner did not run (`no C/C++ files`, `not installed …`, `error: …`).

Precedence (highest first): CLI (`--enable` / `--disable` / `--scanners`) → env
(`EASYSCAN_SCANNERS`, `EASYSCAN_ENABLE_*`) → `.sft/sonar-policy.json`
`scan.scanners` → global policy prefs → auto.

## Do not

- Claim CI quality-gate pass from local Community results
- Print full `SONARQUBE_TOKEN` values into chat
- Mark checklist boxes by hand — always re-run `easyscan-scan` / `sonar-issues export --refresh`
- Invent findings for scanners that were skipped (`sources_skipped`)
