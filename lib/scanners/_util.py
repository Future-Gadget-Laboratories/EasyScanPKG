"""Shared helpers for host-tool scanner adapters."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence


def tools_home() -> Path:
    """Root for scanner tools installed by easyscan-install-scanners."""
    raw = os.environ.get("EASYSCAN_TOOLS_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".config" / "sft" / "scanners"


def tools_bin_dir() -> Path:
    return tools_home() / "bin"


def find_binary(binary: str) -> str | None:
    """Locate a tool on PATH, as an explicit path, or in the EasyScan tools bin dir."""
    found = shutil.which(binary)
    if found:
        return found
    path = Path(binary).expanduser()
    if path.is_absolute() or os.sep in binary:
        return str(path) if path.is_file() else None
    candidate = tools_bin_dir() / binary
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def require_binary(name: str, *, config_key: str = "binary", config: Mapping[str, Any] | None = None) -> str:
    """Resolve and require an external scanner binary."""
    cfg = config or {}
    binary = str(cfg.get(config_key) or name)
    found = find_binary(binary)
    if not found:
        raise RuntimeError(
            f"{name} binary not found ({binary}). Run bin/easyscan-install-scanners "
            "or disable the scanner."
        )
    return found


def resolve_paths(
    workspace: Path,
    config: Mapping[str, Any],
    *,
    default: Sequence[str] | None = None,
) -> list[str]:
    """Return scan target paths relative to workspace (or absolute if outside)."""
    raw = list(config.get("paths") or default or ["."])
    out: list[str] = []
    for item in raw:
        path = Path(str(item))
        if not path.is_absolute():
            path = workspace / path
        out.append(str(path))
    return out


def run_command(
    cmd: Sequence[str],
    *,
    cwd: Path,
    timeout_sec: int = 600,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and capture text output (does not raise on non-zero)."""
    full_env = os.environ.copy()
    if env:
        full_env.update({str(k): str(v) for k, v in env.items()})
    return subprocess.run(
        list(cmd),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
        env=full_env,
    )


def ensure_exit(
    proc: subprocess.CompletedProcess[str],
    tool: str,
    ok_codes: Sequence[int] = (0, 1),
) -> subprocess.CompletedProcess[str]:
    """Raise when a tool crashed, so a failed run is never reported as a clean scan.

    Most linters exit 1 for "findings present"; anything outside ``ok_codes`` is an
    error (bad config, network failure, crash).
    """
    if proc.returncode in ok_codes:
        return proc
    tail = [line for line in (proc.stderr or proc.stdout or "").strip().splitlines() if line.strip()]
    detail = tail[-1][:300] if tail else "no output"
    raise RuntimeError(f"{tool} exited {proc.returncode}: {detail}")


def relativize(path: str, workspace: Path | None) -> str:
    if not workspace:
        return path
    try:
        return str(Path(path).resolve().relative_to(workspace.resolve()))
    except ValueError:
        return path
