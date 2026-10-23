"""Manage local SonarQube Server Docker stack for offline fallback."""

from __future__ import annotations

import base64
import json
import os
import re
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
from env_write import LOCAL_ENV, read_env_file, update_local_credentials, write_private
from server_health import AuthStatus, check_server, wait_for_system_up
from sonar_runtime import compose_env, native_run_args, parse_size_mb, resolve_memory, restart_policy
from safe_io import urlopen as safe_urlopen

DEFAULT_LOCAL_URL = "http://127.0.0.1:9000"
ADMIN_STATE = Path.home() / ".config" / "sft" / "sonar-local-admin.json"
COMPOSE_FILE = Path(__file__).resolve().parents[1] / "docker" / "docker-compose.sonar-local.yml"
NETWORK_NAME = "sft-sonarqube-net"
DB_CONTAINER = "sft-sonarqube-db"
SQ_CONTAINER = "sft-sonarqube"
# Fixed hostname + matching /etc/hosts entry so the embedded Elasticsearch can
# always resolve the local host name, even if the container ends up without a
# network endpoint (seen when port 9000 was held by another server at start).
SQ_HOSTNAME = "sft-sonarqube"
SQ_EXTENSIONS_VOLUME = "sft-sonarqube-extensions"
SQ_IMAGE = "sonarqube:community"
# Images used after the server is up (scans, MCP helper). Pulled by
# sonar-local-up so a fresh host does not fail its first scan / check.
AUX_IMAGES = ("sonarsource/sonar-scanner-cli:latest", "sonarsource/sonarqube-mcp")
NATIVE_VOLUMES = (
    "sft-sonarqube-db",
    "sft-sonarqube-data",
    "sft-sonarqube-extensions",
    "sft-sonarqube-logs",
)


@dataclass
class ServerIdentity:
    """Who answers DEFAULT_LOCAL_URL.

    owner: "sft" (our container), "foreign" (another server/process),
    "starting" (our container runs but nothing answers yet), "none".
    """

    owner: str
    detail: str
    host_version: str = ""
    container_version: str = ""


@dataclass
class LocalServerStatus:
    running: bool
    url: str
    detail: str
    token_present: bool


_RUNNING_FMT = "{{.State.Running}}"

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
    result = run_docker(["inspect", "--type", "container", "-f", _RUNNING_FMT, name])
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def _ensure_container(name: str, run_args: list[str]) -> None:
    inspect = run_docker(["inspect", "--type", "container", name])
    if inspect.returncode == 0:
        spec = json.loads(inspect.stdout)[0]
        if _native_configuration_matches(spec, run_args):
            _start_existing_container(name)
            return
        # Keep named volumes when applying changed memory/heaps/restart policy.
        run_docker(["rm", "-f", name], check=True)
    result = run_docker(["run", "-d", "--name", name, *run_args])
    if result.returncode == 0:
        return
    err = result.stderr or result.stdout or f"run {name} failed"
    if _is_name_conflict(err):
        _recover_name_conflict(name)
        return
    raise RuntimeError(err[-500:])


def _native_configuration_matches(spec: dict, args: list[str]) -> bool:
    host = spec.get("HostConfig", {})
    if host.get("RestartPolicy", {}).get("Name") != args[args.index("--restart") + 1]:
        return False
    for flag, key in (("--memory", "Memory"), ("--memory-swap", "MemorySwap")):
        if flag in args:
            expected = parse_size_mb(args[args.index(flag) + 1]) * 1024 * 1024
            if host.get(key) != expected:
                return False
    current_env = set(spec.get("Config", {}).get("Env") or [])
    for index, arg in enumerate(args[:-1]):
        if arg == "-e" and args[index + 1].startswith(
            ("SONAR_WEB_JAVAOPTS=", "SONAR_CE_JAVAOPTS=", "SONAR_SEARCH_JAVAOPTS=")
        ) and args[index + 1] not in current_env:
            return False
    return True


def lease_running(name: str = SQ_CONTAINER) -> bool:
    """Container state for leases: daemon failures must not mean 'stopped'."""
    result = run_docker(
        ["inspect", "--type", "container", "-f", _RUNNING_FMT, name]
    )
    if result.returncode == 0 and result.stdout.strip().lower() in {"true", "false"}:
        return result.stdout.strip().lower() == "true"
    error = (result.stderr or "").lower()
    if "no such object" in error or "no such container" in error:
        return False
    raise RuntimeError("cannot determine local SonarQube state; keeping lease ownership")


def _start_existing_container(name: str) -> None:
    if _container_running(name):
        return
    result = run_docker(["start", name])
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or f"start {name} failed")[-500:])


