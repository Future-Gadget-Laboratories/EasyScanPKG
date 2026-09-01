"""Docker invocation with group-session fallback."""

from __future__ import annotations

import grp
import os
import shutil
import subprocess


def _in_docker_group() -> bool:
    try:
        docker_gid = grp.getgrnam("docker").gr_gid
    except KeyError:
        return False
    return docker_gid in os.getgroups()


def docker_available() -> bool:
    return shutil.which("docker") is not None


def docker_ok() -> tuple[bool, str]:
    """Return (can_run_docker, detail)."""
    if not docker_available():
        return False, "docker not found in PATH"
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True, "ok"
    if _in_docker_group():
        return False, (
            "permission denied on docker.sock — you are in group 'docker' but this "
            "session has not picked it up yet. Run: newgrp docker   (or log out/in), "
            "then re-run install."
        )
    return False, (
        "permission denied on docker.sock — add your user to docker: "
        "sudo usermod -aG docker $USER && newgrp docker"
    )


def run_docker(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run docker; retry via sg docker when user is in group but session is stale."""
    cmd = ["docker", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return result
    if _in_docker_group() and shutil.which("sg"):
        sg_cmd = ["sg", "docker", "-c", " ".join(_shell_quote(["docker", *args]))]
        result = subprocess.run(sg_cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )
    return result


def _shell_quote(parts: list[str]) -> list[str]:
    out = []
    for p in parts:
        if not p:
            out.append("''")
        elif any(c in p for c in " \t\n'\"\\$"):
            out.append("'" + p.replace("'", "'\\''") + "'")
        else:
            out.append(p)
    return out
