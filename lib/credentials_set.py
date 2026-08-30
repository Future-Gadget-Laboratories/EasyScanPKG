"""Set remote or local SonarQube URL + user token for agents."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from env_write import (
    LOCAL_ENV,
    REMOTE_ENV,
    read_env_file,
    update_local_credentials,
    update_remote_credentials,
)
from local_server import DEFAULT_LOCAL_URL, ensure_token
from merge_codex_config import merge_codex_config
from merge_cursor_mcp import merge_mcp_config
from prompt_credentials import prompt_credentials
from server_health import AuthStatus, check_server


@dataclass
class CredentialSaveResult:
    ok: bool
    url: str
    saved_to: str
    backend: str
    auth_status: str | None = None
    detail: str | None = None
    bootstrapped: bool = False
    project_key: str | None = None


def _prompt_pair(
    *,
    url_default: str,
    prefer_cli: bool,
    reason: str,
) -> tuple[str | None, str | None, bool]:
    """Return (url, token, cancelled)."""
    if prefer_cli:
        os.environ["SFT_SONAR_CLI_PROMPT"] = "1"
    creds = prompt_credentials(
        reason=reason,
        url_default=url_default,
        prefer_cli=prefer_cli,
    )
    if creds.cancelled:
        return None, None, True
    return creds.url, creds.token, False


def set_remote_credentials(
    *,
    url: str | None = None,
    token: str | None = None,
    prefer_cli: bool = False,
    validate: bool = False,
    merge_clients: bool = True,
) -> CredentialSaveResult:
    remote = read_env_file(REMOTE_ENV)
    url_default = (
        url
        or remote.get("SONARQUBE_URL")
        or os.environ.get("SONARQUBE_URL")
        or "http://127.0.0.1:9000"
    )
    if not (url and token):
        prompted_url, prompted_token, cancelled = _prompt_pair(
            url_default=url_default,
            prefer_cli=prefer_cli,
            reason="Enter remote SonarQube URL and user token.",
        )
        if cancelled:
            return CredentialSaveResult(
                ok=False,
                url=url_default,
                saved_to=str(REMOTE_ENV),
                backend="remote",
                detail="cancelled",
            )
        url = url or prompted_url
        token = token or prompted_token

    if not url or not token:
        return CredentialSaveResult(
            ok=False,
            url=url or "",
            saved_to=str(REMOTE_ENV),
            backend="remote",
            detail="URL and token are both required",
        )

    path = update_remote_credentials(url=url, token=token)
    os.environ["SONARQUBE_URL"] = url
    os.environ["SONARQUBE_TOKEN"] = token
    if merge_clients:
        merge_mcp_config()
        merge_codex_config()

    result = CredentialSaveResult(
        ok=True, url=url, saved_to=str(path), backend="remote"
    )
    if validate:
        health = check_server(url, token)
        result.auth_status = health.status.value
        result.detail = health.detail
        result.ok = health.status == AuthStatus.OK
    return result


def set_local_credentials(
    *,
    url: str | None = None,
    token: str | None = None,
    project_key: str | None = None,
    bootstrap: bool = False,
    prefer_cli: bool = False,
    validate: bool = False,
    merge_clients: bool = True,
) -> CredentialSaveResult:
    """Write or bootstrap local Sonar access token into sonar-local.env."""
    local = read_env_file(LOCAL_ENV)
    url_default = (
        url
        or local.get("SONARQUBE_URL")
        or os.environ.get("SONARQUBE_URL")
        or DEFAULT_LOCAL_URL
    )
    project_key = project_key or local.get("SONARQUBE_PROJECT_KEY") or None
    bootstrapped = False

    if bootstrap and not token:
        token = ensure_token(project_key=project_key)
        url = url or DEFAULT_LOCAL_URL
        bootstrapped = True
    elif not (url and token):
        prompted_url, prompted_token, cancelled = _prompt_pair(
            url_default=url_default,
            prefer_cli=prefer_cli,
            reason=(
                "Enter local SonarQube URL and user token "
                f"(saved to {LOCAL_ENV}). Prefer --bootstrap to auto-generate."
            ),
        )
        if cancelled:
            return CredentialSaveResult(
                ok=False,
                url=url_default,
                saved_to=str(LOCAL_ENV),
                backend="local",
                detail="cancelled",
            )
        url = url or prompted_url
        token = token or prompted_token

    if not url or not token:
        return CredentialSaveResult(
            ok=False,
            url=url or "",
            saved_to=str(LOCAL_ENV),
            backend="local",
            detail="URL and token are both required (or use --bootstrap)",
        )

    path = update_local_credentials(url, token, project_key=project_key)
    os.environ["SONARQUBE_URL"] = url
    os.environ["SONARQUBE_TOKEN"] = token
    if project_key:
        os.environ["SONARQUBE_PROJECT_KEY"] = project_key
    if merge_clients:
        merge_mcp_config()
        merge_codex_config()

    result = CredentialSaveResult(
        ok=True,
        url=url,
        saved_to=str(path),
        backend="local",
        bootstrapped=bootstrapped,
        project_key=project_key,
    )
    if validate:
        health = check_server(url, token)
        result.auth_status = health.status.value
        result.detail = health.detail
        result.ok = health.status == AuthStatus.OK
    return result


def credential_paths() -> dict[str, str]:
    return {
        "local_env": str(LOCAL_ENV),
        "remote_env": str(REMOTE_ENV),
        "local_exists": str(LOCAL_ENV.is_file()),
        "remote_exists": str(REMOTE_ENV.is_file()),
    }


def mask_token_present(path: Path) -> bool:
    data = read_env_file(path)
    token = data.get("SONARQUBE_TOKEN", "")
    return len(token) > 8
