"""Bandit Python security adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scanners._util import relativize, require_binary, resolve_paths, run_command
from scanners.base import Finding

_SEV = {
    "HIGH": "CRITICAL",
    "MEDIUM": "MAJOR",
    "LOW": "MINOR",
    "UNDEFINED": "INFO",
}


def parse_bandit_json(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse `bandit -f json` output."""
    if not text.strip():
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    results = data.get("results") if isinstance(data, dict) else []
    findings: list[Finding] = []
    for row in results if isinstance(results, list) else []:
        if not isinstance(row, dict):
            continue
        test_id = str(row.get("test_id") or "unknown")
        severity = _SEV.get(str(row.get("issue_severity") or "MEDIUM").upper(), "MAJOR")
        findings.append(
            Finding(
                source="bandit",
                severity=severity,
                type="VULNERABILITY",
                rule=f"bandit:{test_id}",
                message=str(row.get("issue_text") or test_id),
                file=relativize(str(row.get("filename") or "(unknown)"), workspace),
                line=row.get("line_number") or "-",
                status="OPEN",
            )
        )
    return findings


class BanditScanner:
    name = "bandit"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        workspace = workspace.resolve()
        binary = require_binary("bandit", config=config)
        paths = resolve_paths(workspace, config, default=["lib", "bin"])
        existing = [p for p in paths if Path(p).exists()]
        if not existing:
            existing = [str(workspace)]
        cmd = [binary, "-r", *existing, "-f", "json", "-q"]
        skips = config.get("skips") or config.get("skip")
        if skips:
            cmd.extend(["-s", str(skips) if not isinstance(skips, list) else ",".join(skips)])
        timeout = int(config.get("timeout_sec") or 300)
        proc = run_command(cmd, cwd=workspace, timeout_sec=timeout)
        # Bandit may write JSON to stdout even on findings (exit 1)
        return parse_bandit_json(proc.stdout or "", workspace=workspace)
