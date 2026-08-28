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
| Create/bind project | `./bin/sonar-project --local create KEY --workspace "$PWD"` |
| Scan sources | `./bin/sonar-scan --workspace "$PWD" --sources <dirs>` |
| List / resolve issues | `./bin/sonar-issues --local list` / `resolve ISSUE` |
| Language probe | `./bin/sonar-languages --local` |
| Credentials | `./bin/sonar-credentials --cli --test` |

## Skills (auto-installed)

- `sonar-local-ops` — local projects, scan, issues
- `sonar-mcp-lifecycle` — MCP up/down, credentials
- `sonar-agent-analysis` — end-of-task analyze via IDE/MCP

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
| `~/.config/sft/desktop.env` | Workspace for EasyScan launcher |
| `~/.config/sft/bridge.env` | `BRIDGE` / `SFT_AGENT_BRIDGE` path |

## License

Apache-2.0 for EasyScanPKG source. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for third-party Sonar runtime terms.

## Tests

```bash
python3 -m unittest discover -s tests -v
./bin/easyscan-check --offline --skip-tests
```
