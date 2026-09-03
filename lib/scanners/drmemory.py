"""Dr. Memory dynamic analysis adapter."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from scanners.base import Finding

UNKNOWN_FILE = "(unknown)"

_TITLE_TO_RULE = {
    "UNADDRESSABLE ACCESS": "drmemory:unaddressable-access",
    "UNINITIALIZED READ": "drmemory:uninitialized-read",
    "INVALID HEAP ARGUMENT": "drmemory:invalid-heap-argument",
    "LEAK": "drmemory:leak",
    "POSSIBLE LEAK": "drmemory:possible-leak",
    "HANDLE LEAK": "drmemory:handle-leak",
}


def _rule_for_title(title: str) -> str:
    upper = title.upper()
    for needle, rule in _TITLE_TO_RULE.items():
        if needle in upper:
            return rule
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "finding"
    return f"drmemory:{slug}"


def _parse_error_title(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.lower().startswith("error"):
        return None
    colon = stripped.find(":")
    if colon < 0:
        return None
    return stripped[colon + 1 :].strip()


def _parse_frame_line(line: str) -> tuple[str, int] | None:
    if "[" not in line or "]" not in line:
        return None
    inner = line[line.index("[") + 1 : line.rindex("]")]
    if ":" not in inner:
        return None
    file_part, line_part = inner.rsplit(":", 1)
    if not line_part.isdigit():
        return None
    return file_part, int(line_part)


def _parse_file_line_token(line: str) -> tuple[str, int] | None:
    token = line.strip().split()[-1] if line.strip() else ""
    if ":" not in token or "." not in token.split(":")[0]:
        return None
    file_part, line_part = token.rsplit(":", 1)
    if not line_part.isdigit():
        return None
    return file_part, int(line_part)


def _location_from_block(block: str) -> tuple[str, Any]:
    file_path = UNKNOWN_FILE
    line: Any = "-"
    for raw in block.splitlines()[1:]:
        frame = _parse_frame_line(raw)
        if frame:
            return frame
        fl = _parse_file_line_token(raw)
        if fl and file_path == UNKNOWN_FILE:
            file_path, line = fl
    return file_path, line


def _relativize_path(file_path: str, workspace: Path | None) -> str:
    if workspace is None or file_path == UNKNOWN_FILE:
        return file_path
    try:
        return str(Path(file_path).resolve().relative_to(workspace))
    except ValueError:
        return file_path


def _message_from_block(title: str, block: str) -> str:
    msg_lines = [ln.strip() for ln in block.splitlines()[1:] if ln.strip()]
    if not msg_lines:
        return title
    return f"{title} — {msg_lines[0][:200]}"


def _finding_from_block(block: str, *, workspace: Path | None) -> Finding | None:
    block = block.strip()
    if not block:
        return None
    header = _parse_error_title(block.splitlines()[0])
    if not header:
        return None
    title = header
    file_path, line = _location_from_block(block)
    file_path = _relativize_path(file_path, workspace)
    return Finding(
        source="drmemory",
        severity="CRITICAL" if "UNADDRESSABLE" in title.upper() else "MAJOR",
        type="BUG",
        rule=_rule_for_title(title),
        message=_message_from_block(title, block),
        file=file_path,
        line=line,
        status="OPEN",
    )


def parse_drmemory_output(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse Dr. Memory text/log output into Findings."""
    ws = workspace.resolve() if workspace else None
    findings: list[Finding] = []
    blocks = re.split(
        r"(?=^Error\s+#?\d+\s*:)", text, flags=re.MULTILINE | re.IGNORECASE
    )
    for block in blocks:
        finding = _finding_from_block(block, workspace=ws)
        if finding is not None:
            findings.append(finding)
    return findings


def _parse_target_command(command: Any) -> list[str]:
    if not command:
        raise RuntimeError(
            "drmemory requires config command "
            "(argv[0] of the target to run under Dr. Memory)"
        )
    if isinstance(command, str):
        return [command]
    if isinstance(command, Sequence):
        return [str(x) for x in command]
    raise RuntimeError("drmemory command must be a string or list")


def _resolve_work_dir(workspace: Path, cwd: Any) -> Path:
    if not cwd:
        return workspace
    work_dir = Path(str(cwd))
    if not work_dir.is_absolute():
        work_dir = workspace / work_dir
    return work_dir


def _collect_drmemory_output(
    binary: str,
    *,
    extra_flags: list[str],
    logdir: Path,
    target: list[str],
    args: list[str],
    work_dir: Path,
    timeout: int,
) -> tuple[str, int | None]:
    cmd = [binary, *extra_flags, "-logdir", str(logdir), "--", *target, *args]
    proc = subprocess.run(
        cmd,
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    for results in logdir.rglob("results.txt"):
        try:
            combined += "\n" + results.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return combined, proc.returncode


class DrMemoryScanner:
    name = "drmemory"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        workspace = workspace.resolve()
        binary = str(config.get("binary") or "drmemory")
        if not shutil.which(binary) and not Path(binary).is_file():
            raise RuntimeError(
                f"Dr. Memory binary not found ({binary}). "
                "Install drmemory or disable the scanner."
            )

        target = _parse_target_command(config.get("command"))
        args = [str(a) for a in (config.get("args") or [])]
        extra_flags = [str(f) for f in (config.get("extra_flags") or ["-batch", "-brief"])]
        work_dir = _resolve_work_dir(workspace, config.get("cwd"))
        timeout = int(config.get("timeout_sec") or 600)

        with tempfile.TemporaryDirectory(prefix="easyscan-drmemory-") as tmp:
            logdir = Path(tmp) / "logs"
            logdir.mkdir(parents=True, exist_ok=True)
            combined, rc = _collect_drmemory_output(
                binary,
                extra_flags=extra_flags,
                logdir=logdir,
                target=target,
                args=args,
                work_dir=work_dir,
                timeout=timeout,
            )
            findings = parse_drmemory_output(combined, workspace=workspace)
            if not findings and rc not in (0, None):
                findings.append(
                    Finding(
                        source="drmemory",
                        severity="MAJOR",
                        type="BUG",
                        rule="drmemory:run-failed",
                        message=f"Dr. Memory exited {rc} without parsed errors",
                        file=UNKNOWN_FILE,
                        line="-",
                        status="OPEN",
                    )
                )
            return findings
