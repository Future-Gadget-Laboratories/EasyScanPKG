# EasyScanPKG

Bash + Python (stdlib-only) toolkit that wires a local or remote **SonarQube**
server into **Cursor** / **Codex**: Docker Community stack, token bootstrap,
SonarScanner CLI, MCP helpers, and an agent-ingestible issue checklist.

The all-in-one stage `easyscan-scan` can also run optional **host** analyzers
(cppcheck, ruff, shellcheck, semgrep, bandit, sanitizers, valgrind, gitleaks,
dependency scanners, and more) and merge every finding into one checklist.

> Requires Docker for the Sonar path. Pulls official Sonar Community / scanner
> images at runtime — this repo does **not** redistribute Sonar binaries.
> Extra scanners are opt-in host binaries (not bundled).

## Contents

- [First-time commission](#first-time-commission-linux-mint--ubuntu)
- [Verify install](#verify-install)
- [Core workflow](#core-workflow)
- [Command reference](#command-reference)
- [Multi-scanner stage (`easyscan-scan`)](#multi-scanner-stage-easyscan-scan)
- [Scanner catalog](#scanner-catalog)
- [Controls (CLI, env, policy)](#controls-cli-env-policy)
- [Issue checklist](#issue-checklist-done-when-empty)
- [Multi-project contexts](#multi-project-contexts)
- [Language support](#language-support-local-community)
- [Config files](#config-files)
- [Skills](#skills-auto-installed)
- [Tests](#tests)
- [License](#license)

## First-time commission (Linux Mint / Ubuntu)

```bash
cd EasyScanPKG
chmod +x commission.sh install.sh bin/*
./commission.sh --workspace /path/to/your/project --skip-remote-creds --no-prompt
./bin/easyscan-check --require-local
```

Useful `commission.sh` flags:

| Flag | Effect |
| --- | --- |
| `--no-prompt` | Skip credential dialogs |
| `--skip-remote-creds` | Local Docker only |
| `--no-favorites` | Skip Cinnamon favorites pin |
| `--skip-check` | Skip final `easyscan-check` |

Daily: click **EasyScan** on the panel, or run `bin/sonar-desktop`.

Local stack helpers:

```bash
./bin/sonar-local-up      # SonarQube Community + Postgres on :9000
./bin/sonar-local-down    # stop containers
```

`sonar-local-up` mints a local admin token into `~/.config/sft/sonar-local.env`
and prints the admin password (also stored in
`~/.config/sft/sonar-local-admin.json`). By default it also installs the
[sonar-cxx](https://github.com/SonarOpenCommunity/sonar-cxx) plugin for Community
C/C++ (`cxx` language key). Disable with `SFT_INSTALL_SONAR_CXX=0`.

## Verify install

```bash
./bin/easyscan-check                 # full readiness
./bin/easyscan-check --offline       # CI / no Docker daemon required
./bin/easyscan-check --require-local # must have Sonar UP + token
./install.sh --check-only            # offline check without installing
```

| Flag | Effect |
| --- | --- |
| `--offline` | Skip docker pulls; treat missing local server as soft |
| `--require-local` | Fail if local Sonar is not UP with a token |
| `--quick` | Fast subset for the desktop launcher |
| `--pull-images` | `docker pull` missing images |
| `--skip-tests` | Do not run the unit-test readiness item |
| `--json` | Machine-readable report |

## Core workflow

```bash
# 1) Local server + token (once per machine / after reboot of Docker)
./bin/sonar-local-up

# 2) Bind a project key to a workspace
./bin/sonar-project --local create local-demo --workspace "$PWD"

# 3a) Sonar-only scan + checklist
./bin/sonar-scan --workspace "$PWD" --sources src,lib --project-key local-demo
./bin/sonar-issues --local export --workspace "$PWD" --project-key local-demo --refresh

# 3b) Or all-in-one multi-scanner (Sonar on; other tools opt-in)
./bin/easyscan-scan --workspace "$PWD" --project-key local-demo \
  --sources src,lib \
  --enable ruff --enable shellcheck --enable cppcheck

# 4) Fix until the checklist is empty, then re-run
#    → .sft/issue-checklist.md (+ .json)
```

Note: right after `sonar-scan`, the printed issue count can briefly read `0`
while Sonar indexes. Re-query with `./bin/sonar-issues --local list` or
`easyscan-scan` export.

## Command reference

| Action | Command |
| --- | --- |
| Spin up + open Cursor | `sonar-desktop` / EasyScan icon |
| Local Sonar up / down | `./bin/sonar-local-up` / `./bin/sonar-local-down` |
| Readiness check | `./bin/easyscan-check [--offline] [--require-local]` |
| Named contexts (multi-project) | `./bin/sonar-context create\|list\|use\|bind` |
| Create/bind project | `./bin/sonar-project --local create KEY --workspace "$PWD"` |
| Sonar-only scan | `./bin/sonar-scan --workspace "$PWD" --sources <dirs> [--context NAME]` |
| All-in-one multi-scanner | `./bin/easyscan-scan --workspace "$PWD" [options]` |
| List registered scanners | `./bin/easyscan-scan --list-scanners` |
| List / resolve issues | `./bin/sonar-issues --local list` / `resolve ISSUE` |
| Export issue checklist | `./bin/sonar-issues export --workspace "$PWD" --refresh` |
| Quality profile XML | `./bin/sonar-profile import FILE.xml --local --bind-project KEY` |
| Language probe | `./bin/sonar-languages --local` / `--install-cxx` |
| Credentials (remote) | `./bin/sonar-credentials --cli --test` |
| Credentials (local) | `./bin/sonar-credentials --local --bootstrap --test` |
| MCP up / down / status | `./bin/sonar-mcp-up` / `sonar-mcp-down` / `sonar-mcp-status` |

## Multi-scanner stage (`easyscan-scan`)

`easyscan-scan` runs every **enabled** scanner, merges findings, and writes the
unified checklist (schema `easyscan.issue-checklist/v2`). Each issue is tagged
with `source` (`sonar`, `ruff`, `cppcheck`, …).

**Defaults:** Sonar **on**; every other scanner **off**.

### CLI options

```bash
./bin/easyscan-scan --help
```

| Option | Meaning |
| --- | --- |
| `--workspace PATH` | Project root (default: cwd) |
| `--project-key KEY` | Sonar project key |
| `--context NAME` | Named analysis context (`sonar-context`) |
| `--local` | Prefer local Sonar credentials |
| `--sources CSV` | Sonar sources (passed through to `sonar-scan`) |
| `--exclusions CSV` | Sonar exclusions CSV |
| `--compile-commands PATH` | `compile_commands.json` for clang-tidy / optional CFamily |
| `--enable NAME` | Enable a scanner (repeatable) |
| `--disable NAME` | Disable a scanner (repeatable) |
| `--scanners a,b,c` | Run **only** these scanners (exclusive list) |
| `--list-scanners` | Print registered names and exit |
| `--drmemory-command -- …` | Target argv for Dr. Memory (after `--`) |
| `--asan-command -- …` | Instrumented binary argv for ASan |
| `--ubsan-command -- …` | Instrumented binary argv for UBSan |
| `--valgrind-command -- …` | Target argv for Valgrind Memcheck |
| `--export-only` | Skip tool binaries / sonar-scan when a prior analysis is enough |
| `--no-dedupe` | Disable light cross-scanner dedupe |
| `--output PATH` | Markdown checklist path |
| `--json-output PATH` | JSON checklist path |
| `--no-json` | Skip JSON twin |
| `--json` | Print payload JSON to stdout |
| `--severity CSV` | Sonar severity filter (`BLOCKER,CRITICAL,…`) |
| `--type CSV` | Sonar type filter (`BUG,VULNERABILITY,CODE_SMELL`) |

### Examples

```bash
# List every registered scanner name
./bin/easyscan-scan --list-scanners

# Sonar + Python/Bash/C++ static tools
./bin/easyscan-scan --workspace "$PWD" --project-key local-demo \
  --sources lib,bin,hooks \
  --enable ruff --enable shellcheck --enable cppcheck --enable bandit

# C++ tidy + cppcheck with a compilation database
./bin/easyscan-scan --workspace "$PWD" --project-key local-demo \
  --enable clang-tidy --compile-commands build/compile_commands.json \
  --enable cppcheck --enable flawfinder

# Dynamic memory (pick one style per project)
./bin/easyscan-scan --workspace "$PWD" --disable sonar \
  --enable asan --asan-command -- ./build/tests_asan
./bin/easyscan-scan --workspace "$PWD" --disable sonar \
  --enable valgrind --valgrind-command -- ./build/tests
./bin/easyscan-scan --workspace "$PWD" --disable sonar \
  --enable drmemory --drmemory-command -- ./build/tests

# Secrets + dependency CVEs only
./bin/easyscan-scan --workspace "$PWD" --scanners gitleaks,pip-audit,osv

# Dockerfile + Semgrep AppSec rules
./bin/easyscan-scan --workspace "$PWD" --enable hadolint --enable semgrep
```

## Scanner catalog

Install the host binary (or set `binary` in policy), then enable the scanner.
None of these tools are Dockerized in v1 (except Sonar’s own scanner image).

| Name | Role | Host binary | How to use |
| --- | --- | --- | --- |
| `sonar` | SonarQube analysis + issue export | Docker `sonar-scanner-cli` via `sonar-scan` | On by default. Needs local/remote server + token. |
| `clang-tidy` | C/C++ clang-tidy checks | `clang-tidy` | `--enable clang-tidy --compile-commands build/compile_commands.json` |
| `cppcheck` | C/C++ static analysis (XML) | `cppcheck` | `--enable cppcheck` — optional policy `paths`, `std`, `suppressions` |
| `ruff` | Python lint / readability | `ruff` | `--enable ruff` — default paths `lib`, `bin`, `hooks`; policy `select` / `ignore` / `config` |
| `shellcheck` | Shell script lint | `shellcheck` | `--enable shellcheck` — discovers `*.sh` under `bin`/`hooks`/`scripts` when `paths` empty |
| `semgrep` | Polyglot SAST | `semgrep` | `--enable semgrep` — default config `p/default`; override with policy/`EASYSCAN_SEMGREP_CONFIG` |
| `bandit` | Python security | `bandit` | `--enable bandit` — recursive JSON report; policy `paths`, `skips` |
| `asan` | AddressSanitizer run adapter | instrumented binary | `--enable asan --asan-command -- ./bin_asan` (build with `-fsanitize=address` first) |
| `ubsan` | UndefinedBehaviorSanitizer run adapter | instrumented binary | `--enable ubsan --ubsan-command -- ./bin_ubsan` |
| `valgrind` | Memcheck (Linux) | `valgrind` | `--enable valgrind --valgrind-command -- ./tests` |
| `drmemory` | Dynamic memory (Dr. Memory) | `drmemory` | `--enable drmemory --drmemory-command -- ./tests` |
| `gitleaks` | Secrets detection | `gitleaks` | `--enable gitleaks` — default filesystem `--no-git` scan for checklist line numbers |
| `pip-audit` | Python dependency CVEs | `pip-audit` | `--enable pip-audit` — optional policy `requirements` |
| `osv` | OSV lockfile/manifest CVEs | `osv-scanner` | `--enable osv` |
| `flawfinder` | C/C++ insecure-API heuristics | `flawfinder` | `--enable flawfinder` — policy `minlevel` (noisy; keep off unless wanted) |
| `clang-analyzer` | Clang Static Analyzer | `scan-build` | `--enable clang-analyzer` — policy `report_dir`, `sarif`, or `build_command` |
| `hadolint` | Dockerfile lint | `hadolint` | `--enable hadolint` — discovers `Dockerfile*` when `paths` empty |

### Install hints (host packages)

Exact package names vary by distro; examples on Debian/Ubuntu-ish systems:

```bash
# Static / lint
sudo apt-get install -y cppcheck shellcheck clang-tidy clang-tools valgrind
pipx install ruff bandit semgrep pip-audit   # or: pip install --user …

# Secrets / deps / Dockerfiles / C heuristics
# gitleaks, osv-scanner, hadolint, flawfinder — install from upstream releases
# or your distro packages when available
```

### Overlap guidance

- **Dynamic memory:** prefer **one** of `asan`, `valgrind`, or `drmemory` per project.
- **C/C++ static:** `clang-tidy`, `cppcheck`, `flawfinder`, and `clang-analyzer` overlap; start with tidy + cppcheck.
- **Python:** `ruff` (style) + `bandit` or `semgrep` (security) pair well; Sonar still covers much of the quality surface.

## Controls (CLI, env, policy)

Precedence when resolving scanner config (later wins):

1. Built-in defaults (Sonar on; others off)
2. Policy DB / project overlay
3. Workspace file: `.sft/sonar-policy.json` or `.sft/scan-policy.json`
4. Environment variables
5. CLI (`--enable` / `--disable` / `--scanners` / command flags)

### Environment variables

| Variable | Effect |
| --- | --- |
| `EASYSCAN_SCANNERS` | Comma list → exclusive enable set (like `--scanners`) |
| `EASYSCAN_ENABLE_<NAME>` | `1`/`true`/`on` or `0`/`false`/`off` per scanner |
| `EASYSCAN_COMPILE_COMMANDS` | Path to `compile_commands.json` |
| `EASYSCAN_DRMEMORY_COMMAND` | Dr. Memory target command string |
| `EASYSCAN_ASAN_COMMAND` | ASan target command string |
| `EASYSCAN_UBSAN_COMMAND` | UBSan target command string |
| `EASYSCAN_VALGRIND_COMMAND` | Valgrind target command string |
| `EASYSCAN_SEMGREP_CONFIG` | Semgrep `--config` value (e.g. `p/owasp-top-ten`) |

Enable-flag examples (names map to scanner ids with `-` → `_`, uppercased):

```bash
export EASYSCAN_ENABLE_RUFF=1
export EASYSCAN_ENABLE_SHELLCHECK=1
export EASYSCAN_ENABLE_CPPCHECK=1
export EASYSCAN_ENABLE_GITLEAKS=1
export EASYSCAN_ENABLE_PIP_AUDIT=1
# exclusive set:
export EASYSCAN_SCANNERS=sonar,ruff,shellcheck
./bin/easyscan-scan --workspace "$PWD" --project-key local-demo
```

### Workspace policy overlay

Copy and edit `templates/project.sonar-policy.json` → `.sft/sonar-policy.json`:

```json
{
  "scan": {
    "scanners": {
      "sonar": { "enabled": true },
      "ruff": { "enabled": true, "paths": ["lib", "bin", "hooks"] },
      "shellcheck": { "enabled": true },
      "cppcheck": { "enabled": false, "std": "c++17" },
      "semgrep": { "enabled": false, "config": "p/default" },
      "clang-tidy": {
        "enabled": false,
        "compile_commands": "build/compile_commands.json"
      },
      "asan": { "enabled": false, "command": ["./build/tests_asan"] },
      "gitleaks": { "enabled": false, "no_git": true }
    }
  }
}
```

A top-level `"scanners": { … }` block is also accepted (same shape).

## Issue checklist (done when empty)

```bash
# Sonar-only export
./bin/sonar-issues --local export --workspace "$PWD" --refresh

# Multi-scanner write (same files)
./bin/easyscan-scan --workspace "$PWD" --project-key local-demo \
  --enable ruff --enable shellcheck
```

Outputs:

| File | Purpose |
| --- | --- |
| `.sft/issue-checklist.md` | Agent-readable open-issue list |
| `.sft/issue-checklist.json` | Machine-readable twin |

Schema: `easyscan.issue-checklist/v2`. Each issue includes `source`, `severity`,
`type`, `rule`, `file`, `line`, `message`. Re-run after fixes; stop when
`open_count` is `0`.

Optional remediation hints: `templates/remediation.easyscanpkg.json`.

## Multi-project contexts

Register each GitHub/Sonar target as a **named context** (URL + token file ref +
project key + tags). Local Community still uses one Sonar instance; isolation is
by project key + quality profile.

```bash
./bin/sonar-context create demo --url http://127.0.0.1:9000 \
  --project-key local-demo --gh example/demo --use
./bin/sonar-project --context demo create local-demo --workspace "$PWD"
./bin/sonar-scan --context demo --workspace "$PWD" --sources src
```

## Quality profile XML import

```bash
./bin/sonar-profile --local export --language py --name "Sonar way" -o /tmp/sonar-way-py.xml
./bin/sonar-profile --local import /tmp/custom-py.xml --set-default --bind-project local-demo \
  --remediation templates/remediation.example.json
```

## Language support (local Community)

| Language | Local Community image | Notes |
| --- | --- | --- |
| Python, JS/TS, Java, C#, Go, … | Yes | via `sonar-scan` |
| C / C++ | **Yes via sonar-cxx** | Auto-installed on `sonar-local-up` from [SonarOpenCommunity/sonar-cxx](https://github.com/SonarOpenCommunity/sonar-cxx) (language key `cxx`). Not commercial CFamily/Build Wrapper. Optional external reports: `sonar.cxx.cppcheck.reportPaths`, etc. Disable with `SFT_INSTALL_SONAR_CXX=0`. Also use host tools `cppcheck` / `clang-tidy` via `easyscan-scan`. |
| Objective-C | No | Needs commercial CFamily |
| Assembly | No | No first-party analyzer |
| Julia | No | No official Sonar plugin |

## Config files

| File | Purpose |
| --- | --- |
| `~/.config/sft/sonar.env` | Optional remote URL/token |
| `~/.config/sft/sonar-local.env` | Auto local token (**never commit**) |
| `~/.config/sft/sonar-local-admin.json` | Generated local admin password |
| `~/.config/sft/sonar-policy/policy.db` | Policy + named contexts (no tokens) |
| `~/.config/sft/plugins/` | Cached plugin JARs (e.g. sonar-cxx) |
| `~/.config/sft/desktop.env` | Workspace for EasyScan launcher |
| `~/.config/sft/bridge.env` | `BRIDGE` / `SFT_AGENT_BRIDGE` path |
| `<repo>/.sft/issue-checklist.md` | Agent-ingestible open-issue checklist |
| `<repo>/.sft/issue-checklist.json` | JSON twin |
| `<repo>/.sft/sonar-policy.json` | Optional per-repo scanner / scan prefs |
| `<repo>/.sft/scan-policy.json` | Alternate overlay filename |

## Skills (auto-installed)

- `easyscan-bootstrap` — activate Sonar in any agent environment
- `sonar-fix-queue` — find `.sft/issue-checklist.md` and fix until empty (multi-scanner aware)
- `sonar-local-ops` — local projects, scan, issues
- `sonar-mcp-lifecycle` — MCP up/down, credentials
- `sonar-agent-analysis` — end-of-task analyze via IDE/MCP

See [docs/AGENT_SONAR_PLAYBOOK.md](docs/AGENT_SONAR_PLAYBOOK.md) and
[docs/FIX_QUEUE.md](docs/FIX_QUEUE.md).

## Tests

```bash
python3 -m unittest discover -s tests -v
./bin/easyscan-check --offline --skip-tests
```

## License

Apache-2.0 for EasyScanPKG source. See [LICENSE](LICENSE) and [NOTICE](NOTICE)
for third-party Sonar runtime terms.
