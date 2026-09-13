"""Clang Static Analyzer (scan-build / analyze-build) adapter."""

from __future__ import annotations

import json
import plistlib
from pathlib import Path
from typing import Any, Mapping

from scanners._util import relativize, require_binary, run_command
from scanners.base import Finding


def parse_clang_analyzer_plist(data: Mapping[str, Any], *, workspace: Path | None = None) -> list[Finding]:
    """Parse a single clang analyzer plist diagnostic file."""
    findings: list[Finding] = []
    files = list(data.get("files") or [])
    for diag in data.get("diagnostics") or []:
        if not isinstance(diag, dict):
            continue
        desc = str(diag.get("description") or diag.get("check_name") or "clang-analyzer")
        check = str(diag.get("check_name") or "diagnostic")
        loc = diag.get("location") or {}
        file_idx = loc.get("file")
        file_path = "(unknown)"
        if isinstance(file_idx, int) and 0 <= file_idx < len(files):
            file_path = relativize(str(files[file_idx]), workspace)
        elif isinstance(file_idx, str):
            file_path = relativize(file_idx, workspace)
        line = loc.get("line") or "-"
        findings.append(
            Finding(
                source="clang-analyzer",
                severity="MAJOR",
                type="BUG",
                rule=f"clang-analyzer:{check}",
                message=desc,
                file=file_path,
                line=line,
                status="OPEN",
            )
        )
    return findings


def parse_clang_analyzer_sarif(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse clang analyzer SARIF (optional alternate output)."""
    if not text.strip():
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    findings: list[Finding] = []
    for run in data.get("runs") or []:
        for result in run.get("results") or []:
            rule_id = str(result.get("ruleId") or "diagnostic")
            msg = result.get("message") or {}
            text_msg = str(msg.get("text") or rule_id)
            file_path = "(unknown)"
            line: Any = "-"
            for loc in result.get("locations") or []:
                phys = (loc.get("physicalLocation") or {})
                art = phys.get("artifactLocation") or {}
                region = phys.get("region") or {}
                if art.get("uri"):
                    file_path = relativize(str(art["uri"]).replace("file://", ""), workspace)
                if region.get("startLine"):
                    line = region["startLine"]
                break
            findings.append(
                Finding(
                    source="clang-analyzer",
                    severity="MAJOR",
                    type="BUG",
                    rule=f"clang-analyzer:{rule_id}",
                    message=text_msg,
                    file=file_path,
                    line=line,
                    status="OPEN",
                )
            )
    return findings


def _load_plist_reports(report_dir: Path, workspace: Path) -> list[Finding]:
    findings: list[Finding] = []
    for plist_path in report_dir.rglob("*.plist"):
        try:
            with plist_path.open("rb") as fh:
                data = plistlib.load(fh)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            findings.extend(parse_clang_analyzer_plist(data, workspace=workspace))
    return findings


class ClangAnalyzerScanner:
    name = "clang-analyzer"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        workspace = workspace.resolve()
        # Prefer reading precomputed report_dir; otherwise run analyze-build/scan-build.
        report_dir = config.get("report_dir")
        if report_dir:
            path = Path(str(report_dir))
            if not path.is_absolute():
                path = workspace / path
            return _load_plist_reports(path, workspace)

        sarif_path = config.get("sarif")
        if sarif_path:
            path = Path(str(sarif_path))
            if not path.is_absolute():
                path = workspace / path
            return parse_clang_analyzer_sarif(
                path.read_text(encoding="utf-8", errors="replace"),
                workspace=workspace,
            )

        build_command = config.get("build_command") or config.get("command")
        if not build_command:
            raise RuntimeError(
                "clang-analyzer requires report_dir, sarif, or build_command "
                "(e.g. ['make', '-C', 'build'])"
            )
        binary = require_binary("scan-build", config=config)
        if isinstance(build_command, str):
            build_argv = ["sh", "-c", build_command]
        else:
            build_argv = [str(x) for x in build_command]
        import tempfile

        with tempfile.TemporaryDirectory(prefix="easyscan-clang-analyzer-") as tmp:
            out_dir = Path(tmp) / "reports"
            out_dir.mkdir(parents=True, exist_ok=True)
            cmd = [binary, "-o", str(out_dir), "--keep-empty", *build_argv]
            timeout = int(config.get("timeout_sec") or 1200)
            run_command(cmd, cwd=workspace, timeout_sec=timeout)
            return _load_plist_reports(out_dir, workspace)
