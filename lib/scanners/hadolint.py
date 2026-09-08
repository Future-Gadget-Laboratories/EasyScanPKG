"""Hadolint Dockerfile lint adapter."""

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
    "style": "INFO",
}


def parse_hadolint_json(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse `hadolint -f json` output."""
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
        code = str(row.get("code") or "DL0000")
        level = str(row.get("level") or "warning").lower()
        findings.append(
            Finding(
                source="hadolint",
                severity=_SEV.get(level, "MAJOR"),
                type="CODE_SMELL",
                rule=f"hadolint:{code}",
                message=str(row.get("message") or code),
                file=relativize(str(row.get("file") or "Dockerfile"), workspace),
                line=row.get("line") or "-",
                status="OPEN",
            )
        )
    return findings


def _discover_dockerfiles(workspace: Path, config: Mapping[str, Any]) -> list[Path]:
    raw = list(config.get("paths") or [])
    if raw:
        out: list[Path] = []
        for item in raw:
            path = Path(str(item))
            if not path.is_absolute():
                path = workspace / path
            if path.is_file():
                out.append(path)
        return out
    names = (
        "Dockerfile",
        "Dockerfile.sonar-local",
        "dockerfile",
        "Containerfile",
    )
    found: list[Path] = []
    for name in names:
        path = workspace / name
        if path.is_file():
            found.append(path)
    found.extend(sorted(workspace.glob("**/Dockerfile*")))
    # Deduplicate
    seen: set[Path] = set()
    uniq: list[Path] = []
    for path in found:
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        uniq.append(path)
    return uniq


class HadolintScanner:
    name = "hadolint"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        workspace = workspace.resolve()
        binary = require_binary("hadolint", config=config)
        files = _discover_dockerfiles(workspace, config)
        if not files:
            return []
        cmd = [binary, "-f", "json", *[str(p) for p in files]]
        timeout = int(config.get("timeout_sec") or 300)
        proc = run_command(cmd, cwd=workspace, timeout_sec=timeout)
        return parse_hadolint_json(proc.stdout or "", workspace=workspace)
