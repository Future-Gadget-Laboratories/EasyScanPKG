#!/usr/bin/env bash
# Portable EasyScanPKG installer for Cursor + Codex
# Usage: ./install.sh [--workspace PATH] [--project-key KEY] [--no-prompt]
# First-time Mint desktop commission (favorites icon + local stack):
#   ./commission.sh --workspace /path/to/project
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
export BRIDGE="$ROOT"
export SFT_AGENT_BRIDGE="$ROOT"

WORKSPACE=""
PROJECT_KEY=""
NO_PROMPT=0
INSTALL_ZENITY=0
INSTALL_COMPOSE=0
SKIP_CHECK=0
CHECK_ONLY=0

# Example only — never required
EXAMPLE_WS="$HOME/Desktop/IS-P2QR/CB/CipherBank-src"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

  --workspace PATH     Workspace to bind (default: \$PWD; uses example path only if present)
  --project-key KEY    SonarQube project key (default: local-<dirname>)
  --no-prompt          Skip credential GUI prompts on first spin-up
  --install-zenity     Attempt to install zenity for credential dialogs (apt)
  --install-compose    Install docker-compose-v2 plugin via apt (Ubuntu)
  --skip-check         Skip easyscan-check at end
  --check-only         Run easyscan-check --offline and exit (no install)
  -h, --help           Show this help

