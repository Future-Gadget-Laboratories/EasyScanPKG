"""Valgrind Memcheck dynamic analysis adapter."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence
import tempfile

from scanners._util import relativize, require_binary, run_command
from scanners.base import Finding


def _parse_command(config: Mapping[str, Any]) -> list[str]:
    command = config.get("command")
    if not command:
        raise RuntimeError(
            "valgrind requires config command (argv of the target to run under Memcheck)"
        )
    if isinstance(command, str):
        return [command]
    if isinstance(command, Sequence):
        return [str(x) for x in command]
    raise RuntimeError("valgrind command must be a string or list")


def parse_valgrind_xml(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse Valgrind Memcheck XML output."""
    if not text.strip():
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    findings: list[Finding] = []
    for err in root.iter("error"):
        kind = (err.findtext("kind") or "Error").strip()
        what = (err.findtext("xwhat/text") or err.findtext("what") or kind).strip()
        file_path = "(unknown)"
        line: Any = "-"
        for frame in err.iter("frame"):
            fpath = frame.findtext("file")
            fline = frame.findtext("line")
            if fpath:
                file_path = relativize(fpath, workspace)
                if fline and fline.isdigit():
                    line = int(fline)
                break
        slug = kind.lower().replace(" ", "-")
        findings.append(
            Finding(
                source="valgrind",
                severity="CRITICAL" if "Invalid" in kind or "uninitialized" in kind.lower() else "MAJOR",
                type="BUG",
                rule=f"valgrind:{slug}",
                message=what,
                file=file_path,
                line=line,
                status="OPEN",
            )
        )
    return findings


class ValgrindScanner:
    name = "valgrind"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        workspace = workspace.resolve()
        binary = require_binary("valgrind", config=config)
        target = _parse_command(config)
        args = [str(a) for a in (config.get("args") or [])]
        timeout = int(config.get("timeout_sec") or 600)
        cwd = config.get("cwd")
        work_dir = Path(str(cwd)) if cwd else workspace
        if not work_dir.is_absolute():
            work_dir = workspace / work_dir
        with tempfile.TemporaryDirectory(prefix="easyscan-valgrind-") as tmp:
            xml_path = Path(tmp) / "valgrind.xml"
            cmd = [
                binary,
                "--tool=memcheck",
                "--xml=yes",
                f"--xml-file={xml_path}",
                "--",
                *target,
                *args,
            ]
            proc = run_command(cmd, cwd=work_dir, timeout_sec=timeout)
            text = ""
            if xml_path.is_file():
                text = xml_path.read_text(encoding="utf-8", errors="replace")
            findings = parse_valgrind_xml(text, workspace=workspace)
            if not findings and proc.returncode not in (0, None):
                findings.append(
                    Finding(
                        source="valgrind",
                        severity="MAJOR",
                        type="BUG",
                        rule="valgrind:run-failed",
                        message=f"valgrind exited {proc.returncode} without parsed errors",
                        file="(unknown)",
                        line="-",
                        status="OPEN",
                    )
                )
            return findings
