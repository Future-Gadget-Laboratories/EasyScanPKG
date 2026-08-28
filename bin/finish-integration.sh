#!/usr/bin/env bash
# Finish EasyScanPKG integration for an arbitrary workspace.
# Prefer ./commission.sh for first-time Mint/Ubuntu setup.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export BRIDGE="$ROOT"
export SFT_AGENT_BRIDGE="$ROOT"

WORKSPACE="${1:-$(pwd)}"
WORKSPACE="$(cd "$WORKSPACE" && pwd)"
PROJECT_KEY="${SONARQUBE_PROJECT_KEY:-local-$(basename "$WORKSPACE")}"

echo "==> EasyScanPKG finish (workspace: $WORKSPACE)"

mkdir -p "$HOME/.config/sft"
cat > "$HOME/.config/sft/bridge.env" <<EOF
export SFT_AGENT_BRIDGE="$ROOT"
export BRIDGE="$ROOT"
EOF
chmod 600 "$HOME/.config/sft/bridge.env"

if [[ ! -f "$HOME/.config/sft/sonar.env" ]]; then
  cat > "$HOME/.config/sft/sonar.env" <<EOF
# Optional remote SonarQube (user token — never commit)
SONARQUBE_TOKEN=
SONARQUBE_URL=
SONARQUBE_PROJECT_KEY=$PROJECT_KEY
EOF
  chmod 600 "$HOME/.config/sft/sonar.env"
  echo "Created ~/.config/sft/sonar.env — set token/URL if using a remote server"
else
  echo "Using existing ~/.config/sft/sonar.env"
fi

set -a
# shellcheck disable=SC1091
source "$HOME/.config/sft/sonar.env"
set +a

if command -v docker >/dev/null 2>&1; then
  docker pull sonarsource/sonarqube-mcp >/dev/null || \
    echo "WARNING: docker pull sonarsource/sonarqube-mcp failed"
else
  echo "WARNING: docker not found"
fi

python3 "$ROOT/lib/merge_cursor_mcp.py"

mkdir -p "$HOME/.codex"
CODEX_CFG="$HOME/.codex/config.toml"
if [[ ! -f "$CODEX_CFG" ]] || ! grep -q 'mcp_servers.sonarqube' "$CODEX_CFG" 2>/dev/null; then
  cat "$ROOT/templates/codex.config.toml.snippet" >> "$CODEX_CFG"
  echo "Appended Sonar MCP block to $CODEX_CFG"
fi

"$ROOT/bin/install-skills.sh"

UP_ARGS=(--workspace "$WORKSPACE" --no-prompt)
"$ROOT/bin/sonar-mcp-up" "${UP_ARGS[@]}"
python3 "$ROOT/lib/merge_codex_config.py"
if [[ -n "${SONARQUBE_URL:-}" ]]; then
  "$ROOT/bin/sonar-policy" connection --url "$SONARQUBE_URL" --prefer-connected true
fi
"$ROOT/bin/sonar-policy" bind "$WORKSPACE" "$PROJECT_KEY"

mkdir -p "$WORKSPACE/.sft"
if [[ ! -f "$WORKSPACE/.sft/sonar-policy.json" ]]; then
  cp "$ROOT/templates/project.sonar-policy.json" "$WORKSPACE/.sft/sonar-policy.json"
  python3 <<PY
import json
from pathlib import Path
p = Path("${WORKSPACE}/.sft/sonar-policy.json")
data = json.loads(p.read_text())
data["project_key"] = "${PROJECT_KEY}"
p.write_text(json.dumps(data, indent=2) + "\n")
PY
  echo "Created $WORKSPACE/.sft/sonar-policy.json"
fi

mkdir -p "$HOME/.cursor/hooks"
ln -sf "$ROOT/hooks/sonarqube_analysis_hook.py" "$HOME/.cursor/hooks/sonarqube_analysis_hook.py"
HOOKS_JSON="$HOME/.cursor/hooks.json"
python3 <<PY
import json
from pathlib import Path
p = Path("${HOOKS_JSON}")
data = {"version": 1, "hooks": {"afterFileEdit": [{"command": "./hooks/sonarqube_analysis_hook.py"}]}}
if p.exists():
    existing = json.loads(p.read_text())
    existing.setdefault("hooks", {})
    edits = existing["hooks"].setdefault("afterFileEdit", [])
    cmd = {"command": "./hooks/sonarqube_analysis_hook.py"}
    if cmd not in edits:
        edits.append(cmd)
    data = existing
p.write_text(json.dumps(data, indent=2) + "\n")
print(f"Updated {p}")
PY

"$ROOT/bin/sonar-mcp-status" --json || true
"$ROOT/bin/sonar-ide-port" --workspace "$WORKSPACE" || \
  echo "IDE port not detected — install SonarQube for IDE in Cursor"

echo ""
echo "Done. Prefer ./commission.sh for full one-click setup."
echo "Verify: $ROOT/bin/easyscan-check --require-local"
