#!/usr/bin/env bash
# Install agent-bridge skills into ~/.cursor/skills/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${CURSOR_SKILLS_DIR:-$HOME/.cursor/skills}"
mkdir -p "$DEST"
SKILLS=(
  easyscan-bootstrap
  sonar-fix-queue
  sonar-mcp-lifecycle
  sonar-agent-analysis
  sonar-local-ops
)
for skill in "${SKILLS[@]}"; do
  src="$ROOT/skills/$skill"
  dst="$DEST/$skill"
  if [[ ! -d "$src" ]]; then
    echo "skip missing skill: $skill" >&2
    continue
  fi
  rm -rf "$dst"
  mkdir -p "$dst"
  cp -a "$src/." "$dst/"
  # Point skill docs at this checkout when present
  echo "$ROOT" >"$dst/.bridge-root"
  echo "installed $skill -> $dst"
done
echo "Done. Restart Cursor agents or start a new chat to pick up skills."
