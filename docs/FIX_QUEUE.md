# EasyScanPKG fix queue (agent playbook)

**Project key:** `local-easyscanpkg`  
**Dashboard:** http://127.0.0.1:9000/dashboard?id=local-easyscanpkg  
**Live checklist (source of truth):** `<workspace>/.sft/issue-checklist.md`  
**JSON twin:** `<workspace>/.sft/issue-checklist.json`  
**Schema:** `easyscan.issue-checklist/v2` (each issue has `source`: sonar | clang-tidy | drmemory)  
**Remediation hints:** [`templates/remediation.easyscanpkg.json`](../templates/remediation.easyscanpkg.json)

## How agents should fix

1. Activate EasyScan / local Sonar when Sonar is enabled (skill `easyscan-bootstrap`).
2. Refresh checklist with the **same scanners** you intend to clear:
   ```bash
   "$BRIDGE/bin/easyscan-scan" --workspace "$PWD" --local \
     --project-key local-easyscanpkg --sources lib,bin,hooks
   # Opt-in: --enable clang-tidy --compile-commands build/compile_commands.json
   # Opt-in: --enable drmemory --drmemory-command -- ./build/tests
   ```
   Sonar-only fallback:
   ```bash
   "$BRIDGE/bin/sonar-issues" --local export --workspace "$PWD" \
     --project-key local-easyscanpkg --refresh
   ```
3. Work **unchecked** items in severity order: BLOCKER → CRITICAL → MAJOR → MINOR.
4. For each item, note `source`, open `file:line`, apply remediation JSON guidance for that `rule`.
5. Prefer **code fixes**. Use `sonar-issues resolve` only for Sonar findings with documented rationale.
6. After a batch of fixes, re-run the same `easyscan-scan` / export command.
7. **Done when** checklist `open_count` is **0** / `resolved: yes`.

## Priority buckets (from latest export)

| Priority | Count | Focus |
| --- | ---: | --- |
| BLOCKER | 0 | — |
| CRITICAL | 0 | — |
| MAJOR | 0 | — |
| MINOR | 0 | — |

## Hot files

_None — checklist empty._

## Critical-only export (optional)

```bash
"$BRIDGE/bin/sonar-issues" --local export --workspace "$PWD" \
  --project-key local-easyscanpkg --severity BLOCKER,CRITICAL \
  --output .sft/issue-checklist-critical.md \
  --json-output .sft/issue-checklist-critical.json --refresh
```
