"""AddressSanitizer / UndefinedBehaviorSanitizer run adapters."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from scanners._util import relativize, run_command
from scanners.base import Finding

_ASAN_HEADER = re.compile(
    r"ERROR:\s*AddressSanitizer:\s*(?P<title>.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_UBSAN_HEADER = re.compile(
    r"(?P<file>[^:\n]+):(?P<line>\d+):\d+:\s*runtime error:\s*(?P<title>.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_FRAME = re.compile(
    r"^\s*#\d+\s+0x[0-9a-fA-F]+\s+.*?(?P<file>/[^:\s]+):(?P<line>\d+)",
    re.MULTILINE,
)


def _parse_command(config: Mapping[str, Any]) -> list[str]:
    command = config.get("command")
    if not command:
        raise RuntimeError(
            "asan/ubsan requires config command "
            "(instrumented binary argv; build with -fsanitize=address/undefined first)"
        )
    if isinstance(command, str):
        return [command]
    if isinstance(command, Sequence):
        return [str(x) for x in command]
    raise RuntimeError("asan/ubsan command must be a string or list")


def parse_asan_output(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse AddressSanitizer stderr reports."""
    findings: list[Finding] = []
    for match in _ASAN_HEADER.finditer(text):
        title = match.group("title").strip()
        # Search for first file:line frame after this header
        rest = text[match.end() : match.end() + 2000]
        file_path = "(unknown)"
        line: Any = "-"
        frame = _FRAME.search(rest)
        if frame:
            file_path = relativize(frame.group("file"), workspace)
            line = int(frame.group("line"))
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "error"
        findings.append(
            Finding(
                source="asan",
                severity="CRITICAL",
                type="BUG",
                rule=f"asan:{slug}",
                message=title,
                file=file_path,
                line=line,
                status="OPEN",
            )
        )
    return findings


def parse_ubsan_output(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse UndefinedBehaviorSanitizer stderr reports."""
    findings: list[Finding] = []
    for match in _UBSAN_HEADER.finditer(text):
        title = match.group("title").strip()
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "runtime-error"
        findings.append(
            Finding(
                source="ubsan",
                severity="MAJOR",
                type="BUG",
                rule=f"ubsan:{slug}",
                message=title,
                file=relativize(match.group("file"), workspace),
                line=int(match.group("line")),
                status="OPEN",
            )
        )
    return findings


def _run_sanitizer(
    *,
    source: str,
    workspace: Path,
    config: Mapping[str, Any],
    env: dict[str, str],
    parser,
) -> list[Finding]:
    workspace = workspace.resolve()
    target = _parse_command(config)
    args = [str(a) for a in (config.get("args") or [])]
    timeout = int(config.get("timeout_sec") or 600)
    cwd = config.get("cwd")
    work_dir = Path(str(cwd)) if cwd else workspace
    if not work_dir.is_absolute():
        work_dir = workspace / work_dir
    proc = run_command(
        [*target, *args],
        cwd=work_dir,
        timeout_sec=timeout,
        env=env,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    findings = parser(combined, workspace=workspace)
    if not findings and proc.returncode not in (0, None):
        findings.append(
            Finding(
                source=source,
                severity="MAJOR",
                type="BUG",
                rule=f"{source}:run-failed",
                message=f"{source} target exited {proc.returncode} without parsed errors",
                file="(unknown)",
                line="-",
                status="OPEN",
            )
        )
    return findings


class ASanScanner:
    name = "asan"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        env = {
            "ASAN_OPTIONS": str(
                config.get("asan_options")
                or "halt_on_error=0:detect_leaks=1:abort_on_error=0"
            )
        }
        return _run_sanitizer(
            source="asan",
            workspace=workspace,
            config=config,
            env=env,
            parser=parse_asan_output,
        )


class UBSanScanner:
    name = "ubsan"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        env = {
            "UBSAN_OPTIONS": str(
                config.get("ubsan_options") or "print_stacktrace=1:halt_on_error=0"
            )
        }
        return _run_sanitizer(
            source="ubsan",
            workspace=workspace,
            config=config,
            env=env,
            parser=parse_ubsan_output,
        )
