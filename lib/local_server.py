"""Manage local SonarQube Server Docker stack for offline fallback."""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from docker_util import run_docker
from env_write import LOCAL_ENV, read_env_file, update_local_credentials
from server_health import AuthStatus, check_server, wait_for_system_up

DEFAULT_LOCAL_URL = "http://127.0.0.1:9000"
ADMIN_STATE = Path.home() / ".config" / "sft" / "sonar-local-admin.json"
COMPOSE_FILE = Path(__file__).resolve().parents[1] / "docker" / "docker-compose.sonar-local.yml"
NETWORK_NAME = "sft-sonarqube-net"
DB_CONTAINER = "sft-sonarqube-db"
SQ_CONTAINER = "sft-sonarqube"
NATIVE_VOLUMES = (
    "sft-sonarqube-db",
    "sft-sonarqube-data",
    "sft-sonarqube-extensions",
    "sft-sonarqube-logs",
)


@dataclass
class LocalServerStatus:
    running: bool
    url: str
    detail: str
    token_present: bool


def _docker() -> str | None:
    return shutil.which("docker")


def _compose_cmd(*args: str) -> list[str]:
    docker = _docker()
    if not docker:
        raise RuntimeError("docker not found in PATH")
    # Prefer docker compose plugin (docker-compose-v2 on Ubuntu)
    probe = subprocess.run(
        [docker, "compose", "version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode == 0:
        return [docker, "compose", "-f", str(COMPOSE_FILE), *args]
    compose = shutil.which("docker-compose")
    if compose:
        return [compose, "-f", str(COMPOSE_FILE), *args]
    hint = "sudo apt install docker-compose-v2"
    if "unknown command: docker compose" in (probe.stderr or ""):
        hint = (
            "Docker is installed but the Compose plugin is missing. "
            "Install it with: sudo apt install docker-compose-v2"
        )
    elif probe.returncode != 0 and "permission denied" in (probe.stderr or "").lower():
        hint = "Docker permission denied — run: newgrp docker   then retry"
    raise RuntimeError(f"docker compose not available — {hint}")


def _ok_result() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["native-docker"], returncode=0, stdout="", stderr="")


def _ensure_network() -> None:
    if run_docker(["network", "inspect", NETWORK_NAME]).returncode != 0:
        result = run_docker(["network", "create", NETWORK_NAME])
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "network create failed")[-500:])


def _ensure_volumes() -> None:
    for volume in NATIVE_VOLUMES:
        if run_docker(["volume", "inspect", volume]).returncode != 0:
            result = run_docker(["volume", "create", volume])
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "volume create failed")[-500:])


def _container_running(name: str) -> bool:
    result = run_docker(["inspect", "--type", "container", "-f", "{{.State.Running}}", name])
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def _ensure_container(name: str, run_args: list[str]) -> None:
    inspect = run_docker(["inspect", "--type", "container", name])
    if inspect.returncode == 0:
        if not _container_running(name):
            result = run_docker(["start", name])
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or f"start {name} failed")[-500:])
        return
    result = run_docker(["run", "-d", "--name", name, *run_args])
    if result.returncode != 0:
        err = result.stderr or result.stdout or f"run {name} failed"
        err_lower = err.lower()
        if "already in use" in err_lower or "conflict" in err_lower:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if _container_running(name):
                    return
                start_result = run_docker(["start", name])
                if start_result.returncode == 0:
                    return
                time.sleep(2)
            if run_docker(["inspect", "--type", "container", name]).returncode == 0:
                return
        raise RuntimeError(err[-500:])


def _wait_container_healthy(name: str, *, timeout_s: int = 120) -> None:
    result = run_docker(
        ["inspect", "--type", "container", "-f", "{{.State.Health.Status}}", name]
    )
    if result.returncode == 0 and result.stdout.strip() == "healthy":
        return
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = run_docker(
            ["inspect", "--type", "container", "-f", "{{.State.Health.Status}}", name]
        )
        if result.returncode == 0 and result.stdout.strip() == "healthy":
            return
        time.sleep(2)
    raise RuntimeError(f"{name} did not become healthy within {timeout_s}s")


