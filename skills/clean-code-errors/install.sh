#!/usr/bin/env bash
# Install the clean-code-errors skill for Cursor, Codex, and other agents.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BRIDGE="$(cd "$ROOT/../.." && pwd)"
DESTINATIONS=(
  "${CURSOR_SKILLS_DIR:-$HOME/.cursor/skills}/clean-code-errors"
  "${CODEX_SKILLS_DIR:-$HOME/.codex/skills}/clean-code-errors"
  "${AGENTS_SKILLS_DIR:-$HOME/.agents/skills}/clean-code-errors"
)
for dst in "${DESTINATIONS[@]}"; do
  mkdir -p "$(dirname "$dst")"
  rm -rf "$dst"
  mkdir -p "$dst"
  cp -a "$ROOT/SKILL.md" "$ROOT/install.sh" "$dst/"
  echo "$BRIDGE" >"$dst/.bridge-root"
  echo "installed clean-code-errors -> $dst"
done
echo "Done. Start a new agent chat so it picks up clean-code-errors."
