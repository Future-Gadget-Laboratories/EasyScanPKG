"""Ruff Python lint / readability adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scanners._util import relativize, require_binary, resolve_paths, run_command
from scanners.base import Finding

# Ruff uses letter prefixes; treat security-ish codes as MAJOR, else MINOR/MAJOR by default.
_MAJOR_PREFIXES = ("S", "B", "BLE", "T", "PGH")


def _severity_for_code(code: str) -> str:
    upper = (code or "").upper()
    for prefix in _MAJOR_PREFIXES:
        if upper.startswith(prefix):
            return "MAJOR"
    return "MINOR"


def parse_ruff_json(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse `ruff check --output-format=json` output."""
    if not text.strip():
        return []
    try:
        rows = json.loads(text)
    except json.JSONDecodeError:
        return []
    findings: list[Finding] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "unknown")
        path = relativize(str(row.get("filename") or "(unknown)"), workspace)
        loc = row.get("location") or {}
        line = loc.get("row") or row.get("line") or "-"
        findings.append(
            Finding(
                source="ruff",
                severity=_severity_for_code(code),
                type="CODE_SMELL",
                rule=f"ruff:{code}",
                message=str(row.get("message") or code),
                file=path,
                line=line,
                status="OPEN",
            )
        )
    return findings


class RuffScanner:
    name = "ruff"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        workspace = workspace.resolve()
        binary = require_binary("ruff", config=config)
        paths = resolve_paths(workspace, config, default=["lib", "bin", "hooks"])
        # Keep only paths that exist to avoid ruff hard-failing on missing dirs.
        existing = [p for p in paths if Path(p).exists()]
        if not existing:
            existing = [str(workspace)]
        cmd = [binary, "check", "--output-format=json", *existing]
        select = config.get("select")
        if select:
            cmd.extend(["--select", str(select)])
        ignore = config.get("ignore")
        if ignore:
            cmd.extend(["--ignore", str(ignore)])
        cfg = config.get("config")
        if cfg:
            cmd.extend(["--config", str(cfg)])
        timeout = int(config.get("timeout_sec") or 300)
        proc = run_command(cmd, cwd=workspace, timeout_sec=timeout)
        return parse_ruff_json(proc.stdout or "", workspace=workspace)