def _native_up() -> subprocess.CompletedProcess[str]:
    _ensure_network()
    _ensure_volumes()
    _ensure_container(
        DB_CONTAINER,
        [
            "--network",
            NETWORK_NAME,
            "--restart",
            "unless-stopped",
            "-e",
            "POSTGRES_USER=sonar",
            "-e",
            "POSTGRES_PASSWORD=sonar",
            "-e",
            "POSTGRES_DB=sonar",
            "-v",
            "sft-sonarqube-db:/var/lib/postgresql/data",
            "--health-cmd",
            "pg_isready -U sonar -d sonar",
            "--health-interval",
            "10s",
            "--health-timeout",
            "5s",
            "--health-retries",
            "10",
            "postgres:16-alpine",
        ],
    )
    _wait_container_healthy(DB_CONTAINER)
    _ensure_container(
        SQ_CONTAINER,
        [
            "--network",
            NETWORK_NAME,
            "--restart",
            "unless-stopped",
            "-p",
            "127.0.0.1:9000:9000",
            "-e",
            f"SONAR_JDBC_URL=jdbc:postgresql://{DB_CONTAINER}:5432/sonar",
            "-e",
            "SONAR_JDBC_USERNAME=sonar",
            "-e",
            "SONAR_JDBC_PASSWORD=sonar",
            "-v",
            "sft-sonarqube-data:/opt/sonarqube/data",
            "-v",
            "sft-sonarqube-extensions:/opt/sonarqube/extensions",
            "-v",
            "sft-sonarqube-logs:/opt/sonarqube/logs",
            "sonarqube:community",
        ],
    )
    return _ok_result()


def _native_down() -> subprocess.CompletedProcess[str]:
    for name in (SQ_CONTAINER, DB_CONTAINER):
        run_docker(["rm", "-f", name])
    return _ok_result()


def is_running() -> bool:
    docker = _docker()
    if not docker:
        return False
    result = run_docker(
        ["inspect", "--type", "container", "-f", "{{.State.Running}}", SQ_CONTAINER],
    )
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def status() -> LocalServerStatus:
    local = read_env_file(LOCAL_ENV)
    running = is_running()
    return LocalServerStatus(
        running=running,
        url=local.get("SONARQUBE_URL", DEFAULT_LOCAL_URL),
        detail="running" if running else "stopped",
        token_present=bool(local.get("SONARQUBE_TOKEN")),
    )


def _run_compose(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        cmd = _compose_cmd(*args)
    except RuntimeError as exc:
        if args == ("up", "-d"):
            return _native_up()
        if args == ("down",):
            return _native_down()
        raise
    if cmd[0] == shutil.which("docker") or cmd[0] == "docker":
        return run_docker(cmd[1:])
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        from docker_util import _in_docker_group

        if _in_docker_group() and shutil.which("sg"):
            from docker_util import _shell_quote

            joined = " ".join(_shell_quote(cmd))
            result = subprocess.run(
                ["sg", "docker", "-c", joined],
                capture_output=True,
                text=True,
                check=False,
            )
    return result


def start(*, wait: bool = True) -> LocalServerStatus:
    result = _run_compose("up", "-d")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "compose up failed")[-500:])
    if wait:
        if not wait_for_system_up(DEFAULT_LOCAL_URL):
            raise RuntimeError(f"local SonarQube did not become UP at {DEFAULT_LOCAL_URL}")
    return status()


def stop() -> LocalServerStatus:
    _run_compose("down")
    return status()


