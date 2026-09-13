"""ShellCheck shell script lint adapter."""

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


def parse_shellcheck_json(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse `shellcheck -f json` output."""
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
        code = row.get("code")
        level = (row.get("level") or "warning").lower()
        findings.append(
            Finding(
                source="shellcheck",
                severity=_SEV.get(level, "MAJOR"),
                type="CODE_SMELL",
                rule=f"shellcheck:SC{code}" if code is not None else "shellcheck:unknown",
                message=str(row.get("message") or ""),
                file=relativize(str(row.get("file") or "(unknown)"), workspace),
                line=row.get("line") or "-",
                status="OPEN",
            )
        )
    return findings


def _discover_shell_scripts(workspace: Path, config: Mapping[str, Any]) -> list[Path]:
    raw = list(config.get("paths") or [])
    if raw:
        out: list[Path] = []
        for item in raw:
            path = Path(str(item))
            if not path.is_absolute():
                path = workspace / path
            if path.is_file():
                out.append(path)
            elif path.is_dir():
                out.extend(sorted(path.rglob("*.sh")))
        return out
    # Default: bin/ and hooks/ shell scripts + top-level *.sh
    candidates: list[Path] = []
    for folder in ("bin", "hooks", "scripts"):
        root = workspace / folder
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file() and (
                    path.suffix == ".sh"
                    or path.name in {"easyscan-check", "sonar-scan", "sonar-local-up", "sonar-local-down"}
                ):
                    # Only treat as shell if shebang looks like shell or .sh suffix
                    if path.suffix == ".sh":
                        candidates.append(path)
                    else:
                        try:
                            head = path.read_text(encoding="utf-8", errors="ignore")[:40]
                        except OSError:
                            continue
                        if head.startswith("#!") and ("bash" in head or "sh" in head):
                            candidates.append(path)
    candidates.extend(sorted(workspace.glob("*.sh")))
    # Deduplicate
    seen: set[Path] = set()
    uniq: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        uniq.append(path)
    return uniq


class ShellCheckScanner:
    name = "shellcheck"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        workspace = workspace.resolve()
        binary = require_binary("shellcheck", config=config)
        scripts = _discover_shell_scripts(workspace, config)
        if not scripts:
            return []
        cmd = [binary, "-f", "json", *[str(p) for p in scripts]]
        timeout = int(config.get("timeout_sec") or 300)
        proc = run_command(cmd, cwd=workspace, timeout_sec=timeout)
        return parse_shellcheck_json(proc.stdout or "", workspace=workspace)
