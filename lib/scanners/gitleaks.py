"""Gitleaks secrets detection adapter."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from scanners._util import relativize, require_binary, run_command
from scanners.base import Finding


def parse_gitleaks_json(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse `gitleaks detect --report-format json` output."""
    if not text.strip():
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    rows = data if isinstance(data, list) else data.get("findings") or data.get("leaks") or []
    findings: list[Finding] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        rule_id = str(row.get("RuleID") or row.get("rule") or "secret")
        file_path = str(row.get("File") or row.get("file") or "(unknown)")
        line = row.get("StartLine") or row.get("line") or "-"
        desc = str(row.get("Description") or row.get("description") or rule_id)
        findings.append(
            Finding(
                source="gitleaks",
                severity="CRITICAL",
                type="VULNERABILITY",
                rule=f"gitleaks:{rule_id}",
                message=desc,
                file=relativize(file_path, workspace),
                line=line,
                status="OPEN",
            )
        )
    return findings


class GitleaksScanner:
    name = "gitleaks"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        workspace = workspace.resolve()
        binary = require_binary("gitleaks", config=config)
        # Prefer filesystem scan for checklist usefulness (line numbers in working tree).
        no_git = bool(config.get("no_git", True))
        timeout = int(config.get("timeout_sec") or 600)
        with tempfile.TemporaryDirectory(prefix="easyscan-gitleaks-") as tmp:
            report = Path(tmp) / "gitleaks.json"
            cmd = [
                binary,
                "detect",
                "--report-format",
                "json",
                "--report-path",
                str(report),
                "--source",
                str(workspace),
            ]
            if no_git:
                cmd.append("--no-git")
            run_command(cmd, cwd=workspace, timeout_sec=timeout)
            text = ""
            if report.is_file():
                text = report.read_text(encoding="utf-8", errors="replace")
            return parse_gitleaks_json(text, workspace=workspace)
