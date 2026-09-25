#!/usr/bin/env bash
# Install agent-bridge skills into ~/.cursor/skills/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESTINATIONS=(
  "${CURSOR_SKILLS_DIR:-$HOME/.cursor/skills}"
  "${CODEX_SKILLS_DIR:-$HOME/.codex/skills}"
  "${AGENTS_SKILLS_DIR:-$HOME/.agents/skills}"
)
SKILLS=(
  easyscan-bootstrap
  sonar-fix-queue
  sonar-mcp-lifecycle
  sonar-agent-analysis
  sonar-local-ops
  clean-code-errors
)
for DEST in "${DESTINATIONS[@]}"; do
  mkdir -p "$DEST"
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
    echo "$ROOT" >"$dst/.bridge-root"
    echo "installed $skill -> $dst"
  done
done
echo "Done. Restart agents or start a new chat to pick up skills."
