# AGENTS.md

EasyScanPKG is a Bash + Python (stdlib-only) helper toolkit that wires a local
(or remote) **SonarQube** server into Cursor/Codex: it spins up a local
SonarQube Community + PostgreSQL Docker stack, bootstraps tokens, runs the
SonarScanner CLI, and exports an agent-ingestible issue checklist.

Standard dev commands are documented in `README.md` and `CONTRIBUTING.md`:
- Unit tests: `python3 -m unittest discover -s tests -v`
- Offline readiness/lint: `./bin/easyscan-check --offline`
- Full readiness: `./bin/easyscan-check` (needs Docker + local server)

## Cursor Cloud specific instructions

- **Python is stdlib-only.** There are no pip/npm dependencies to install. Unit
  tests (`python3 -m unittest discover -s tests -v`) and the offline check
  (`./bin/easyscan-check --offline --skip-tests`) run with just Python 3 and no
  Docker — this is the fastest inner dev loop and mirrors CI (`.github/workflows/ci.yml`).

- **Docker is pre-installed in the environment but the daemon is NOT running on
  startup** (this VM has no systemd). The full end-to-end flow (local SonarQube
  stack, scanner, MCP) needs Docker, so before running `sonar-local-up`,
  `sonar-scan`, `easyscan-check --require-local`, etc., start the daemon and set
  the Elasticsearch kernel setting SonarQube requires:
  - `sudo sysctl -w vm.max_map_count=262144`  (resets on reboot; required or the
    SonarQube container's Elasticsearch will fail to start)
  - Start `dockerd` in the background (e.g. in a tmux session): `sudo dockerd`
  - If `docker` needs sudo, either run as the `docker` group or
    `sudo chmod 666 /var/run/docker.sock` (the `ubuntu` user is already in the
    `docker` group; a fresh login/`newgrp docker` also works).
  - Docker is configured with the `fuse-overlayfs` storage driver and
    `containerd-snapshotter=false` in `/etc/docker/daemon.json` — this
    combination is required for Docker to run inside this VM. Do not remove it.

- **Local SonarQube lifecycle:** `./bin/sonar-local-up` starts the
  `sft-sonarqube` + `sft-sonarqube-db` containers (compose file at
  `docker/docker-compose.sonar-local.yml`), waits for the server to be UP on
  `http://127.0.0.1:9000`, auto-mints an admin token into
  `~/.config/sft/sonar-local.env`, and (by default) downloads/installs the
  **SonarOpenCommunity/sonar-cxx** plugin JAR into the extensions volume for
  Community C/C++ support (`cxx` language key — not commercial CFamily). Disable
  with `SFT_INSTALL_SONAR_CXX=0`. First boot + first cxx install can take several
  minutes (JAR download + Sonar restart). Stop with `./bin/sonar-local-down`.

- **Dogfood / self-scan:** stock `sonar-scan` exclusions hide `**/bin/**`. When
  scanning EasyScanPKG itself use:
  `./bin/sonar-scan --workspace "$PWD" --sources lib,bin,hooks --project-key local-easyscanpkg --exclusions '**/obj/**,**/node_modules/**,**/.git/**,**/__pycache__/**,**/.venv/**'`
  then `./bin/sonar-issues --local export --workspace "$PWD" --project-key local-easyscanpkg --refresh`.
  Note: `sonar-scan`'s post-scan issue printout can briefly read `0` due to indexing
  lag — re-query with `sonar-issues list` for the real totals.

- Config/state lives under `~/.config/sft/` (not in the repo). Secret files
  (`sonar.env`, `sonar-local.env`, `sonar-local-admin.json`) must never be committed.
  Plugin JARs cache under `~/.config/sft/plugins/` (also not committed).
