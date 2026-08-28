#!/usr/bin/env python3
"""Merge SonarQube MCP server into ~/.cursor/mcp.json from env + template."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def merge_mcp_config(bridge: Path | None = None) -> Path:
    bridge = bridge or Path(os.environ.get("BRIDGE", Path(__file__).resolve().parents[1]))
    mcp_path = Path.home() / ".cursor" / "mcp.json"
    snippet = json.loads((bridge / "templates/cursor.mcp.json.snippet").read_text(encoding="utf-8"))
    data = json.loads(mcp_path.read_text(encoding="utf-8")) if mcp_path.exists() else {"mcpServers": {}}
    data.setdefault("mcpServers", {}).update(snippet["mcpServers"])
    env = data["mcpServers"]["sonarqube"]["env"]
    for key in (
        "SONARQUBE_TOKEN",
        "SONARQUBE_URL",
        "SONARQUBE_ORG",
        "SONARQUBE_PROJECT_KEY",
        "SONARQUBE_IDE_PORT",
    ):
        val = os.environ.get(key)
        if val:
            env[key] = val
        else:
            env.pop(key, None)
    # Drop any leftover template placeholders
    for key, val in list(env.items()):
        if isinstance(val, str) and val.startswith("${") and val.endswith("}"):
            del env[key]
    mcp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return mcp_path


if __name__ == "__main__":
    path = merge_mcp_config()
    print(f"Updated {path}")
