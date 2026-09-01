"""Resolve Sonar connection: remote → prompt → local fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from env_load import apply_sonar_env
from env_write import LOCAL_ENV, REMOTE_ENV, read_env_file, update_remote_credentials
from local_server import DEFAULT_LOCAL_URL, ensure_project, ensure_token, is_running, start
from merge_cursor_mcp import merge_mcp_config
from policy_db import PolicyStore, resolve_store
from prompt_credentials import prompt_credentials, prompt_unreachable
from server_health import AuthStatus, HealthResult, check_server


class Backend(Enum):
    REMOTE = "remote"
    LOCAL = "local"
    STANDALONE = "standalone"


@dataclass
class ResolvedConnection:
    backend: Backend
    url: str | None
    token: str | None
    project_key: str | None
    detail: str
    prompted: bool = False
    credentials_saved: bool = False


DEFAULT_CONNECTION_KV = {
    "fallback_to_local_server": True,
    "local_url": DEFAULT_LOCAL_URL,
    "local_project_prefix": "local-",
    "active_backend": "unknown",
}


def _connection_kv(store: PolicyStore) -> dict:
    kv = store.get_domain("connection")
    return {**DEFAULT_CONNECTION_KV, **kv}


def _apply_to_env(url: str | None, token: str | None, project_key: str | None) -> None:
    if url:
        os.environ["SONARQUBE_URL"] = url
    if token:
        os.environ["SONARQUBE_TOKEN"] = token
    if project_key:
        os.environ["SONARQUBE_PROJECT_KEY"] = project_key


def _local_project_key(store: PolicyStore, workspace: str | Path) -> str:
    ws = store.get_workspace(workspace)
    base = (ws or {}).get("project_key") or os.environ.get("SONARQUBE_PROJECT_KEY") or "workspace"
    prefix = str(_connection_kv(store).get("local_project_prefix", "local-"))
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in base)
    return f"{prefix}{safe}"[:400]


def _try_remote(
    store: PolicyStore,
    url: str | None,
    token: str | None,
    project_key: str | None,
    *,
    prompted: bool,
) -> ResolvedConnection | None:
    if not url and not token:
        return None
    health = check_server(url or "", token)
    if health.status != AuthStatus.OK:
        return None
    _apply_to_env(url, token, project_key)
    store.set_pref("connection", "active_backend", Backend.REMOTE.value)
    return ResolvedConnection(
        Backend.REMOTE, url, token, project_key, health.detail, prompted=prompted
    )


def _prompt_for_remote(
    url: str | None,
    health: HealthResult,
    *,
    prefer_cli: bool,
) -> tuple[str | None, str | None, bool]:
    """Return (url, token, credentials_saved)."""
    bad = health.status in (AuthStatus.UNAUTHORIZED, AuthStatus.UNREACHABLE)
    if bad and url:
        creds = prompt_unreachable(url, health.detail, url_default=url, prefer_cli=prefer_cli)
    else:
        creds = prompt_credentials(
            reason="SonarQube URL and user token are required for connected analysis.",
            url_default=url or "http://127.0.0.1:9000",
            prefer_cli=prefer_cli,
        )
    if creds.cancelled or not creds.url or not creds.token:
        return url, None, False
    update_remote_credentials(url=creds.url, token=creds.token)
    os.environ["SONARQUBE_URL"] = creds.url
    os.environ["SONARQUBE_TOKEN"] = creds.token
    return creds.url, creds.token, True


def _try_local_fallback(
    store: PolicyStore,
    workspace: str | Path,
    *,
    conn: dict,
    remote_url: str | None,
    project_key: str | None,
    health: HealthResult,
    prompted: bool,
    credentials_saved: bool,
) -> ResolvedConnection | None:
    local_env = read_env_file(LOCAL_ENV)
    local_url = str(conn.get("local_url", DEFAULT_LOCAL_URL))
    local_key = local_env.get("SONARQUBE_PROJECT_KEY") or _local_project_key(store, workspace)

    if not is_running():
        try:
            start(wait=True)
        except Exception as exc:
            store.record_event("local_start_failed", {"error": str(exc)})
            if health.status == AuthStatus.MISSING_CREDENTIALS:
                return None
            return ResolvedConnection(
                Backend.STANDALONE,
                remote_url,
                None,
                project_key,
                f"remote failed ({health.detail}); local start failed: {exc}",
                prompted=prompted,
                credentials_saved=credentials_saved,
            )

    try:
        local_token = ensure_token(project_key=local_key)
        ensure_project(local_key, project_name=Path(workspace).name)
        local_health = check_server(local_url, local_token)
        if local_health.status != AuthStatus.OK:
            return None
        _apply_to_env(local_url, local_token, local_key)
        store.set_pref("connection", "active_backend", Backend.LOCAL.value)
        merge_mcp_config()
        return ResolvedConnection(
            Backend.LOCAL,
            local_url,
            local_token,
            local_key,
            "using local SonarQube Docker fallback",
            prompted=prompted,
            credentials_saved=credentials_saved,
        )
    except Exception as exc:
        store.record_event("local_fallback_failed", {"error": str(exc)})
        return None


def _seed_remote_creds(store: PolicyStore) -> tuple[str | None, str | None, str | None]:
    remote_env = read_env_file(REMOTE_ENV)
    url = (
        os.environ.get("SONARQUBE_URL")
        or remote_env.get("SONARQUBE_URL")
        or store.get_connection_prefs().last_url
    )
    token = os.environ.get("SONARQUBE_TOKEN") or remote_env.get("SONARQUBE_TOKEN")
    project_key = os.environ.get("SONARQUBE_PROJECT_KEY") or remote_env.get(
        "SONARQUBE_PROJECT_KEY"
    )
    return url, token, project_key


def _needs_remote_prompt(health: HealthResult) -> bool:
    return health.status in (
        AuthStatus.MISSING_CREDENTIALS,
        AuthStatus.UNAUTHORIZED,
        AuthStatus.UNREACHABLE,
    )


def _maybe_prompt_and_retry(
    store: PolicyStore,
    url: str | None,
    project_key: str | None,
    health: HealthResult,
    *,
    prefer_cli: bool,
) -> tuple[str | None, str | None, HealthResult, bool, ResolvedConnection | None]:
    """Prompt for remote creds when needed. Returns (url, token, health, saved, early_hit)."""
    new_url, new_token, credentials_saved = _prompt_for_remote(
        url, health, prefer_cli=prefer_cli
    )
    if not credentials_saved:
        return new_url, new_token, health, False, None

    hit = _try_remote(store, new_url, new_token, project_key, prompted=True)
    if hit:
        hit.credentials_saved = True
        merge_mcp_config()
        return new_url, new_token, health, True, hit

    refreshed = check_server(new_url or "", new_token)
    return new_url, new_token, refreshed, True, None


def _standalone_result(
    store: PolicyStore,
    *,
    url: str | None,
    token: str | None,
    project_key: str | None,
    health: HealthResult,
    prompted: bool,
    credentials_saved: bool,
) -> ResolvedConnection:
    store.set_pref("connection", "active_backend", Backend.STANDALONE.value)
    detail = health.detail
    if prompted and not credentials_saved:
        detail = "Token is missing (credential prompt cancelled or empty)"
    return ResolvedConnection(
        Backend.STANDALONE,
        url,
        token or None,
        project_key,
        detail,
        prompted=prompted,
        credentials_saved=credentials_saved,
    )


def resolve_connection(
    store: PolicyStore | None = None,
    *,
    workspace: str | Path = ".",
    prompt: bool = True,
    allow_local_fallback: bool | None = None,
) -> ResolvedConnection:
    store = store or resolve_store()
    apply_sonar_env()
    conn = _connection_kv(store)
    if allow_local_fallback is None:
        allow_local_fallback = bool(conn.get("fallback_to_local_server", True))

    url, token, project_key = _seed_remote_creds(store)
    prefer_cli = os.environ.get("SFT_SONAR_CLI_PROMPT") == "1"

    hit = _try_remote(store, url, token, project_key, prompted=False)
    if hit:
        return hit

    health = check_server(url or "", token)
    prompted = False
    credentials_saved = False
    if prompt and _needs_remote_prompt(health):
        prompted = True
        url, token, health, credentials_saved, early = _maybe_prompt_and_retry(
            store, url, project_key, health, prefer_cli=prefer_cli
        )
        if early:
            return early

    if allow_local_fallback:
        local_hit = _try_local_fallback(
            store,
            workspace,
            conn=conn,
            remote_url=url,
            project_key=project_key,
            health=health,
            prompted=prompted,
            credentials_saved=credentials_saved,
        )
        if local_hit:
            return local_hit

    return _standalone_result(
        store,
        url=url,
        token=token,
        project_key=project_key,
        health=health,
        prompted=prompted,
        credentials_saved=credentials_saved,
    )