def _is_name_conflict(err: str) -> bool:
    err_lower = err.lower()
    return "already in use" in err_lower or "conflict" in err_lower


def _recover_name_conflict(name: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _container_running(name):
            return
        if run_docker(["start", name]).returncode == 0:
            return
        time.sleep(2)
    if run_docker(["inspect", "--type", "container", name]).returncode == 0:
        return
    raise RuntimeError(f"container name conflict for {name} and recovery failed")


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
            restart_policy(),
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
            "--network-alias",
            "sonarqube",
            "--hostname",
            SQ_HOSTNAME,
            "--add-host",
            f"{SQ_HOSTNAME}:127.0.0.1",
            "--restart",
            restart_policy(),
            *native_run_args(resolve_memory()),
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
            SQ_IMAGE,
        ],
    )
    return _ok_result()


def _native_down() -> subprocess.CompletedProcess[str]:
    """Remove both containers (volumes are kept). Only used by stop(remove=True)."""
    for name in (SQ_CONTAINER, DB_CONTAINER):
        run_docker(["rm", "-f", name])
    return _ok_result()


def _native_stop() -> subprocess.CompletedProcess[str]:
    """Stop both containers without removing them."""
    for name in (SQ_CONTAINER, DB_CONTAINER):
        if lease_running(name):
            result = run_docker(["stop", name])
            if result.returncode != 0:
                return result
    return _ok_result()


