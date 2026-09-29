# EasyScanPKG

Bash + Python (stdlib-only) toolkit that wires a local or remote **SonarQube**
server into **Cursor** / **Codex**: Docker Community stack, token bootstrap,
SonarScanner CLI, MCP helpers, and an agent-ingestible issue checklist.

The all-in-one stage `easyscan-scan` also runs **host** analyzers (ruff, bandit,
semgrep, shellcheck, cppcheck, clang-tidy, flawfinder, hadolint, gitleaks,
pip-audit, osv-scanner, sanitizers, valgrind, …). It detects what the project
contains, sends each kind of file to every installed tool that understands it,
and merges every finding into one checklist.

> Requires Docker for the Sonar path. Pulls official Sonar Community / scanner
> images at runtime — this repo does **not** redistribute Sonar binaries.
> Scanner tools are installed on the host by `bin/easyscan-install-scanners`
> (run automatically by `install.sh` / `commission.sh`), never bundled.

## Contents

- [First-time commission](#first-time-commission-linux-mint--ubuntu)
- [Verify install](#verify-install)
- [Core workflow](#core-workflow)
- [Command reference](#command-reference)
- [Multi-scanner stage (`easyscan-scan`)](#multi-scanner-stage-easyscan-scan)
- [Installing scanner tools](#installing-scanner-tools)
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
| `--skip-scanners` | Do not install the `easyscan-scan` tools (see [Installing scanner tools](#installing-scanner-tools)) |

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

# 3b) Or all-in-one multi-scanner: every installed tool that fits the project
./bin/easyscan-scan --workspace "$PWD" --plan          # preview the routing
./bin/easyscan-scan --workspace "$PWD" --project-key local-demo --sources src,lib

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
| Preview scanner routing | `./bin/easyscan-scan --workspace "$PWD" --plan` |
| Install / check scanner tools | `./bin/easyscan-install-scanners` / `--status` |
| List registered scanners | `./bin/easyscan-scan --list-scanners` |
| List / resolve issues | `./bin/sonar-issues --local list` / `resolve ISSUE` |
| Export issue checklist | `./bin/sonar-issues export --workspace "$PWD" --refresh` |
| Quality profile XML | `./bin/sonar-profile import FILE.xml --local --bind-project KEY` |
| Language probe | `./bin/sonar-languages --local` / `--install-cxx` |
| Credentials (remote) | `./bin/sonar-credentials --cli --test` |
| Credentials (local) | `./bin/sonar-credentials --local --bootstrap --test` |
| MCP up / down / status | `./bin/sonar-mcp-up` / `sonar-mcp-down` / `sonar-mcp-status` |

## Multi-scanner stage (`easyscan-scan`)

`easyscan-scan` is the scan harness. For every run it:

1. **Detects** what the workspace contains (Python incl. extensionless
   `#!/usr/bin/env python` scripts, shell, C/C++, Dockerfiles, requirements
   files, lockfiles, `compile_commands.json`, other languages), skipping
   `.git`, virtualenvs, `node_modules`, caches, `dist`, and `.sft`.
2. **Routes** each file type to the scanners that understand it — ruff gets the
   Python roots, shellcheck gets the shell scripts, cppcheck/flawfinder get the
   C/C++ roots, hadolint gets the Dockerfiles, pip-audit gets `requirements.txt`,
   and so on. Bandit skips test code (assert / `/tmp` noise).
3. **Runs** every scanner that applies *and* is installed (**auto mode**, the
   default), then merges findings into the unified checklist
   (schema `easyscan.issue-checklist/v2`). Each issue carries its `source`.

Every tool's levels are normalized onto one scale — `BLOCKER > CRITICAL > MAJOR >
MINOR > INFO` (bandit/semgrep MEDIUM → MAJOR, HIGH/ERROR → CRITICAL, LOW → MINOR) —
which is what `--fail-on-severity` compares against.

Scanners that are skipped are listed with the reason (`no C/C++ files`,
`not installed — run bin/easyscan-install-scanners`, `sonar server not ready`,
…) both in the scan plan and in the checklist's `sources_skipped`. A scanner
that crashes (bad config, network failure) is reported as `error: …` — never
as a clean run.

```bash
./bin/easyscan-scan --workspace "$PWD" --plan
```

```text
Scan plan — /work/app (languages: cpp, dockerfile, python)
  [RUN ] ruff            auto      12 Python files → app, tools/deploy
  [RUN ] cppcheck        auto      4 C/C++ files → native
  [RUN ] clang-tidy      auto      compile database build/compile_commands.json
  [RUN ] hadolint        auto      1 Dockerfile → Dockerfile
  [RUN ] pip-audit       auto      auditing requirements.txt
  [skip] shellcheck      auto      no shell scripts
  [skip] osv             auto      not installed — run bin/easyscan-install-scanners (osv-scanner)
  [skip] valgrind        auto      dynamic analysis needs a run command (--valgrind-command -- ./binary)
  …
```

**What auto mode will not guess:** dynamic tools (`asan`, `ubsan`, `valgrind`,
`drmemory`) need a program to run, `clang-analyzer` needs a build command or an
existing report, and `clang-tidy` needs a `compile_commands.json` (found
automatically in the root, `build/`, `out/`, or `build*/`). Sonar runs when
Docker is present and the configured server accepts the token.

`--mode manual` (or `EASYSCAN_MODE=manual`) restores the old behaviour: Sonar
only, every other tool opt-in via `--enable`.

### CLI options

```bash
./bin/easyscan-scan --help
```

| Option | Meaning |
| --- | --- |
| `--workspace PATH` | Project root (default: cwd) |
| `--mode auto\|manual` | `auto` (default): every installed, applicable tool. `manual`: Sonar + explicit `--enable` only |
| `--plan` | Print which scanners would run on which files, then exit (add `--json` for machine output) |
| `--fail-on-error` | Exit 2 if a selected scanner crashed — use in CI |
| `--fail-on-severity LEVEL` | Exit 3 if any open finding is at/above `LEVEL` (`info`, `minor`/`low`, `major`/`medium`, `critical`/`high`, `blocker`). This repo's CI uses `medium` |
| `--project-key KEY` | Sonar project key |
| `--context NAME` | Named analysis context (`sonar-context`) |
| `--local` | Prefer local Sonar credentials |
| `--sources CSV` | Sonar sources (passed through to `sonar-scan`) |
| `--exclusions CSV` | Sonar exclusions CSV |
| `--compile-commands PATH` | `compile_commands.json` for clang-tidy / Sonar (overrides auto-detection) |
| `--enable NAME` | Force a scanner on (repeatable) |
| `--disable NAME` | Force a scanner off (repeatable) |
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
# Everything that applies (default)
./bin/easyscan-scan --workspace "$PWD"

# CI gate: fail if a tool breaks or anything medium (MAJOR) or worse is found
./bin/easyscan-scan --workspace "$PWD" --fail-on-error --fail-on-severity medium

# Only secrets + dependency CVEs
./bin/easyscan-scan --workspace "$PWD" --scanners gitleaks,pip-audit,osv

# Add a dynamic memory check to the auto set (pick one style per project)
./bin/easyscan-scan --workspace "$PWD" --asan-command -- ./build/tests_asan
./bin/easyscan-scan --workspace "$PWD" --valgrind-command -- ./build/tests

# clang-analyzer with a build command lives in policy (see below)
```

## Installing scanner tools

`install.sh` and `commission.sh` run `bin/easyscan-install-scanners` by default
(`--skip-scanners` opts out). Run it yourself any time:

```bash
./bin/easyscan-install-scanners --status          # what is installed, where
./bin/easyscan-install-scanners                   # install every default tool
./bin/easyscan-install-scanners --workspace "$PWD" # only tools this project needs
./bin/easyscan-install-scanners --with drmemory   # add opt-in tools
./bin/easyscan-install-scanners --dry-run         # print the steps only
```

Each tool is tried through an ordered list of methods; the first that works wins:

| Method | Used for | Notes |
| --- | --- | --- |
| `apt-get` / `dnf` | shellcheck, cppcheck, flawfinder, clang-tidy, clang-tools (scan-build), valgrind, gitleaks | Needs root or sudo; `--non-interactive` uses `sudo -n`; `--no-system` skips it |
| pip (isolated venv) | ruff, bandit, semgrep, pip-audit, hadolint (`hadolint-bin`), shellcheck / flawfinder fallback | `~/.config/sft/scanners/venv`; creates `python3-venv` via apt if missing; `--no-pip` skips it |
| GitHub release | osv-scanner, gitleaks / hadolint fallback, drmemory | Linux x86_64/arm64; **SHA-256 verified** (release digest or checksum file) before install; `--no-download` skips it |

Everything that is not a distro package is linked into
`~/.config/sft/scanners/bin` (override the root with `EASYSCAN_TOOLS_HOME`).
`easyscan-scan` searches that directory as well as `PATH`, so no shell profile
changes are needed. Tools already on `PATH` are left alone. `asan`/`ubsan` use
your compiler (gcc or clang) and Sonar uses Docker, so neither is installed here.
Set `GITHUB_TOKEN` to avoid GitHub API rate limits on shared machines/CI.

## Scanner catalog

| Name | Role | Auto-runs when | Installed via |
| --- | --- | --- | --- |
| `sonar` | SonarQube analysis + issue export | Code found + Docker + server accepts token | Docker (`sonar-local-up`) |
| `ruff` | Python lint | Python files | pip |
| `bandit` | Python security | Non-test Python files | pip |
| `semgrep` | Polyglot SAST | Any code (needs semgrep.dev for registry rules like `p/default`) | pip |
| `shellcheck` | Shell lint | `*.sh` or `#!/bin/bash`-style scripts | apt → pip |
| `cppcheck` | C/C++ static analysis | C/C++ files | apt |
| `flawfinder` | C/C++ risky-API heuristics | C/C++ files | apt → pip |
| `clang-tidy` | C/C++ clang-tidy | C/C++ files + `compile_commands.json` | apt |
| `clang-analyzer` | Clang Static Analyzer | C/C++ + `build_command` / `report_dir` / `sarif` in policy | apt (`clang-tools`) |
| `hadolint` | Dockerfile lint | `Dockerfile*` / `Containerfile` | pip → GitHub |
| `gitleaks` | Secrets detection | Always (filesystem scan) | apt → GitHub |
| `pip-audit` | Python dependency CVEs | `requirements*.txt` | pip |
| `osv` | Lockfile / manifest CVEs | Lockfiles (`poetry.lock`, `package-lock.json`, `go.mod`, `Cargo.lock`, …) | GitHub |
| `asan` / `ubsan` | Sanitizer run adapters | `--asan-command` / `--ubsan-command` given | compiler |
| `valgrind` | Memcheck (Linux) | `--valgrind-command` given | apt |
| `drmemory` | Dr. Memory | `--drmemory-command` given | GitHub (opt-in: `--with drmemory`) |

### Overlap guidance

- **Dynamic memory:** prefer **one** of `asan`, `valgrind`, or `drmemory` per project.
- **C/C++ static:** `clang-tidy`, `cppcheck`, `flawfinder`, and `clang-analyzer`
  overlap. `flawfinder` is noisy; disable it in policy if it drowns out the rest.
- **Python:** `ruff` (style) + `bandit`/`semgrep` (security); Sonar still covers
  much of the quality surface.

## Controls (CLI, env, policy)

Every scanner starts as `"enabled": "auto"`. Any explicit `true`/`false` wins
over auto, with this precedence (later wins):

1. Built-in defaults (`auto` for every scanner)
2. Policy DB (`sonar-policy set scan scanners …`) / project overlay
3. Workspace file: `.sft/sonar-policy.json` or `.sft/scan-policy.json`
4. Environment variables
5. CLI (`--enable` / `--disable` / `--scanners` / command flags)

A scanner forced on still gets auto-routed paths unless you set `paths`.

### Environment variables

| Variable | Effect |
| --- | --- |
| `EASYSCAN_MODE` | `auto` (default) or `manual` |
| `EASYSCAN_FAIL_ON_SEVERITY` | Default for `--fail-on-severity` (e.g. `medium`) |
| `EASYSCAN_SCANNERS` | Comma list → exclusive enable set (like `--scanners`) |
| `EASYSCAN_ENABLE_<NAME>` | `1`/`true`/`on` or `0`/`false`/`off` per scanner |
| `EASYSCAN_EXCLUDE_DIRS` | Extra directory names the harness never routes (e.g. `vendor,third_party`) |
| `EASYSCAN_TOOLS_HOME` | Root for installed tools (default `~/.config/sft/scanners`) |
| `EASYSCAN_COMPILE_COMMANDS` | Path to `compile_commands.json` |
| `EASYSCAN_DRMEMORY_COMMAND` | Dr. Memory target command string |
| `EASYSCAN_ASAN_COMMAND` | ASan target command string |
| `EASYSCAN_UBSAN_COMMAND` | UBSan target command string |
| `EASYSCAN_VALGRIND_COMMAND` | Valgrind target command string |
| `EASYSCAN_SEMGREP_CONFIG` | Semgrep `--config` value (e.g. `p/owasp-top-ten` or a local rules dir) |

Enable-flag names map to scanner ids with `-` → `_`, uppercased:

```bash
export EASYSCAN_ENABLE_FLAWFINDER=0        # opt one tool out of auto
export EASYSCAN_SCANNERS=sonar,ruff,shellcheck   # exclusive set
./bin/easyscan-scan --workspace "$PWD"
```

### Workspace policy overlay

Copy and edit `templates/project.sonar-policy.json` → `.sft/sonar-policy.json`.
Only list what you want to change; everything else stays `auto`:

```json
{
  "scan": {
    "scanners": {
      "semgrep": { "config": "p/owasp-top-ten" },
      "clang-tidy": { "compile_commands": "cmake-out/compile_commands.json" },
      "clang-analyzer": { "build_command": ["make", "-C", "build"] },
      "cppcheck": { "std": "c++17" },
      "flawfinder": { "enabled": false },
      "ruff": { "enabled": true, "paths": ["src"] }
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
| `~/.config/sft/scanners/` | Scanner tools from `easyscan-install-scanners` (`bin/`, `venv/`) |
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
