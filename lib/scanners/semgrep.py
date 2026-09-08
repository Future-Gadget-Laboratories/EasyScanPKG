"""Semgrep polyglot SAST adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scanners._util import relativize, require_binary, run_command
from scanners.base import Finding

_SEV = {
    "error": "CRITICAL",
    "warning": "MAJOR",
    "info": "MINOR",
    "inventory": "INFO",
    "critical": "CRITICAL",
    "high": "CRITICAL",
    "medium": "MAJOR",
    "low": "MINOR",
}


def parse_semgrep_json(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse `semgrep --json` output."""
    if not text.strip():
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    results = data.get("results") if isinstance(data, dict) else data
    if not isinstance(results, list):
        return []
    findings: list[Finding] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        check_id = str(row.get("check_id") or "unknown")
        extra = row.get("extra") or {}
        severity_raw = str(
            extra.get("severity")
            or (extra.get("metadata") or {}).get("severity")
            or "WARNING"
        ).lower()
        start = row.get("start") or {}
        findings.append(
            Finding(
                source="semgrep",
                severity=_SEV.get(severity_raw, "MAJOR"),
                type="VULNERABILITY" if "security" in check_id.lower() else "CODE_SMELL",
                rule=f"semgrep:{check_id}",
                message=str(extra.get("message") or check_id),
                file=relativize(str(row.get("path") or "(unknown)"), workspace),
                line=start.get("line") or "-",
                status="OPEN",
            )
        )
    return findings


class SemgrepScanner:
    name = "semgrep"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        workspace = workspace.resolve()
        binary = require_binary("semgrep", config=config)
        rules = str(config.get("config") or config.get("rules") or "p/default")
        cmd = [binary, "scan", "--json", "--quiet", "--config", rules]
        exclude = config.get("exclude")
        if isinstance(exclude, (list, tuple)):
            for item in exclude:
                cmd.extend(["--exclude", str(item)])
        elif exclude:
            cmd.extend(["--exclude", str(exclude)])
        cmd.append(str(workspace))
        timeout = int(config.get("timeout_sec") or 900)
        proc = run_command(cmd, cwd=workspace, timeout_sec=timeout)
        return parse_semgrep_json(proc.stdout or "", workspace=workspace)