Installs EasyScanPKG:
  - ~/.config/sft/ sonar env + policy DB
  - Cursor MCP + hooks + skills
  - Codex MCP config
  - Local SonarQube Docker stack (fallback)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace) WORKSPACE="$2"; shift 2 ;;
    --project-key) PROJECT_KEY="$2"; shift 2 ;;
    --no-prompt) NO_PROMPT=1; shift ;;
    --install-zenity) INSTALL_ZENITY=1; shift ;;
    --install-compose) INSTALL_COMPOSE=1; shift ;;
    --skip-check) SKIP_CHECK=1; shift ;;
    --check-only) CHECK_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  chmod +x "$ROOT"/bin/* 2>/dev/null || true
  exec "$ROOT/bin/easyscan-check" --offline --skip-tests
fi

if [[ -z "$WORKSPACE" ]]; then
  if [[ -d "$EXAMPLE_WS" ]]; then
    WORKSPACE="$EXAMPLE_WS"
  else
    WORKSPACE="$(pwd)"
  fi
fi
WORKSPACE="$(cd "$WORKSPACE" && pwd)"

if [[ -z "$PROJECT_KEY" ]]; then
  PROJECT_KEY="local-$(basename "$WORKSPACE" | tr -c 'A-Za-z0-9._-' '-' | cut -c1-80)"
fi

echo "==> EasyScanPKG install"
echo "    BRIDGE=$BRIDGE"
echo "    WORKSPACE=$WORKSPACE"
echo "    PROJECT_KEY=$PROJECT_KEY"

# Record install location for skills and scripts
mkdir -p "$HOME/.config/sft"
echo "export SFT_AGENT_BRIDGE=\"$ROOT\"" > "$HOME/.config/sft/bridge.env"
echo "export BRIDGE=\"$ROOT\"" >> "$HOME/.config/sft/bridge.env"
chmod 600 "$HOME/.config/sft/bridge.env"

# Optional shell hook snippet
PROFILE_SNIPPET="$HOME/.config/sft/sonar-shell.sh"
cat > "$PROFILE_SNIPPET" <<'SNIP'
# EasyScanPKG — source from ~/.bashrc if desired
[ -f "$HOME/.config/sft/bridge.env" ] && . "$HOME/.config/sft/bridge.env"
[ -f "$HOME/.config/sft/sonar.env" ] && set -a && . "$HOME/.config/sft/sonar.env" && set +a
[ -f "$HOME/.config/sft/sonar-local.env" ] && set -a && . "$HOME/.config/sft/sonar-local.env" && set +a
alias sonar-up='${SFT_AGENT_BRIDGE:-$BRIDGE}/bin/sonar-mcp-up'
alias sonar-desktop='${SFT_AGENT_BRIDGE:-$BRIDGE}/bin/sonar-desktop'
alias sonar-creds='${SFT_AGENT_BRIDGE:-$BRIDGE}/bin/sonar-credentials'
alias sonar-local='${SFT_AGENT_BRIDGE:-$BRIDGE}/bin/sonar-local-up'
alias sonar-scan='${SFT_AGENT_BRIDGE:-$BRIDGE}/bin/sonar-scan'
alias sonar-issues='${SFT_AGENT_BRIDGE:-$BRIDGE}/bin/sonar-issues'
alias easyscan-check='${SFT_AGENT_BRIDGE:-$BRIDGE}/bin/easyscan-check'
SNIP
echo "    shell helpers: $PROFILE_SNIPPET"

if [[ "$INSTALL_ZENITY" -eq 1 ]] && ! command -v zenity >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y zenity || true
  fi
fi

# Remote env template
if [[ ! -f "$HOME/.config/sft/sonar.env" ]]; then
  cat > "$HOME/.config/sft/sonar.env" <<EOF
# Remote SonarQube server (user token — never commit)
SONARQUBE_TOKEN=
SONARQUBE_URL=
SONARQUBE_PROJECT_KEY=${PROJECT_KEY:-}
EOF
  chmod 600 "$HOME/.config/sft/sonar.env"
fi

# Docker compose plugin (required for local SonarQube stack)
if command -v docker >/dev/null 2>&1; then
  if ! docker compose version >/dev/null 2>&1; then
    echo "WARN: 'docker compose' plugin not installed (needed for sonar-local-up)"
    if [[ "$INSTALL_COMPOSE" -eq 1 ]] && command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update && sudo apt-get install -y docker-compose-v2
    else
      echo "      Install with: sudo apt install docker-compose-v2"
      echo "      Or re-run: ./install.sh --install-compose"
    fi
  fi
fi

# Docker images
if command -v docker >/dev/null 2>&1; then
  DOCKER_PULL='docker pull'
  if ! docker info >/dev/null 2>&1; then
    if groups | grep -qw docker && command -v sg >/dev/null 2>&1; then
      DOCKER_PULL='sg docker -c "docker pull'
      DOCKER_SUFFIX='"'
      echo "NOTE: using 'sg docker' — your shell has not refreshed the docker group yet."
      echo "      For a permanent fix: log out/in, or run: newgrp docker"
    else
      echo "WARN: docker permission denied — run: sudo usermod -aG docker \$USER && newgrp docker"
    fi
  fi
  for img in sonarsource/sonarqube-mcp sonarqube:community postgres:16-alpine sonarsource/sonar-scanner-cli; do
    if [[ "$DOCKER_PULL" == "docker pull" ]]; then
      docker pull "$img" || echo "WARN: pull failed for $img"
    else
      sg docker -c "docker pull $img" || echo "WARN: pull failed for $img"
    fi
  done
  # CFamily Build Wrapper — only present on editions that ship C/C++ analysis
  if [[ -f "$HOME/.config/sft/sonar-local.env" ]] || curl -s -o /dev/null -w '' -m 2 http://127.0.0.1:9000/api/system/status 2>/dev/null; then
    "$ROOT/bin/sonar-languages" --local --install-build-wrapper 2>/dev/null || \
      echo "NOTE: C/C++ Build Wrapper not available on this Sonar edition (expected for Community)"
  fi
else
  echo "WARN: docker not found — install Docker for MCP and local fallback"
fi

# Python deps: stdlib only
chmod +x "$ROOT"/bin/* "$ROOT"/hooks/*.py "$ROOT"/lib/*.py 2>/dev/null || true

# Client configs + skills + hooks via finish-integration
ARGS=("$WORKSPACE")
"$ROOT/bin/finish-integration.sh" "${ARGS[@]}"

# Policy: enable local fallback
"$ROOT/bin/sonar-policy" set connection fallback_to_local_server true

# Bind project
"$ROOT/bin/sonar-policy" bind "$WORKSPACE" "$PROJECT_KEY" >/dev/null || true

# Spin up with connection resolution (prompts if credentials missing)
UP_ARGS=(--workspace "$WORKSPACE")
[[ "$NO_PROMPT" -eq 1 ]] && UP_ARGS+=(--no-prompt)
"$ROOT/bin/sonar-mcp-up" "${UP_ARGS[@]}" || true

# Unit tests
if command -v python3 >/dev/null 2>&1; then
  (cd "$ROOT" && python3 -m unittest discover -s tests -v) || echo "WARN: some tests failed"
fi

# Skills (ensure present for agents)
"$ROOT/bin/install-skills.sh" || true

if [[ "$SKIP_CHECK" -eq 0 ]]; then
  echo "==> EasyScanPKG check"
  "$ROOT/bin/easyscan-check" --skip-tests || echo "WARN: easyscan-check reported failures"
fi

cat <<EOF

==> EasyScanPKG install complete

Package:    EasyScanPKG ($ROOT)
Config:     ~/.config/sft/sonar.env
Local env:  ~/.config/sft/sonar-local.env (auto-managed fallback)
Policy DB:  ~/.config/sft/sonar-policy/policy.db

Commands:
  sonar-up --workspace "\$PWD"          # spin up + resolve remote/local
  sonar-desktop                         # daily: local up + MCP + open Cursor
  easyscan-check                        # verify install readiness
  sonar-creds --test                    # update URL/token via dialog
  \$BRIDGE/bin/sonar-local-up           # start local SonarQube only
  \$BRIDGE/bin/sonar-local-down          # stop local stack

First-time Linux Mint commission (favorites icon + tokens):
  \$BRIDGE/commission.sh --workspace "$WORKSPACE"

Add to ~/.bashrc (optional):
  source ~/.config/sft/sonar-shell.sh

Manual:
  1. Install SonarQube for IDE (not just the SonarQube MCP plugin):
     cursor --install-extension SonarSource.sonarlint-vscode
     # or: \$BRIDGE/bin/sonar-ide-port --install
  2. Reload Cursor (Developer: Reload Window) with your workspace open
  3. Set SONARQUBE_TOKEN or run: \$BRIDGE/bin/sonar-credentials --test
  4. Click the EasyScan favorites icon after each reboot

Skills installed: sonar-mcp-lifecycle, sonar-agent-analysis, sonar-local-ops
EOF
