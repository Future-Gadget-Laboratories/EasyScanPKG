#!/usr/bin/env bash
# First-time machine commission: local Sonar + Cursor wiring + Mint favorites icon.
# Usage:
#   ./commission.sh [--workspace PATH] [--project-key KEY] [--remote-url URL]
#                   [--no-prompt] [--no-favorites] [--no-desktop-shortcut]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
export BRIDGE="$ROOT"
export SFT_AGENT_BRIDGE="$ROOT"

WORKSPACE=""
PROJECT_KEY=""
REMOTE_URL=""
NO_PROMPT=0
INSTALL_ZENITY=1
INSTALL_COMPOSE=1
FAVORITES=1
DESKTOP_SHORTCUT=1
SKIP_REMOTE_CREDS=0
SKIP_CHECK=0

# Example path only if present (not required)
DEFAULT_CB=""
EXAMPLE_WS="$HOME/Desktop/IS-P2QR/CB/CipherBank-src"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

First-time EasyScanPKG commission (Linux Mint / Ubuntu):
  1. Install configs, skills, MCP, hooks
  2. Ensure Docker + optional zenity / docker-compose-v2
  3. Install SonarQube for IDE in Cursor
  4. Start local SonarQube and bootstrap edit tokens
  5. Optionally collect remote URL/token
  6. Pin EasyScan to the panel favorites (+ Desktop shortcut)
  7. Run easyscan-check

Options:
  --workspace PATH          Project folder (default: example path if present, else \$PWD)
  --project-key KEY         Sonar project key (default: local-<dirname>)
  --remote-url URL          Optional remote SonarQube URL
  --no-prompt               Skip credential dialogs
  --skip-remote-creds       Do not prompt for remote token (local-only OK)
  --no-zenity               Do not apt-install zenity
  --no-compose              Do not apt-install docker-compose-v2
  --no-favorites            Do not pin to Cinnamon favorites
  --no-desktop-shortcut     Do not create ~/Desktop shortcut
  --skip-check              Skip final easyscan-check
  -h, --help                Show help

After commission, click the EasyScan favorites icon to spin up + open Cursor.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace) WORKSPACE="$2"; shift 2 ;;
    --project-key) PROJECT_KEY="$2"; shift 2 ;;
    --remote-url) REMOTE_URL="$2"; shift 2 ;;
    --no-prompt) NO_PROMPT=1; shift ;;
    --skip-remote-creds) SKIP_REMOTE_CREDS=1; shift ;;
    --no-zenity) INSTALL_ZENITY=0; shift ;;
    --no-compose) INSTALL_COMPOSE=0; shift ;;
    --no-favorites) FAVORITES=0; shift ;;
    --no-desktop-shortcut) DESKTOP_SHORTCUT=0; shift ;;
    --skip-check) SKIP_CHECK=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$WORKSPACE" ]]; then
  if [[ -n "$DEFAULT_CB" && -d "$DEFAULT_CB" ]]; then
    WORKSPACE="$DEFAULT_CB"
  elif [[ -d "$EXAMPLE_WS" ]]; then
    WORKSPACE="$EXAMPLE_WS"
  else
    WORKSPACE="$(pwd)"
  fi
fi
WORKSPACE="$(cd "$WORKSPACE" && pwd)"

if [[ -z "$PROJECT_KEY" ]]; then
  PROJECT_KEY="local-$(basename "$WORKSPACE" | tr -c 'A-Za-z0-9._-' '-' | cut -c1-80)"
fi

echo "==> EasyScanPKG commission"
echo "    BRIDGE=$ROOT"
echo "    WORKSPACE=$WORKSPACE"
echo "    PROJECT_KEY=$PROJECT_KEY"

# --- 1) Base install (configs, MCP, skills, images) ---
INSTALL_ARGS=(--workspace "$WORKSPACE" --project-key "$PROJECT_KEY" --skip-check)
[[ "$NO_PROMPT" -eq 1 ]] && INSTALL_ARGS+=(--no-prompt)
[[ "$INSTALL_ZENITY" -eq 1 ]] && INSTALL_ARGS+=(--install-zenity)
[[ "$INSTALL_COMPOSE" -eq 1 ]] && INSTALL_ARGS+=(--install-compose)
"$ROOT/install.sh" "${INSTALL_ARGS[@]}"

# Ensure remote URL is set even if token empty
mkdir -p "$HOME/.config/sft"
if [[ -n "$REMOTE_URL" ]] && [[ -f "$HOME/.config/sft/sonar.env" ]]; then
  REMOTE_URL="$REMOTE_URL" PROJECT_KEY="$PROJECT_KEY" python3 - <<'PY'
