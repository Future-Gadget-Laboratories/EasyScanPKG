"""cppcheck C/C++ static analysis adapter."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping

from scanners._util import relativize, require_binary, resolve_paths, run_command
from scanners.base import Finding

_SEV = {
    "error": "CRITICAL",
    "warning": "MAJOR",
    "style": "MINOR",
    "performance": "MAJOR",
    "portability": "MAJOR",
    "information": "INFO",
}


def parse_cppcheck_xml(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse cppcheck XML (version 2) into Findings."""
    findings: list[Finding] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return findings
    for err in root.iter("error"):
        err_id = err.get("id") or "unknown"
        severity = _SEV.get((err.get("severity") or "warning").lower(), "MAJOR")
        msg = err.get("msg") or err.get("verbose") or err_id
        file_path = "(unknown)"
        line: Any = "-"
        loc = err.find("location")
        if loc is not None:
            file_path = relativize(loc.get("file") or file_path, workspace)
            line_raw = loc.get("line")
            if line_raw and str(line_raw).isdigit():
                line = int(line_raw)
        findings.append(
            Finding(
                source="cppcheck",
                severity=severity,
                type="BUG" if severity in {"CRITICAL", "MAJOR"} else "CODE_SMELL",
                rule=f"cppcheck:{err_id}",
                message=msg,
                file=file_path,
                line=line,
                status="OPEN",
            )
        )
    return findings


class CppcheckScanner:
    name = "cppcheck"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        workspace = workspace.resolve()
        binary = require_binary("cppcheck", config=config)
        paths = resolve_paths(workspace, config, default=["."])
        cmd = [
            binary,
            "--enable=all",
            "--xml",
            "--xml-version=2",
            "--inline-suppr",
        ]
        std = config.get("std")
        if std:
            cmd.append(f"--std={std}")
        suppressions = config.get("suppressions")
        if suppressions:
            cmd.append(f"--suppressions-list={suppressions}")
        cmd.extend(paths)
        timeout = int(config.get("timeout_sec") or 600)
        # cppcheck writes XML to stderr by convention
        proc = run_command(cmd, cwd=workspace, timeout_sec=timeout)
        xml_text = proc.stderr or proc.stdout or ""
        return parse_cppcheck_xml(xml_text, workspace=workspace)
