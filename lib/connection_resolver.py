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
from server_health import AuthStatus, check_server


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
    merged = {**DEFAULT_CONNECTION_KV, **kv}
    return merged


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

    remote_env = read_env_file(REMOTE_ENV)
    url = os.environ.get("SONARQUBE_URL") or remote_env.get("SONARQUBE_URL") or store.get_connection_prefs().last_url
    token = os.environ.get("SONARQUBE_TOKEN") or remote_env.get("SONARQUBE_TOKEN")
    project_key = os.environ.get("SONARQUBE_PROJECT_KEY") or remote_env.get("SONARQUBE_PROJECT_KEY")
    prompted = False
    credentials_saved = False
    prefer_cli = os.environ.get("SFT_SONAR_CLI_PROMPT") == "1"

    def try_remote(u: str | None, t: str | None) -> ResolvedConnection | None:
        if not u and not t:
            return None
        health = check_server(u or "", t)
        if health.status == AuthStatus.OK:
            _apply_to_env(u, t, project_key)
            store.set_pref("connection", "active_backend", Backend.REMOTE.value)
            return ResolvedConnection(
                Backend.REMOTE, u, t, project_key, health.detail, prompted=prompted
            )
        return None

    # 1) Try existing remote credentials
    hit = try_remote(url, token)
    if hit:
        return hit

    health = check_server(url or "", token)
    missing = health.status == AuthStatus.MISSING_CREDENTIALS
    bad = health.status in (AuthStatus.UNAUTHORIZED, AuthStatus.UNREACHABLE)

    # 2) Prompt user when missing or bad remote
    if prompt and (missing or bad):
        if bad and url:
            creds = prompt_unreachable(url, health.detail, url_default=url, prefer_cli=prefer_cli)
        else:
            creds = prompt_credentials(
                reason="SonarQube URL and user token are required for connected analysis.",
                url_default=url or "https://sonar.cipherbank.money",
                prefer_cli=prefer_cli,
            )
        prompted = True
        if not creds.cancelled and creds.url and creds.token:
            update_remote_credentials(url=creds.url, token=creds.token)
            os.environ["SONARQUBE_URL"] = creds.url
            os.environ["SONARQUBE_TOKEN"] = creds.token
            url, token = creds.url, creds.token
            credentials_saved = True
            hit = try_remote(url, token)
            if hit:
                hit.prompted = True
                hit.credentials_saved = True
                merge_mcp_config()
                return hit
            health = check_server(url, token)

    # 3) Local Docker fallback
    if allow_local_fallback:
        local_env = read_env_file(LOCAL_ENV)
        local_url = str(conn.get("local_url", DEFAULT_LOCAL_URL))
        local_token = local_env.get("SONARQUBE_TOKEN")
        local_key = local_env.get("SONARQUBE_PROJECT_KEY") or _local_project_key(store, workspace)

        if not is_running():
            try:
                start(wait=True)
            except Exception as exc:
                store.record_event("local_start_failed", {"error": str(exc)})
                if health.status != AuthStatus.MISSING_CREDENTIALS:
                    return ResolvedConnection(
                        Backend.STANDALONE,
                        url,
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
            if local_health.status == AuthStatus.OK:
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

    store.set_pref("connection", "active_backend", Backend.STANDALONE.value)
    return ResolvedConnection(
        Backend.STANDALONE,
        url,
        token if token else None,
        project_key,
        health.detail if not (prompted and not credentials_saved) else "Token is missing (credential prompt cancelled or empty)",
        prompted=prompted,
        credentials_saved=credentials_saved,
    )
