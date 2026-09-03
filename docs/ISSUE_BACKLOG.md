# EasyScanPKG issue backlog

Tooling readiness for one-click EasyScanPKG (local Sonar + agent bridge).

## EasyScanPKG tooling

| ID | Issue | Status / fix |
| --- | --- | --- |
| T1 | Compose missing on Ubuntu `docker.io` | Native Docker fallback in `local_server.py`; `easyscan-check` notes Compose optional |
| T2 | MCP can’t reach local Sonar in Docker | `--network=host` in MCP template; check asserts it |
| T3 | IDE bridge confused with MCP plugin | `sonar-ide-port --install` + diagnose; check soft-warns |
| T4 | Stale local token / validate body | Fixed in `server_health` / `ensure_token`; covered by smoke |
| T5 | SQ 26 password API / policy | Fixed in local bootstrap; exercised by `sonar-local-up` |
| T6 | Hardcoded workspace URL/source defaults | Parameterized; optional `EASYSCAN_EXAMPLE_WS` only |
| T7 | No single verify gate | `bin/easyscan-check` (+ install/commission/desktop hooks) |
| T8 | Skills not auto-installed on every start | `sonar-desktop` / commission call `install-skills.sh` |
| T9 | C++/ASM/Julia expectations | `sonar-languages` honesty + README matrix |
| T10 | Multi-project / multi-server switching | Named `contexts` in policy DB + `sonar-context` CLI; `--context` on project/scan/issues |
| T11 | Agent-ingestible issue checklist | `sonar-issues export` / `easyscan-scan` → `.sft/issue-checklist.md` (v2 multi-source; done when empty) |
| T12 | Quality profile XML import per context | `sonar-profile import/export/list/bind` + optional remediation sidecar |
| T13 | Agent bootstrap + fix-queue skills | `easyscan-bootstrap`, `sonar-fix-queue`; playbooks in `docs/` |
| T14 | Multi-scanner stage (clang-tidy, Dr. Memory) | `easyscan-scan` + `lib/scanners/*`; enable/disable via CLI/env/`.sft/sonar-policy.json` |

Gate before publish: `./bin/easyscan-check --offline` and unit tests pass.

## Example agent workflow

1. `sonar-local-up` / EasyScan desktop
2. `easyscan-scan --workspace <PROJECT> --sources <dirs>` (add `--enable clang-tidy` / `--enable drmemory` as needed)
3. Fix findings in the scanned project repository (note each issue's `source`)
4. Re-run the same scan; resolve Sonar-only with documented rationale
