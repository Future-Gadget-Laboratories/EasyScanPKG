"""Docker invocation with group-session fallback."""

from __future__ import annotations

import grp
import os
import pwd
import shutil
import shlex
import subprocess


def _in_docker_group() -> bool:
    """True when the user is a configured member of group 'docker'.

    A login session that predates ``usermod -aG docker`` does not carry the
    group in ``os.getgroups()``; that is exactly when ``sg docker`` is needed,
    so membership is read from the group database as well.
    """
    try:
        group = grp.getgrnam("docker")
    except KeyError:
        return False
    if group.gr_gid in os.getgroups():
        return True
    try:
        user = pwd.getpwuid(os.getuid())
    except KeyError:
        return False
    return user.pw_gid == group.gr_gid or user.pw_name in group.gr_mem


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
    if _in_docker_group() and shutil.which("sg"):
        via_sg = run_docker(["info"], timeout=30)
        if via_sg.returncode == 0:
            return True, "ok via sg docker"
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


def _permission_denied(result: subprocess.CompletedProcess[str]) -> bool:
    text = f"{result.stderr or ''}{result.stdout or ''}".lower()
    return "permission denied" in text and "docker" in text


def run_docker(
    args: list[str],
    *,
    check: bool = False,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run docker; retry via sg docker when user is in group but session is stale.

    The child inherits this process environment, so secrets are passed as bare
    ``-e NAME`` flags and never appear in argv (``ps``). ``sg`` keeps the
    environment as well. ``env`` replaces the inherited environment when set.
    """
    cmd = ["docker", *args]
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=False, timeout=timeout, env=env
    )
    if result.returncode == 0:
        return result
    if _permission_denied(result) and _in_docker_group() and shutil.which("sg"):
        sg_cmd = ["sg", "docker", "-c", " ".join(_shell_quote(["docker", *args]))]
        result = subprocess.run(
            sg_cmd, capture_output=True, text=True, check=False, timeout=timeout, env=env
        )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )
    return result


def _shell_quote(parts: list[str]) -> list[str]:
    return [shlex.quote(part) for part in parts]
