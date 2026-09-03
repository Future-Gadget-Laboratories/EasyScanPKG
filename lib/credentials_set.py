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

_MSG_URL_TOKEN_REQUIRED = "URL and token are both required"
_MSG_URL_TOKEN_OR_BOOTSTRAP = "URL and token are both required (or use --bootstrap)"


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


def _apply_validation(result: CredentialSaveResult, url: str, token: str) -> CredentialSaveResult:
    health = check_server(url, token)
    result.auth_status = health.status.value
    result.detail = health.detail
    result.ok = health.status == AuthStatus.OK
    return result


def _export_credentials_env(
    *,
    url: str,
    token: str,
    project_key: str | None = None,
    merge_clients: bool = True,
) -> None:
    os.environ["SONARQUBE_URL"] = url
    os.environ["SONARQUBE_TOKEN"] = token
    if project_key:
        os.environ["SONARQUBE_PROJECT_KEY"] = project_key
    if merge_clients:
        merge_mcp_config()
        merge_codex_config()


def _resolve_url_and_token(
    *,
    url: str | None,
    token: str | None,
    url_default: str,
    prefer_cli: bool,
    reason: str,
    backend: str,
    saved_to: str,
) -> tuple[str | None, str | None, CredentialSaveResult | None]:
    """Prompt when needed. Returns (url, token, error_result_or_None)."""
    if url and token:
        return url, token, None

    prompted_url, prompted_token, cancelled = _prompt_pair(
        url_default=url_default,
        prefer_cli=prefer_cli,
        reason=reason,
    )
    if cancelled:
        return None, None, CredentialSaveResult(
            ok=False,
            url=url_default,
            saved_to=saved_to,
            backend=backend,
            detail="cancelled",
        )
    resolved_url = url or prompted_url
    resolved_token = token or prompted_token
    if not resolved_url or not resolved_token:
        return None, None, CredentialSaveResult(
            ok=False,
            url=resolved_url or "",
            saved_to=saved_to,
            backend=backend,
            detail=_MSG_URL_TOKEN_REQUIRED,
        )
    return resolved_url, resolved_token, None


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
    url, token, err = _resolve_url_and_token(
        url=url,
        token=token,
        url_default=url_default,
        prefer_cli=prefer_cli,
        reason="Enter remote SonarQube URL and user token.",
        backend="remote",
        saved_to=str(REMOTE_ENV),
    )
    if err:
        return err
    assert url and token

    path = update_remote_credentials(url=url, token=token)
    _export_credentials_env(url=url, token=token, merge_clients=merge_clients)

    result = CredentialSaveResult(
        ok=True, url=url, saved_to=str(path), backend="remote"
    )
    if validate:
        return _apply_validation(result, url, token)
    return result


def _resolve_local_access(
    *,
    url: str | None,
    token: str | None,
    url_default: str,
    project_key: str | None,
    bootstrap: bool,
    prefer_cli: bool,
) -> tuple[str | None, str | None, bool, CredentialSaveResult | None]:
    """Return (url, token, bootstrapped, error_result_or_None)."""
    if bootstrap and not token:
        return (
            url or DEFAULT_LOCAL_URL,
            ensure_token(project_key=project_key),
            True,
            None,
        )

    url, token, err = _resolve_url_and_token(
        url=url,
        token=token,
        url_default=url_default,
        prefer_cli=prefer_cli,
        reason=(
            "Enter local SonarQube URL and user token "
            f"(saved to {LOCAL_ENV}). Prefer --bootstrap to auto-generate."
        ),
        backend="local",
        saved_to=str(LOCAL_ENV),
    )
    if err and err.detail == _MSG_URL_TOKEN_REQUIRED:
        err.detail = _MSG_URL_TOKEN_OR_BOOTSTRAP
    return url, token, False, err


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

    url, token, bootstrapped, err = _resolve_local_access(
        url=url,
        token=token,
        url_default=url_default,
        project_key=project_key,
        bootstrap=bootstrap,
        prefer_cli=prefer_cli,
    )
    if err:
        return err
    assert url and token

    path = update_local_credentials(url, token, project_key=project_key)
    _export_credentials_env(
        url=url, token=token, project_key=project_key, merge_clients=merge_clients
    )

    result = CredentialSaveResult(
        ok=True,
        url=url,
        saved_to=str(path),
        backend="local",
        bootstrapped=bootstrapped,
        project_key=project_key,
    )
    if validate:
        return _apply_validation(result, url, token)
    return result


def credential_paths() -> dict[str, str]:
    from local_server import ADMIN_STATE

    return {
        "local_env": str(LOCAL_ENV),
        "remote_env": str(REMOTE_ENV),
        "local_exists": str(LOCAL_ENV.is_file()),
        "remote_exists": str(REMOTE_ENV.is_file()),
        "admin_state": str(ADMIN_STATE),
        "admin_exists": str(ADMIN_STATE.is_file()),
    }


def mask_token_present(path: Path) -> bool:
    data = read_env_file(path)
    token = data.get("SONARQUBE_TOKEN", "")
    return len(token) > 8