def _basic_auth(user: str, password: str = "") -> str:
    raw = f"{user}:{password}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _api(
    method: str,
    path: str,
    *,
    user: str | None = None,
    password: str = "",
    query: dict | None = None,
    form: dict | None = None,
) -> tuple[int, str]:
    url = DEFAULT_LOCAL_URL.rstrip("/") + path
    data: bytes | None = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
    elif query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, data=data, method=method)
    if form is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if user is not None:
        req.add_header("Authorization", _basic_auth(user, password))
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _validate_credentials(login: str, password: str) -> bool:
    code, body = _api("GET", "/api/authentication/validate", user=login, password=password)
    if code != 200:
        return False
    try:
        return json.loads(body).get("valid") is True
    except json.JSONDecodeError:
        return False


def _wait_default_admin_ready(*, timeout_s: float = 180.0, poll_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _validate_credentials("admin", "admin"):
            return True
        time.sleep(poll_s)
    return False


def _load_admin_state() -> dict:
    if ADMIN_STATE.is_file():
        return json.loads(ADMIN_STATE.read_text(encoding="utf-8"))
    return {}


def _save_admin_state(data: dict) -> None:
    ADMIN_STATE.parent.mkdir(parents=True, exist_ok=True)
    ADMIN_STATE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.chmod(ADMIN_STATE, 0o600)


def _generate_admin_password() -> str:
    # SonarQube 26 rejects token_urlsafe-only passwords (needs a special character).
    return f"{secrets.token_urlsafe(14)}!1Aa"


def _admin_reset_hint() -> str:
    volumes = " ".join(NATIVE_VOLUMES)
    return (
        "Reset local SonarQube data with: "
        f"sonar-local-down && docker volume rm {volumes}"
    )


def _ensure_admin_password() -> str:
    state = _load_admin_state()
    stored = state.get("admin_password")
    if stored and _validate_credentials("admin", stored):
        return stored

    if not _wait_default_admin_ready():
        raise RuntimeError(
            "local SonarQube admin credentials unavailable. " + _admin_reset_hint()
        )

    password = _generate_admin_password()
    code, body = _api(
        "POST",
        "/api/users/change_password",
        user="admin",
        password="admin",
        form={
            "login": "admin",
            "previousPassword": "admin",
            "password": password,
        },
    )
    if code not in (200, 204):
        raise RuntimeError(
            f"failed to set local admin password (HTTP {code}). " + _admin_reset_hint()
        )
    state["admin_password"] = password
    _save_admin_state(state)
    return password


def ensure_token(*, project_key: str | None = None) -> str:
    local = read_env_file(LOCAL_ENV)
    cached = local.get("SONARQUBE_TOKEN")
    local_url = local.get("SONARQUBE_URL", DEFAULT_LOCAL_URL)
    if cached:
        health = check_server(local_url, cached)
        if health.status == AuthStatus.OK:
            return cached

    if not is_running():
        start(wait=True)

    admin_password = _ensure_admin_password()
    code, body = _api(
        "POST",
        "/api/user_tokens/generate",
        user="admin",
        password=admin_password,
        query={"name": "sft-agent-bridge"},
    )
    if code != 200:
        raise RuntimeError(f"token generation failed: HTTP {code} {body[:200]}")
    token = json.loads(body).get("token")
    if not token:
        raise RuntimeError("token generation returned no token")
    update_local_credentials(DEFAULT_LOCAL_URL, token, project_key=project_key)
    return token


def ensure_project(project_key: str, project_name: str | None = None) -> None:
    local = read_env_file(LOCAL_ENV)
    token = local.get("SONARQUBE_TOKEN") or ensure_token(project_key=project_key)
    name = project_name or project_key
    code, body = _api(
        "POST",
        "/api/projects/create",
        user=token,
        query={"project": project_key, "name": name},
    )
    if code == 200:
        return
    if code == 400 and "already exists" in body.lower():
        return
    if code == 403:
        return
    raise RuntimeError(f"project create failed: HTTP {code} {body[:200]}")
