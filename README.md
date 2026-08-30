# EasyScanPKG

One-click local **SonarQube** helper for **Cursor** / **Codex**: Docker Community stack, MCP wiring, IDE bridge detection, scanner CLI, policy DB, and agent skills.

> Requires Docker. Pulls official Sonar Community / scanner images at runtime — this repo does **not** redistribute Sonar binaries.

## First-time commission (Linux Mint / Ubuntu)

```bash
cd EasyScanPKG
chmod +x commission.sh install.sh bin/*
./commission.sh --workspace /path/to/your/project --skip-remote-creds --no-prompt
./bin/easyscan-check --require-local
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--no-prompt` | Skip credential dialogs |
| `--skip-remote-creds` | Local Docker only |
| `--no-favorites` | Skip Cinnamon favorites pin |
| `--skip-check` | Skip final `easyscan-check` |

Daily: click **EasyScan** on the panel, or run `bin/sonar-desktop`.

## Verify install

```bash
./bin/easyscan-check                 # full readiness
./bin/easyscan-check --offline       # CI / no daemon
./bin/easyscan-check --require-local # must have Sonar UP + token
./install.sh --check-only            # offline check without installing
```

## Commands

| Action | Command |
| --- | --- |
| Spin up + open Cursor | `sonar-desktop` / EasyScan icon |
| Named contexts (multi-project) | `./bin/sonar-context create\|list\|use\|bind` |
| Create/bind project | `./bin/sonar-project --local create KEY --workspace "$PWD"` |
| Scan sources | `./bin/sonar-scan --workspace "$PWD" --sources <dirs> [--context NAME]` |
| List / resolve issues | `./bin/sonar-issues --local list` / `resolve ISSUE` |
| Export issue checklist | `./bin/sonar-issues export --workspace "$PWD" --refresh` |
| Quality profile XML | `./bin/sonar-profile import FILE.xml --local --bind-project KEY` |
| Language probe | `./bin/sonar-languages --local` |
| Credentials | `./bin/sonar-credentials --cli --test` |

### Multi-project contexts

Register each GitHub/Sonar target as a **named context** (URL + token file ref + project key + tags). Local Community still uses one Sonar instance; isolation is by project key + quality profile.

```bash
./bin/sonar-context create cipherbank --url http://127.0.0.1:9000 \
  --project-key local-cipherbank --gh acme/cipherbank --use
./bin/sonar-project --context cipherbank create local-cipherbank --workspace "$PWD"
./bin/sonar-scan --context cipherbank --workspace "$PWD" --sources src
```

### Issue checklist (done when empty)

```bash
./bin/sonar-issues --local export --workspace "$PWD" --refresh
# writes .sft/issue-checklist.md (+ .json). Re-export after fixes; stop at open_count=0.
```

### Quality profile XML import

```bash
./bin/sonar-profile --local export --language py --name "Sonar way" -o /tmp/sonar-way-py.xml
./bin/sonar-profile --local import /tmp/custom-py.xml --set-default --bind-project local-cipherbank \
  --remediation templates/remediation.example.json
```


## Skills (auto-installed)

- `easyscan-bootstrap` — activate Sonar in any agent environment
- `sonar-fix-queue` — find `.sft/issue-checklist.md` and fix until empty
- `sonar-local-ops` — local projects, scan, issues
- `sonar-mcp-lifecycle` — MCP up/down, credentials
- `sonar-agent-analysis` — end-of-task analyze via IDE/MCP

See [docs/AGENT_SONAR_PLAYBOOK.md](docs/AGENT_SONAR_PLAYBOOK.md) and [docs/FIX_QUEUE.md](docs/FIX_QUEUE.md).

## Language support (local Community)

| Language | Local Community image | Notes |
| --- | --- | --- |
| Python, JS/TS, Java, C#, Go, … | Yes | `sonar-scan` |
| C / C++ / Obj-C | Usually **no** | Needs CFamily (Developer+/Cloud); Build Wrapper not vendored |
| Assembly | No | No first-party analyzer |
| Julia | No | No official Sonar plugin |

## Config files

| File | Purpose |
| --- | --- |
| `~/.config/sft/sonar.env` | Optional remote URL/token |
| `~/.config/sft/sonar-local.env` | Auto local token (never commit) |
| `~/.config/sft/sonar-policy/policy.db` | Policy + named contexts (no tokens) |
| `~/.config/sft/desktop.env` | Workspace for EasyScan launcher |
| `~/.config/sft/bridge.env` | `BRIDGE` / `SFT_AGENT_BRIDGE` path |
| `<repo>/.sft/issue-checklist.md` | Agent-ingestible open-issue checklist |
| `<repo>/.sft/sonar-policy.json` | Optional per-repo preference overlay |

## License

Apache-2.0 for EasyScanPKG source. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for third-party Sonar runtime terms.

## Tests

```bash
python3 -m unittest discover -s tests -v
./bin/easyscan-check --offline --skip-tests
```
