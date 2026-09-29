"""Ruff Python lint / readability adapter."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from scanners._util import ensure_exit, relativize, require_binary, resolve_paths, run_command
from scanners.base import Finding

# Ruff codes are <family letters><digits>. Security (S), bugbear (B), blind except
# (BLE), debugger/print (T), and pygrep hooks (PGH) are MAJOR; everything else —
# including look-alike families such as SIM (simplify) — is MINOR.
_MAJOR_FAMILIES = frozenset({"S", "B", "BLE", "T", "PGH"})
_FAMILY_RE = re.compile(r"^([A-Z]+)")


def _severity_for_code(code: str) -> str:
    match = _FAMILY_RE.match((code or "").upper())
    return "MAJOR" if match and match.group(1) in _MAJOR_FAMILIES else "MINOR"


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
        paths = resolve_paths(workspace, config, default=["."])
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
        proc = ensure_exit(run_command(cmd, cwd=workspace, timeout_sec=timeout), "ruff")
        return parse_ruff_json(proc.stdout or "", workspace=workspace)
