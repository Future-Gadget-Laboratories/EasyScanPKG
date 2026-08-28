#!/usr/bin/env bash
# Finish Sonar agent-bridge integration (see docs/superpowers/plans/2026-08-25-sonar-agent-bridge.md)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export BRIDGE="$ROOT"
WORKSPACE="${1:-$HOME/Desktop/IS-P2QR/CB/CipherBank-src}"
PROJECT_KEY="${SONARQUBE_PROJECT_KEY:-CB-st_CipherBank-src_d25eb365-b11f-4144-9d9c-03aa1434d528}"

echo "==> Sonar integration finish (workspace: $WORKSPACE)"

# 1) Credentials file
mkdir -p "$HOME/.config/sft"
if [[ ! -f "$HOME/.config/sft/sonar.env" ]]; then
  cat > "$HOME/.config/sft/sonar.env" <<EOF
# Sonar credentials for agent-bridge / MCP (never commit real tokens)
SONARQUBE_TOKEN=
SONARQUBE_URL=https://sonar.cipherbank.money
SONARQUBE_PROJECT_KEY=$PROJECT_KEY
EOF
  chmod 600 "$HOME/.config/sft/sonar.env"
  echo "Created ~/.config/sft/sonar.env — edit and set SONARQUBE_TOKEN before MCP will authenticate"
else
  echo "Using existing ~/.config/sft/sonar.env"
fi

set -a
# shellcheck disable=SC1091
source "$HOME/.config/sft/sonar.env"
set +a

if [[ -z "${SONARQUBE_TOKEN:-}" ]]; then
  echo "WARNING: SONARQUBE_TOKEN is empty — connected mode and MCP auth will fail until set"
fi

# 2) Docker image (best effort)
if command -v docker >/dev/null 2>&1; then
  if docker pull sonarsource/sonarqube-mcp; then
    echo "Pulled sonarsource/sonarqube-mcp"
  else
    echo "WARNING: docker pull failed (permission? add user to docker group and re-login)"
  fi
else
  echo "WARNING: docker not found"
fi

# 3) Cursor MCP merge
python3 "$ROOT/lib/merge_cursor_mcp.py"

# 4) Codex config
mkdir -p "$HOME/.codex"
CODEX_CFG="$HOME/.codex/config.toml"
if [[ ! -f "$CODEX_CFG" ]] || ! grep -q 'mcp_servers.sonarqube' "$CODEX_CFG" 2>/dev/null; then
  cat "$ROOT/templates/codex.config.toml.snippet" >> "$CODEX_CFG"
  echo "Appended Sonar MCP block to $CODEX_CFG"
fi

# 5) Skills
"$ROOT/bin/install-skills.sh"

# 6) Policy + bind
UP_ARGS=(--workspace "$WORKSPACE" --no-prompt)
"$ROOT/bin/sonar-mcp-up" "${UP_ARGS[@]}"
python3 "$ROOT/lib/merge_codex_config.py"
if [[ -n "${SONARQUBE_URL:-}" ]]; then
  "$ROOT/bin/sonar-policy" connection --url "$SONARQUBE_URL" --prefer-connected true
fi
"$ROOT/bin/sonar-policy" bind "$WORKSPACE" "$PROJECT_KEY"

# 7) Project overlay
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

# 8) Cursor hook (user-level, fail-open)
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

# 9) Status
"$ROOT/bin/sonar-mcp-status" --json || true
"$ROOT/bin/sonar-ide-port" --workspace "$WORKSPACE" || echo "IDE port not detected — install SonarQube for IDE in Cursor and open the workspace"

echo ""
echo "Done. Next steps:"
echo "  1. Run: $ROOT/bin/sonar-credentials --test   (or set SONARQUBE_TOKEN in ~/.config/sft/sonar.env)"
echo "  2. Or run: $ROOT/install.sh --workspace \"$WORKSPACE\""
echo "  3. Install 'SonarQube for IDE' in Cursor; restart Cursor"
echo "  4. Local fallback: $ROOT/bin/sonar-local-up (auto-used when remote fails)"
echo "  5. Codex: source ~/.config/sft/sonar.env && codex"
