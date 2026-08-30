"""Manage optional long-lived SonarQube MCP Docker helper container.

Cursor/Codex normally spawn MCP via stdio. This helper is for CLI
on-demand use and health checks. Policy DB supplies image/name;
tokens come only from environment / env files.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from docker_util import docker_ok, run_docker
from env_load import apply_sonar_env, connected_mode_available, redact_env
from policy_db import PolicyStore, resolve_store


@dataclass
class McpStatus:
    docker_available: bool
    container_running: bool
    container_name: str
    mode: str  # connected | standalone | unavailable
    detail: str


def _docker() -> str | None:
    return shutil.which("docker")


def container_running(name: str) -> bool:
    if not _docker():
        return False
    result = run_docker(["inspect", "-f", "{{.State.Running}}", name])
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def status(store: PolicyStore | None = None) -> McpStatus:
    store = store or resolve_store()
    prefs = store.get_connection_prefs()
    env = apply_sonar_env()
    docker_ok_flag, docker_detail = docker_ok()
    running = container_running(prefs.mcp_container_name) if docker_ok_flag else False
    if not docker_ok_flag:
        mode = "unavailable"
        detail = docker_detail
    elif connected_mode_available(env):
        mode = "connected"
        detail = f"url={env.get('SONARQUBE_URL') or os_get_url()}"
    else:
        mode = "standalone"
        detail = "no SONARQUBE_URL/ORG+TOKEN; standalone / IDE-bridge tools only"
    return McpStatus(
        docker_available=docker_ok_flag,
        container_running=running,
        container_name=prefs.mcp_container_name,
        mode=mode,
        detail=detail,
    )


def os_get_url() -> str:
    import os

    return os.environ.get("SONARQUBE_ORG") or "(cloud org or unset)"


def ensure_up(
    store: PolicyStore | None = None,
    *,
    ide_port: int | None = None,
    detach: bool = True,
) -> McpStatus:
    """Start helper container if docker exists and container is not running."""
    import os

    store = store or resolve_store()
    prefs = store.get_connection_prefs()
    env = apply_sonar_env()
    tool = store.get_domain("tool")

    st = status(store)
    docker_ok_flag, docker_detail = docker_ok()
    if not docker_ok_flag:
        store.record_event("mcp_up_skipped", {"reason": "no_docker", "detail": docker_detail, "mode": st.mode})
        return st
    if st.container_running:
        store.record_event("mcp_already_up", {"container": prefs.mcp_container_name})
        return st

    docker = _docker()
    assert docker is not None
    pass_keys = _prepare_mcp_env(ide_port=ide_port, tool=tool)

    cmd = [
        docker,
        "run",
        "-d",
        "--rm",
        "--name",
        prefs.mcp_container_name,
        "--init",
    ]
    for key in pass_keys:
        if os.environ.get(key):
            cmd.extend(["-e", key])
    cmd.append(prefs.mcp_image)

    pull = run_docker(["pull", prefs.mcp_image])
    store.record_event(
        "mcp_image_pull",
        {
            "image": prefs.mcp_image,
            "ok": pull.returncode == 0,
            "mode": "connected" if connected_mode_available(env) else "standalone",
            "ide_port": ide_port,
            "env": redact_env({k: os.environ[k] for k in pass_keys if os.environ.get(k)}),
        },
    )

    if detach:
        store.set_connection_prefs(
            last_url=os.environ.get("SONARQUBE_URL") or prefs.last_url,
            last_org=os.environ.get("SONARQUBE_ORG") or prefs.last_org,
        )
        return status(store)

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    store.record_event(
        "mcp_up",
        {
            "ok": result.returncode == 0,
            "stderr": (result.stderr or "")[-500:],
            "container": prefs.mcp_container_name,
        },
    )
    return status(store)


def _prepare_mcp_env(*, ide_port: int | None, tool: dict) -> list[str]:
    import os

    pass_keys = [
        "SONARQUBE_TOKEN",
        "SONARQUBE_URL",
        "SONARQUBE_ORG",
        "SONARQUBE_PROJECT_KEY",
        "SONARQUBE_TOOLSETS",
        "SONARQUBE_READ_ONLY",
        "SONARQUBE_IDE_PORT",
    ]
    if ide_port is not None:
        os.environ["SONARQUBE_IDE_PORT"] = str(ide_port)
    if tool.get("toolsets") and not os.environ.get("SONARQUBE_TOOLSETS"):
        os.environ["SONARQUBE_TOOLSETS"] = str(tool["toolsets"])
    if tool.get("read_only") and not os.environ.get("SONARQUBE_READ_ONLY"):
        os.environ["SONARQUBE_READ_ONLY"] = "true" if tool["read_only"] else "false"
    return pass_keys


def ensure_down(store: PolicyStore | None = None) -> McpStatus:
    store = store or resolve_store()
    prefs = store.get_connection_prefs()
    docker = _docker()
    if docker and container_running(prefs.mcp_container_name):
        subprocess.run(
            [docker, "rm", "-f", prefs.mcp_container_name],
            capture_output=True,
            text=True,
            check=False,
        )
        store.record_event("mcp_down", {"container": prefs.mcp_container_name})
    # Never wipe policy DB on down
    return status(store)