def is_running() -> bool:
    docker = _docker()
    if not docker:
        return False
    result = run_docker(
        ["inspect", "--type", "container", "-f", _RUNNING_FMT, SQ_CONTAINER],
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
    except RuntimeError:
        if args == ("up", "-d"):
            return _native_up()
        if args == ("down",):
            return _native_down()
        if args == ("stop",):
            return _native_stop()
        raise
    env = dict(os.environ)
    if args == ("up", "-d"):
        env.update(compose_env(resolve_memory()))
    if cmd[0] == shutil.which("docker") or cmd[0] == "docker":
        return run_docker(cmd[1:], env=env)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    if result.returncode == 0:
        return result
    from docker_util import _in_docker_group, _shell_quote

    if not (_in_docker_group() and shutil.which("sg")):
        return result
    joined = " ".join(_shell_quote(cmd))
    return subprocess.run(
        ["sg", "docker", "-c", joined],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

def _http_system_status(url: str = DEFAULT_LOCAL_URL) -> dict | None:
    try:
        with safe_urlopen(f"{url.rstrip('/')}/api/system/status", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _container_system_status() -> dict | None:
    """/api/system/status as seen from inside our container (bypasses port 9000)."""
    result = run_docker(
        [
            "exec",
            SQ_CONTAINER,
            "curl",
            "-fsS",
            "-m",
            "5",
            "http://127.0.0.1:9000/api/system/status",
        ],
        timeout=30,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout or "")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


_APP_JAR_RE = re.compile(r"sonar-application-(\d+(?:\.\d+)+)\.jar")


def container_server_version() -> str:
    """SonarQube version of our container, read from its lib/ (no HTTP)."""
    result = run_docker(["exec", SQ_CONTAINER, "ls", "/opt/sonarqube/lib"], timeout=30)
    if result.returncode != 0:
        return ""
    match = _APP_JAR_RE.search(result.stdout or "")
    return match.group(1) if match else ""


def _container_publishes_port() -> bool:
    result = run_docker(["port", SQ_CONTAINER, "9000/tcp"], timeout=30)
    return result.returncode == 0 and bool((result.stdout or "").strip())


def server_identity(url: str = DEFAULT_LOCAL_URL) -> ServerIdentity:
    """Decide whether DEFAULT_LOCAL_URL is answered by our sft-sonarqube container."""
    host = _http_system_status(url)
    host_version = str((host or {}).get("version") or "")
    if not is_running():
        if host is not None:
            return ServerIdentity(
                "foreign",
                f"{url} is answered by SonarQube {host_version or '?'} but container "
                f"{SQ_CONTAINER} is not running",
                host_version=host_version,
            )
        return ServerIdentity("none", f"{SQ_CONTAINER} not running; nothing answers {url}")
    if host is None:
        return ServerIdentity("starting", f"{SQ_CONTAINER} running; {url} not answering yet")
    return _identity_of_answering_server(url, host)


def _identity_of_answering_server(url: str, host: dict) -> ServerIdentity:
    """Our container runs and something answers url: is it our container?"""
    host_version = str(host.get("version") or "")
    if not _container_publishes_port():
        return ServerIdentity(
            "foreign",
            f"{url} is answered by SonarQube {host_version or '?'} while {SQ_CONTAINER} "
            "has no port 9000 binding (the port was taken when it started)",
            host_version=host_version,
        )
    inner = _container_system_status()
    if inner is not None:
        inner_version = str(inner.get("version") or "")
        if inner.get("id") and inner.get("id") == host.get("id"):
            return ServerIdentity(
                "sft",
                f"{SQ_CONTAINER} {inner_version} answers {url}",
                host_version=host_version,
                container_version=inner_version,
            )
        return ServerIdentity(
            "foreign",
            f"{url} is answered by SonarQube {host_version or '?'} (id {host.get('id')}), "
            f"not {SQ_CONTAINER} {inner_version or '?'} (id {inner.get('id')})",
            host_version=host_version,
            container_version=inner_version,
        )
    # Equal software versions do not identify a server.
    return ServerIdentity(
        "unknown",
        f"cannot compare server IDs for {url} and {SQ_CONTAINER}",
        host_version=host_version,
        container_version=container_server_version(),
    )


def require_own_server(url: str = DEFAULT_LOCAL_URL) -> ServerIdentity:
    """Raise unless our container answers url. Guards admin/token bootstrap."""
    ident = server_identity(url)
    if ident.owner != "sft":
        raise RuntimeError(
            f"refusing to manage {url}: {ident.detail}. Stop the other server holding "
            "port 9000 (keep its data; e.g. `docker compose stop`) and re-run sonar-local-up."
        )
    return ident


def _container_networks(name: str) -> dict:
    result = run_docker(
        ["inspect", "--type", "container", "-f", "{{json .NetworkSettings.Networks}}", name]
    )
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout or "null")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _repair_network() -> bool:
    """Reattach sft-sonarqube to the DB network if it lost its endpoint.

    Returns True when a repair (connect + restart) was performed.
    """
    if run_docker(["inspect", "--type", "container", SQ_CONTAINER]).returncode != 0:
        return False
    if _container_networks(SQ_CONTAINER):
        return False
    db_networks = list(_container_networks(DB_CONTAINER))
    target = db_networks[0] if db_networks else NETWORK_NAME
    result = run_docker(
        ["network", "connect", "--alias", "sonarqube", "--alias", SQ_HOSTNAME, target, SQ_CONTAINER]
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{SQ_CONTAINER} has no network endpoint and reconnecting to {target} failed: "
            + (result.stderr or result.stdout or "")[-300:]
        )
    run_docker(["restart", SQ_CONTAINER], timeout=120)
    return True


_HOSTNAME_FAILURE = ("UnknownHostException", "Could not determine local host name")
# Stack frames from the plugin only appear when it threw during startup.
_CXX_FAILURE = re.compile(r"at org\.sonar\.(?:cxx|plugins\.cxx)\.|CxxRuleRepository")


def _disable_cxx_plugins_in_volume() -> bool:
    """Move sonar-cxx JARs out of the plugins dir (kept under plugins-disabled/)."""
    result = run_docker(
        [
            "run",
            "--rm",
            "--user",
            "0",
            "--entrypoint",
            "sh",
            "-v",
            f"{SQ_EXTENSIONS_VOLUME}:/ext",
            SQ_IMAGE,
            "-c",
            (
                "mkdir -p /ext/plugins-disabled && moved=0; "
                "for f in /ext/plugins/sonar-cxx-plugin-*.jar; do "
                '[ -e "$f" ] || continue; mv -f "$f" /ext/plugins-disabled/ && moved=1; '
                'done; [ "$moved" = 1 ]'
            ),
        ],
        timeout=300,
    )
    return result.returncode == 0


def _recover_failed_start() -> str | None:
    """Inspect logs of a server that never came UP and fix known causes.

    Returns a short description of the recovery applied, or None.
    """
    logs = run_docker(["logs", "--tail", "500", SQ_CONTAINER], timeout=60)
    text = f"{logs.stdout or ''}{logs.stderr or ''}"
    if any(marker in text for marker in _HOSTNAME_FAILURE) and _repair_network():
        return "reattached network endpoint (hostname resolution failure)"
    if _CXX_FAILURE.search(text) and _disable_cxx_plugins_in_volume():
        run_docker(["restart", SQ_CONTAINER], timeout=120)
        return "disabled incompatible sonar-cxx plugin (reinstalled after start)"
    return None


def pull_aux_images() -> list[str]:
    """Pull scanner/MCP images when missing. Returns images that failed."""
    failed: list[str] = []
    for image in AUX_IMAGES:
        if run_docker(["image", "inspect", image], timeout=60).returncode == 0:
            continue
        if run_docker(["pull", image], timeout=900).returncode != 0:
            failed.append(image)
    return failed


def _wait_up_or_recover() -> None:
    """Wait for UP; on failure apply a known recovery once, then wait again."""
    if wait_for_system_up(DEFAULT_LOCAL_URL):
        return
    recovery = _recover_failed_start()
    if recovery and wait_for_system_up(DEFAULT_LOCAL_URL, timeout_s=300.0):
        print(f"NOTE: local SonarQube recovered: {recovery}", flush=True)
        return
    after = f" (after: {recovery})" if recovery else ""
    raise RuntimeError(
        f"local SonarQube did not become UP at {DEFAULT_LOCAL_URL}{after}; "
        f"see: docker logs {SQ_CONTAINER}"
    )


def _pull_aux_images_soft() -> None:
    try:
        failed = pull_aux_images()
    except Exception as exc:  # noqa: BLE001 — scans pull again on demand
        print(f"NOTE: image pull skipped: {exc}", flush=True)
        return
    if failed:
        print(f"NOTE: could not pull {', '.join(failed)}", flush=True)


def start(*, wait: bool = True) -> LocalServerStatus:
    ident = server_identity()
    if ident.owner == "foreign":
        raise RuntimeError(
            f"refusing to start local SonarQube: {ident.detail}. Stop the other server "
            "holding port 9000 (keep its data; e.g. `docker compose stop`) and retry."
        )
    result = _run_compose("up", "-d")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "compose up failed")[-500:])
    _repair_network()
    if wait:
        _wait_up_or_recover()
        require_own_server()
    _ensure_cxx_soft()
    _pull_aux_images_soft()
    return status()