import os
from pathlib import Path
p = Path.home() / ".config" / "sft" / "sonar.env"
remote_url = os.environ["REMOTE_URL"]
project_key = os.environ["PROJECT_KEY"]
text = p.read_text(encoding="utf-8")
lines = []
seen_url = False
for line in text.splitlines():
    if line.startswith("SONARQUBE_URL="):
        lines.append(f"SONARQUBE_URL={remote_url}")
        seen_url = True
    elif line.startswith("SONARQUBE_PROJECT_KEY=") and not line.split("=", 1)[1].strip():
        lines.append(f"SONARQUBE_PROJECT_KEY={project_key}")
    else:
        lines.append(line)
if not seen_url:
    lines.append(f"SONARQUBE_URL={remote_url}")
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
fi

# --- 2) SonarQube for IDE extension ---
echo "==> Installing SonarQube for IDE extension"
"$ROOT/bin/sonar-ide-port" --install || true

# --- 3) Local server + token bootstrap ---
echo "==> Starting local SonarQube and bootstrapping token"
if ! "$ROOT/bin/sonar-local-up"; then
  echo "WARN: sonar-local-up failed — check Docker (newgrp docker) and retry"
else
  echo "    Local server ready; token in ~/.config/sft/sonar-local.env"
fi

# --- 4) Remote credentials (keys for connected edits) ---
if [[ "$SKIP_REMOTE_CREDS" -eq 0 ]]; then
  echo "==> Remote credentials"
  if [[ "$NO_PROMPT" -eq 1 ]]; then
    echo "    --no-prompt set; edit ~/.config/sft/sonar.env or run:"
    echo "      $ROOT/bin/sonar-credentials --cli --test"
  else
    CRED_ARGS=(--cli --test)
    [[ -n "$REMOTE_URL" ]] && CRED_ARGS+=(--url "$REMOTE_URL")
    "$ROOT/bin/sonar-credentials" "${CRED_ARGS[@]}" || \
      echo "WARN: remote credentials skipped — local fallback remains available"
  fi
fi

# --- 5) Wire MCP with whatever credentials we have ---
set -a
[[ -f "$HOME/.config/sft/sonar.env" ]] && . "$HOME/.config/sft/sonar.env"
[[ -f "$HOME/.config/sft/sonar-local.env" ]] && . "$HOME/.config/sft/sonar-local.env"
set +a
UP_ARGS=(--workspace "$WORKSPACE")
[[ "$NO_PROMPT" -eq 1 ]] && UP_ARGS+=(--no-prompt)
"$ROOT/bin/sonar-mcp-up" "${UP_ARGS[@]}" || true

# Persist desktop workspace
cat > "$HOME/.config/sft/desktop.env" <<EOF
# Written by commission.sh
SFT_SONAR_WORKSPACE=$WORKSPACE
SFT_SONAR_OPEN_CURSOR=1
EOF
chmod 600 "$HOME/.config/sft/desktop.env"

# --- 6) Desktop icon + favorites ---
echo "==> Installing desktop launcher + favorites"
DESKTOP_ARGS=(--workspace "$WORKSPACE")
[[ "$FAVORITES" -eq 0 ]] && DESKTOP_ARGS+=(--no-favorites)
[[ "$DESKTOP_SHORTCUT" -eq 0 ]] && DESKTOP_ARGS+=(--no-desktop-shortcut)
python3 "$ROOT/lib/desktop_install.py" "${DESKTOP_ARGS[@]}"

# Shell helpers always available
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
SNIP

# Offer to append to bashrc once
if [[ -f "$HOME/.bashrc" ]] && ! grep -q 'sonar-shell.sh' "$HOME/.bashrc" 2>/dev/null; then
  echo "" >> "$HOME/.bashrc"
  echo "# EasyScanPKG" >> "$HOME/.bashrc"
  echo "[ -f \"\$HOME/.config/sft/sonar-shell.sh\" ] && . \"\$HOME/.config/sft/sonar-shell.sh\"" >> "$HOME/.bashrc"
  echo "    appended source to ~/.bashrc"
fi

"$ROOT/bin/install-skills.sh" || true

if [[ "$SKIP_CHECK" -eq 0 ]]; then
  echo "==> EasyScanPKG check"
  "$ROOT/bin/easyscan-check" --require-local --skip-tests || \
    echo "WARN: easyscan-check reported failures — see output above"
fi

cat <<EOF

==> EasyScanPKG commission complete

Favorites icon:  EasyScan  (Cinnamon panel)
Desktop file:    ~/.local/share/applications/easyscan.desktop
Daily launcher:  $ROOT/bin/sonar-desktop
Workspace:       $WORKSPACE

Local token:     ~/.config/sft/sonar-local.env
Remote creds:    ~/.config/sft/sonar.env

Next:
  1. Click the EasyScan icon (or run sonar-desktop) after each reboot
  2. In Cursor: Developer → Reload Window so SonarQube for IDE starts its port
  3. Re-run sonar-desktop once so IDE port is detected and MCP is updated
  4. Optional remote token: $ROOT/bin/sonar-credentials --cli --test
  5. Verify: $ROOT/bin/easyscan-check --require-local

EOF
