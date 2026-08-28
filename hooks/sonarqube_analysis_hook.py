#!/usr/bin/env python3
"""Cursor afterFileEdit / Windsurf-style hook: ask IDE to analyze a written file."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
ANALYZE = BRIDGE_ROOT / "bin" / "sonar-analyze"


def extract_file_path(event: dict) -> str | None:
    # Cursor afterFileEdit style
    for key in ("file_path", "filePath", "path"):
        if event.get(key):
            return str(event[key])
    tool_info = event.get("tool_info") or event.get("toolInfo") or {}
    if isinstance(tool_info, dict):
        for key in ("file_path", "filePath", "path"):
            if tool_info.get(key):
                return str(tool_info[key])
    # edits array
    edits = event.get("edits") or event.get("file_edits") or []
    if edits and isinstance(edits, list):
        first = edits[0]
        if isinstance(first, dict):
            for key in ("file_path", "filePath", "path"):
                if first.get(key):
                    return str(first[key])
    return None


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print("No event on stdin; failing open")
        return 0
    try:
        event = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"JSON error (fail-open): {exc}")
        return 0

    path = extract_file_path(event)
    if not path:
        print("No file path in event (fail-open)")
        return 0

    if not ANALYZE.is_file():
        print(f"Missing analyzer at {ANALYZE} (fail-open)")
        return 0

    result = subprocess.run(
        [sys.executable, str(ANALYZE), path, "--workspace", str(Path(path).parent)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip(), file=sys.stderr)
    # Always fail-open for editor hooks
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