def _ensure_cxx_soft() -> None:
    # Community C/C++ via SonarOpenCommunity/sonar-cxx (optional; default on).
    try:
        from sonar_cxx_plugin import ensure_cxx_plugin, install_sonar_cxx_enabled

        if install_sonar_cxx_enabled():
            cxx = ensure_cxx_plugin(url=DEFAULT_LOCAL_URL)
            if not cxx.ok:
                # Soft-fail: local stack is still usable without cxx.
                print(f"NOTE: sonar-cxx install: {cxx.detail}", flush=True)
    except Exception as exc:  # noqa: BLE001 — never block local-up on plugin issues
        print(f"NOTE: sonar-cxx install skipped: {exc}", flush=True)


def stop(*, remove: bool = False) -> LocalServerStatus:
    """Stop the stack. Containers are kept unless remove=True (volumes always kept)."""
    result = _run_compose("down" if remove else "stop")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "stop failed")[-500:])
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
    if user is not None:
        require_own_server()
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
        with safe_urlopen(req, timeout=15) as resp:
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
    write_private(ADMIN_STATE, json.dumps(data, indent=2) + "\n")


def _generate_admin_password() -> str:
    # SonarQube 26 rejects token_urlsafe-only passwords (needs a special character).
    return f"{secrets.token_urlsafe(14)}!1Aa"


def _admin_reset_hint() -> str:
    volumes = " ".join(NATIVE_VOLUMES)
    return (
        "Reset local SonarQube data (destroys it) with: "
        f"sonar-local-down --remove && docker volume rm {volumes}"
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
    code, _body = _api(
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


def admin_login() -> dict[str, str]:
    """Return local UI login details (URL, username, password, state file path)."""
    state = _load_admin_state()
    return {
        "url": DEFAULT_LOCAL_URL,
        "username": "admin",
        "password": str(state.get("admin_password") or ""),
        "state_file": str(ADMIN_STATE),
    }


def _is_loopback_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def ensure_token(*, project_key: str | None = None) -> str:
    local = read_env_file(LOCAL_ENV)
    cached = local.get("SONARQUBE_TOKEN")
    local_url = local.get("SONARQUBE_URL", DEFAULT_LOCAL_URL)
    if cached:
        if _is_loopback_url(local_url):
            if not is_running():
                start(wait=True)
            # Check ownership before sending even a cached credential.
            require_own_server(local_url)
        health = check_server(local_url, cached)
        if health.status == AuthStatus.OK:
            return cached

    if not is_running():
        start(wait=True)

    # Never set an admin password or mint tokens on a server we do not own.
    require_own_server()
    admin_password = _ensure_admin_password()
    code, body = _api(
        "POST",
        "/api/user_tokens/generate",
        user="admin",
        password=admin_password,
        query={"name": "sft-agent-bridge"},
    )
    if code != 200:
        raise RuntimeError(f"token generation failed: HTTP {code}")
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
