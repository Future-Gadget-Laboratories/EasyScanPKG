"""clang-tidy static analysis adapter."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

from scanners.base import Finding

# /path/file.cpp:12:5: warning: message [check-name]
_DIAG_RE = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+):\d+:\s*"
    r"(?P<level>error|warning|note):\s*"
    r"(?P<message>.*?)\s*"
    r"\[(?P<check>[^\]]+)\]\s*$"
)

_LEVEL_TO_SEV = {
    "error": "CRITICAL",
    "warning": "MAJOR",
    "note": "INFO",
}

_LEVEL_TO_TYPE = {
    "error": "BUG",
    "warning": "CODE_SMELL",
    "note": "CODE_SMELL",
}

_CXX_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".m", ".mm"}


def parse_clang_tidy_output(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse clang-tidy text diagnostics into Findings."""
    findings: list[Finding] = []
    ws = workspace.resolve() if workspace else None
    for raw_line in text.splitlines():
        match = _DIAG_RE.match(raw_line.strip())
        if not match:
            continue
        level = match.group("level")
        if level == "note":
            continue
        path = match.group("file")
        if ws is not None:
            try:
                path = str(Path(path).resolve().relative_to(ws))
            except ValueError:
                path = str(Path(path))
        check = match.group("check")
        findings.append(
            Finding(
                source="clang-tidy",
                severity=_LEVEL_TO_SEV.get(level, "MAJOR"),
                type=_LEVEL_TO_TYPE.get(level, "CODE_SMELL"),
                rule=f"clang-tidy:{check}",
                message=match.group("message").strip(),
                file=path,
                line=int(match.group("line")),
                status="OPEN",
            )
        )
    return findings


def _require_binary(config: Mapping[str, Any]) -> str:
    binary = str(config.get("binary") or "clang-tidy")
    if not shutil.which(binary) and not Path(binary).is_file():
        raise RuntimeError(
            f"clang-tidy binary not found ({binary}). "
            "Install LLVM clang-tidy or disable the scanner."
        )
    return binary


def _resolve_compile_commands(workspace: Path, config: Mapping[str, Any]) -> Path:
    compile_commands = config.get("compile_commands")
    if not compile_commands:
        raise RuntimeError(
            "clang-tidy requires compile_commands (path to compile_commands.json)"
        )
    cc_path = Path(str(compile_commands))
    if not cc_path.is_absolute():
        cc_path = workspace / cc_path
    if not cc_path.is_file():
        raise RuntimeError(f"compile_commands.json not found: {cc_path}")
    return cc_path


def _source_paths(cc_path: Path, workspace: Path, config: Mapping[str, Any]) -> list[str]:
    paths = list(config.get("paths") or [])
    if paths:
        return paths
    discovered = _paths_from_compile_commands(cc_path, workspace)
    if not discovered:
        raise RuntimeError("clang-tidy: no source paths to analyze")
    return discovered


def _build_clang_tidy_cmd(
    binary: str,
    cc_path: Path,
    config: Mapping[str, Any],
    workspace: Path,
    paths: list[str],
) -> list[str]:
    cmd = [binary, f"-p={cc_path.parent}"]
    checks = config.get("checks")
    if checks:
        cmd.append(f"-checks={checks}")
    config_file = config.get("config_file")
    if config_file:
        cfg = Path(str(config_file))
        if not cfg.is_absolute():
            cfg = workspace / cfg
        cmd.append(f"--config-file={cfg}")
    header_filter = config.get("header_filter")
    if header_filter:
        cmd.append(f"-header-filter={header_filter}")
    cmd.extend(str(p) for p in paths)
    return cmd


class ClangTidyScanner:
    name = "clang-tidy"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        workspace = workspace.resolve()
        binary = _require_binary(config)
        cc_path = _resolve_compile_commands(workspace, config)
        paths = _source_paths(cc_path, workspace, config)
        cmd = _build_clang_tidy_cmd(binary, cc_path, config, workspace, paths)
        timeout = int(config.get("timeout_sec") or 600)
        proc = subprocess.run(
            cmd,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return parse_clang_tidy_output(combined, workspace=workspace)


def _paths_from_compile_commands(cc_path: Path, workspace: Path) -> list[str]:
    try:
        entries = json.loads(cc_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        file_path = entry.get("file")
        if not file_path:
            continue
        path = Path(str(file_path))
        if not path.is_absolute():
            directory = entry.get("directory") or cc_path.parent
            path = Path(str(directory)) / path
        try:
            rel = path.resolve().relative_to(workspace)
        except ValueError:
            continue
        rel_s = str(rel)
        if rel_s in seen or path.suffix.lower() not in _CXX_SUFFIXES:
            continue
        seen.add(rel_s)
        out.append(rel_s)
    return out
