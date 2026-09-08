"""Shared helpers for host-tool scanner adapters."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence


def require_binary(name: str, *, config_key: str = "binary", config: Mapping[str, Any] | None = None) -> str:
    """Resolve and require an external scanner binary."""
    cfg = config or {}
    binary = str(cfg.get(config_key) or name)
    if not shutil.which(binary) and not Path(binary).is_file():
        raise RuntimeError(
            f"{name} binary not found ({binary}). Install it or disable the scanner."
        )
    return binary


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
    import os

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


def relativize(path: str, workspace: Path | None) -> str:
    if not workspace:
        return path
    try:
        return str(Path(path).resolve().relative_to(workspace.resolve()))
    except ValueError:
        return path
