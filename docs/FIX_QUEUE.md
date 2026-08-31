# EasyScanPKG Sonar fix queue (agent playbook)

**Project key:** `local-easyscanpkg`  
**Dashboard:** http://127.0.0.1:9000/dashboard?id=local-easyscanpkg  
**Live checklist (source of truth):** `<workspace>/.sft/issue-checklist.md`  
**JSON twin:** `<workspace>/.sft/issue-checklist.json`  
**Remediation hints:** [`templates/remediation.easyscanpkg.json`](../templates/remediation.easyscanpkg.json)

Last exported scan (lib + bin + hooks): **1 open**  
(0 BLOCKER, 1 CRITICAL, 0 MAJOR, 0 MINOR) — 2026-08-31

## How agents should fix

1. Activate EasyScan / local Sonar (skill `easyscan-bootstrap`).
2. Refresh checklist:
   ```bash
   "$BRIDGE/bin/sonar-issues" --local export --workspace "$PWD" \
     --project-key local-easyscanpkg --refresh
   ```
3. Work **unchecked** items in severity order: BLOCKER → CRITICAL → MAJOR → MINOR.
4. For each item, open `file:line`, apply guidance from the remediation JSON for that `rule`.
5. Prefer **code fixes**. Use `sonar-issues resolve` only with documented rationale.
6. After a batch of fixes (note `--exclusions` so `bin/` is analyzed — stock default hides it):
   ```bash
   "$BRIDGE/bin/sonar-scan" --workspace "$PWD" --sources lib,bin,hooks \
     --project-key local-easyscanpkg \
     --exclusions '**/obj/**,**/node_modules/**,**/.git/**,**/__pycache__/**,**/.venv/**'
   "$BRIDGE/bin/sonar-issues" --local export --workspace "$PWD" \
     --project-key local-easyscanpkg --refresh
   ```
7. **Done when** checklist `open_count` is **0** / `resolved: yes`.

## Priority buckets (from latest export)

| Priority | Count | Focus |
| --- | ---: | --- |
| BLOCKER | 0 | — |
| CRITICAL | 1 | `lib/credentials_set.py:113` cognitive complexity (python:S3776) |
| MAJOR | 0 | — |
| MINOR | 0 | — |

## Hot files

| File | Issues |
| --- | ---: |
| `lib/credentials_set.py` | 1 |

## Critical-only export (optional)

```bash
"$BRIDGE/bin/sonar-issues" --local export --workspace "$PWD" \
  --project-key local-easyscanpkg --severity BLOCKER,CRITICAL \
  --output .sft/issue-checklist-critical.md \
  --json-output .sft/issue-checklist-critical.json --refresh
```
