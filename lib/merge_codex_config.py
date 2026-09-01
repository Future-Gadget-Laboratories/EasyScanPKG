#!/usr/bin/env python3
"""Merge SonarQube MCP block into ~/.codex/config.toml from environment."""

from __future__ import annotations

import os
import re
from pathlib import Path


def _replace_sonarqube_mcp_block(text: str, snippet: str) -> str:
    """Replace the [mcp_servers.sonarqube] TOML table with snippet."""
    header = "[mcp_servers.sonarqube]"
    start = text.find(header)
    if start < 0:
        return text.rstrip() + "\n\n" + snippet
    rest = text[start + len(header) :]
    # Next top-level table starts at a line beginning with '['.
    match = re.search(r"\n\[", rest)
    end = start + len(header) + match.start() if match else len(text)
    return text[:start] + snippet.strip() + "\n\n" + text[end:].lstrip("\n")


def merge_codex_config(bridge: Path | None = None) -> Path:
    bridge = bridge or Path(os.environ.get("BRIDGE", Path(__file__).resolve().parents[1]))
    cfg = Path.home() / ".codex" / "config.toml"
    snippet = (bridge / "templates/codex.config.toml.snippet").read_text(encoding="utf-8")

    env_lines = []
    for key in (
        "SONARQUBE_TOKEN",
        "SONARQUBE_URL",
        "SONARQUBE_ORG",
        "SONARQUBE_PROJECT_KEY",
        "SONARQUBE_IDE_PORT",
    ):
        val = os.environ.get(key)
        if val:
            env_lines.append(f'  "{key}" = "{val}",')

    if env_lines:
        env_block = "env = {\n" + "\n".join(env_lines) + "\n}"
        if "env = {" in snippet:
            snippet = re.sub(r"env = \{[^}]*\}", env_block, snippet, count=1)
        else:
            snippet = snippet.rstrip() + "\n" + env_block + "\n"

    if cfg.is_file() and "[mcp_servers.sonarqube]" in cfg.read_text(encoding="utf-8"):
        text = cfg.read_text(encoding="utf-8")
        cfg.write_text(_replace_sonarqube_mcp_block(text, snippet), encoding="utf-8")
    else:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        if cfg.is_file():
            cfg.write_text(cfg.read_text(encoding="utf-8").rstrip() + "\n\n" + snippet, encoding="utf-8")
        else:
            cfg.write_text(snippet, encoding="utf-8")
    return cfg


if __name__ == "__main__":
    path = merge_codex_config()
    print(f"Updated {path}")
