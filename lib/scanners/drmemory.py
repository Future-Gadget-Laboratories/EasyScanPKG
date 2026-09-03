"""Dr. Memory dynamic analysis adapter."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from scanners.base import Finding

_ERROR_HEADER = re.compile(
    r"^Error\s+#?\d+\s*:\s*(?P<title>.+)$",
    re.IGNORECASE,
)
_FRAME_RE = re.compile(
    r"^#\s*\d+\s+\S+.*?\[(?P<file>[^\]:\n]+):(?P<line>\d+)\]"
)
_FILE_LINE = re.compile(r"(?P<file>[\w./\\-]+\.\w+):(?P<line>\d+)")

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


def parse_drmemory_output(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse Dr. Memory text/log output into Findings."""
    findings: list[Finding] = []
    ws = workspace.resolve() if workspace else None
    blocks = re.split(
        r"(?=^Error\s+#?\d+\s*:)", text, flags=re.MULTILINE | re.IGNORECASE
    )
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        header = _ERROR_HEADER.match(block.splitlines()[0].strip())
        if not header:
            continue
        title = header.group("title").strip()
        file_path = "(unknown)"
        line: Any = "-"
        for raw in block.splitlines()[1:]:
            frame = _FRAME_RE.match(raw.strip())
            if frame:
                file_path = frame.group("file")
                line = int(frame.group("line"))
                break
            fl = _FILE_LINE.search(raw)
            if fl and file_path == "(unknown)":
                file_path = fl.group("file")
                line = int(fl.group("line"))
        if ws is not None and file_path != "(unknown)":
            try:
                file_path = str(Path(file_path).resolve().relative_to(ws))
            except ValueError:
                pass
        msg_lines = [ln.strip() for ln in block.splitlines()[1:] if ln.strip()]
        message = title
        if msg_lines:
            message = f"{title} — {msg_lines[0][:200]}"
        findings.append(
            Finding(
                source="drmemory",
                severity="CRITICAL" if "UNADDRESSABLE" in title.upper() else "MAJOR",
                type="BUG",
                rule=_rule_for_title(title),
                message=message,
                file=file_path,
                line=line,
                status="OPEN",
            )
        )
    return findings


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

        command = config.get("command")
        if not command:
            raise RuntimeError(
                "drmemory requires config command "
                "(argv[0] of the target to run under Dr. Memory)"
            )
        if isinstance(command, str):
            target: list[str] = [command]
        elif isinstance(command, Sequence):
            target = [str(x) for x in command]
        else:
            raise RuntimeError("drmemory command must be a string or list")

        args = [str(a) for a in (config.get("args") or [])]
        extra_flags = [str(f) for f in (config.get("extra_flags") or ["-batch", "-brief"])]
        cwd = config.get("cwd")
        work_dir = Path(str(cwd)) if cwd else workspace
        if not work_dir.is_absolute():
            work_dir = workspace / work_dir

        timeout = int(config.get("timeout_sec") or 600)
        with tempfile.TemporaryDirectory(prefix="easyscan-drmemory-") as tmp:
            logdir = Path(tmp) / "logs"
            logdir.mkdir(parents=True, exist_ok=True)
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
            findings = parse_drmemory_output(combined, workspace=workspace)
            if not findings and proc.returncode not in (0, None):
                findings.append(
                    Finding(
                        source="drmemory",
                        severity="MAJOR",
                        type="BUG",
                        rule="drmemory:run-failed",
                        message=f"Dr. Memory exited {proc.returncode} without parsed errors",
                        file="(unknown)",
                        line="-",
                        status="OPEN",
                    )
                )
            return findings
