# EasyScanPKG issue backlog

Two tracks: **Track A** tooling readiness for one-click EasyScanPKG, and **Track B**
CipherBank `modulebuilder/mm` findings discovered via local Sonar (fix in CipherBank-src).

## Track A — EasyScanPKG tooling

| ID | Issue | Status / fix |
| --- | --- | --- |
| T1 | Compose missing on Ubuntu `docker.io` | Native Docker fallback in `local_server.py`; `easyscan-check` notes Compose optional |
| T2 | MCP can’t reach local Sonar in Docker | `--network=host` in MCP template; check asserts it |
| T3 | IDE bridge confused with MCP plugin | `sonar-ide-port --install` + diagnose; check soft-warns |
| T4 | Stale local token / validate body | Fixed in `server_health` / `ensure_token`; covered by smoke |
| T5 | SQ 26 password API / policy | Fixed in local bootstrap; exercised by `sonar-local-up` |
| T6 | CipherBank-specific defaults in commission | Parameterized; CipherBank path is example-only if present |
| T7 | No single verify gate | `bin/easyscan-check` (+ install/commission/desktop hooks) |
| T8 | Skills not auto-installed on every start | `sonar-desktop` / commission call `install-skills.sh` |
| T9 | C++/ASM/Julia expectations | `sonar-languages` honesty + README matrix |

Gate before GitHub: `./bin/easyscan-check --offline` and unit tests pass.

## Track B — CipherBank Sonar findings (15)

**Scope:** `make/make-module/modulebuilder/mm` (and `features/`)  
**Project key (local example):** `local-CB-st_CipherBank-src_d25eb365-b11f-4144-9d9c-03aa1434d528`

Re-verify:

```bash
EasyScanPKG/bin/sonar-scan --workspace <CipherBank> \
  --sources make/make-module/modulebuilder/mm
EasyScanPKG/bin/sonar-issues --local list
```

| Priority | Rule | Location | Approach |
| --- | --- | --- | --- |
| P0 | `python:S3776` | `audit.py:96` | Extract helpers; early returns |
| P0 | `python:S3776` | `cli.py:125`, `:221`, `:303` | Extract helpers; early returns |
| P0 | `python:S3776` | `docs.py:74` | Extract helpers |
| P0 | `python:S3776` | `flags.py:179` | Extract helpers |
| P0 | `python:S3776` | `parser.py:278`, `:309` | Extract helpers |
| P0 | `python:S3776` | `patcher.py:294` | Extract helpers |
| P0 | `python:S3776` | `ports.py:61` | Extract helpers |
| P0 | `python:S3776` | `templates.py:42` | Extract helpers |
| P1 | `python:S1192` | `features/expose_via_http.py:79` | Named constant for path literal |
| P1 | `python:S1192` | `registry.py:124` | Named constant for `*.yaml` |
| P2 | `python:S1172` | `flags.py:392` | Remove unused `module` param (or use it) |
| P3 | `python:S6903` | `registry.py:52` | `datetime.now(timezone.utc)` |

DoD: `sonar-issues --local list` shows **0 CRITICAL** on this tree after CipherBank PRs.

## Example agent workflow

1. `sonar-local-up` / EasyScan desktop
2. `sonar-scan --workspace … --sources make/make-module/modulebuilder/mm`
3. Fix Track B in CipherBank-src (not in EasyScanPKG)
4. Re-scan; resolve only with documented rationale
