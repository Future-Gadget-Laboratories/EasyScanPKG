#!/usr/bin/env python3
"""Cursor afterFileEdit / Windsurf-style hook: ask IDE to analyze a written file."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
ANALYZE = BRIDGE_ROOT / "bin" / "sonar-analyze"
_PATH_KEYS = ("file_path", "filePath", "path")


def _path_from_mapping(data: Mapping[str, Any] | None) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in _PATH_KEYS:
        if data.get(key):
            return str(data[key])
    return None


def extract_file_path(event: dict) -> str | None:
    direct = _path_from_mapping(event)
    if direct:
        return direct
    nested = _path_from_mapping(event.get("tool_info") or event.get("toolInfo"))
    if nested:
        return nested
    edits = event.get("edits") or event.get("file_edits") or []
    if edits and isinstance(edits, list) and isinstance(edits[0], dict):
        return _path_from_mapping(edits[0])
    return None


def main() -> None:
    """Fail-open editor hook: never raise; never block the edit."""
    raw = sys.stdin.read()
    if not raw.strip():
        print("No event on stdin; failing open")
        return
    try:
        event = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"JSON error (fail-open): {exc}")
        return

    path = extract_file_path(event)
    if not path:
        print("No file path in event (fail-open)")
        return

    if not ANALYZE.is_file():
        print(f"Missing analyzer at {ANALYZE} (fail-open)")
        return

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


if __name__ == "__main__":
    main()
