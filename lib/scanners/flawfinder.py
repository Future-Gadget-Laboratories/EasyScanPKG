"""Flawfinder C/C++ insecure-API heuristic adapter."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Mapping

from scanners._util import relativize, require_binary, resolve_paths, run_command
from scanners.base import Finding


def parse_flawfinder_csv(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse `flawfinder --csv` output."""
    if not text.strip():
        return []
    reader = csv.DictReader(io.StringIO(text))
    findings: list[Finding] = []
    for row in reader:
        if not row:
            continue
        level_raw = row.get("Level") or row.get("level") or "1"
        try:
            level = int(float(level_raw))
        except ValueError:
            level = 1
        if level >= 4:
            severity = "CRITICAL"
        elif level == 3:
            severity = "MAJOR"
        else:
            severity = "MINOR"
        name = row.get("Name") or row.get("name") or "hit"
        cwe = row.get("CWEs") or row.get("CWE") or ""
        rule = f"flawfinder:{cwe}" if cwe else f"flawfinder:{name}"
        findings.append(
            Finding(
                source="flawfinder",
                severity=severity,
                type="VULNERABILITY",
                rule=rule,
                message=str(row.get("Warning") or row.get("warning") or name),
                file=relativize(str(row.get("File") or row.get("file") or "(unknown)"), workspace),
                line=row.get("Line") or row.get("line") or "-",
                status="OPEN",
            )
        )
    return findings


class FlawfinderScanner:
    name = "flawfinder"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        workspace = workspace.resolve()
        binary = require_binary("flawfinder", config=config)
        paths = resolve_paths(workspace, config, default=["."])
        minlevel = str(config.get("minlevel") or "1")
        cmd = [binary, "--csv", f"--minlevel={minlevel}", *paths]
        timeout = int(config.get("timeout_sec") or 300)
        proc = run_command(cmd, cwd=workspace, timeout_sec=timeout)
        return parse_flawfinder_csv(proc.stdout or "", workspace=workspace)
