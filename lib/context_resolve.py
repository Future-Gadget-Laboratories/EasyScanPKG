"""Resolve Sonar credentials from named EasyScanPKG contexts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from env_load import apply_sonar_env
from env_write import CONFIG_DIR, LOCAL_ENV, REMOTE_ENV, read_env_file
from local_server import DEFAULT_LOCAL_URL, ensure_token, is_running, start
from policy_db import AnalysisContext, PolicyStore, resolve_store

LOCALHOST_PREFIXES = ("http://127.0.0.1", "http://localhost")


@dataclass
class ResolvedCreds:
    url: str
    token: str
    project_key: str | None
    context_name: str | None
    org: str | None = None
    prefer_local: bool = False


def _is_local_url(url: str) -> bool:
    return url.startswith(LOCALHOST_PREFIXES)


def _token_from_ref(token_ref: str | None) -> str | None:
    if not token_ref:
        return None
    if token_ref.startswith("env:"):
        return os.environ.get(token_ref[4:]) or None
    path = Path(token_ref).expanduser()
    if not path.is_file():
        return None
    if path.suffix == ".env" or path.name.endswith(".env"):
        return read_env_file(path).get("SONARQUBE_TOKEN")
    text = path.read_text(encoding="utf-8").strip()
    if "=" in text and "SONARQUBE_TOKEN" in text:
        return read_env_file(path).get("SONARQUBE_TOKEN")
    return text.splitlines()[0].strip() if text else None


def ensure_default_contexts(store: PolicyStore | None = None) -> PolicyStore:
    """Seed local/remote contexts from existing singleton env files if missing."""
    store = store or resolve_store()
    store.ensure_schema()
    existing = {c.name for c in store.list_contexts()}
    local = read_env_file(LOCAL_ENV)
    remote = read_env_file(REMOTE_ENV)
    if "local" not in existing:
        store.upsert_context(
            "local",
            url=local.get("SONARQUBE_URL") or DEFAULT_LOCAL_URL,
            token_ref=str(LOCAL_ENV),
            project_key=local.get("SONARQUBE_PROJECT_KEY"),
            tags=["backend:local"],
            activate=store.get_active_context_name() is None,
        )
    if "remote" not in existing and remote.get("SONARQUBE_URL"):
        store.upsert_context(
            "remote",
            url=remote.get("SONARQUBE_URL"),
            token_ref=str(REMOTE_ENV),
            org=remote.get("SONARQUBE_ORG") or remote.get("SONAR_ORGANIZATION"),
            project_key=remote.get("SONARQUBE_PROJECT_KEY"),
            tags=["backend:remote"],
            activate=False,
        )
    return store


def get_context(name: str | None, store: PolicyStore | None = None) -> AnalysisContext | None:
    store = ensure_default_contexts(store)
    if name:
        return store.get_context(name)
    active = store.get_active_context_name()
    if active:
        return store.get_context(active)
    return store.get_context("local")


def _local_forced_creds(project_key: str | None) -> ResolvedCreds:
    if not is_running():
        start(wait=True)
    token = ensure_token()
    local = read_env_file(LOCAL_ENV)
    return ResolvedCreds(
        url=local.get("SONARQUBE_URL") or DEFAULT_LOCAL_URL,
        token=token,
        project_key=project_key
        or local.get("SONARQUBE_PROJECT_KEY")
        or os.environ.get("SONARQUBE_PROJECT_KEY"),
        context_name="local",
        prefer_local=True,
    )


def _creds_from_context(
    ctx: AnalysisContext, project_key: str | None
) -> ResolvedCreds:
    url = ctx.url or DEFAULT_LOCAL_URL
    token = _token_from_ref(ctx.token_ref)
    is_local = _is_local_url(url)
    if is_local:
        if not is_running():
            start(wait=True)
        token = token or ensure_token()
    if not token:
        raise SystemExit(
            f"No token for context '{ctx.name}' (token_ref={ctx.token_ref!r}). "
            "Run sonar-local-up or sonar-credentials --cli / set token_ref."
        )
    return ResolvedCreds(
        url=url,
        token=token,
        project_key=project_key or ctx.project_key or os.environ.get("SONARQUBE_PROJECT_KEY"),
        context_name=ctx.name,
        org=ctx.org,
        prefer_local=is_local,
    )


def _legacy_creds(project_key: str | None) -> ResolvedCreds:
    local = read_env_file(LOCAL_ENV)
    remote = read_env_file(REMOTE_ENV)
    url = (
        os.environ.get("SONARQUBE_URL")
        or remote.get("SONARQUBE_URL")
        or local.get("SONARQUBE_URL")
        or DEFAULT_LOCAL_URL
    )
    token = (
        os.environ.get("SONARQUBE_TOKEN")
        or remote.get("SONARQUBE_TOKEN")
        or local.get("SONARQUBE_TOKEN")
    )
    key = (
        project_key
        or os.environ.get("SONARQUBE_PROJECT_KEY")
        or remote.get("SONARQUBE_PROJECT_KEY")
        or local.get("SONARQUBE_PROJECT_KEY")
    )
    if not token:
        if _is_local_url(url):
            if not is_running():
                start(wait=True)
            return ResolvedCreds(
                url=local.get("SONARQUBE_URL") or DEFAULT_LOCAL_URL,
                token=ensure_token(),
                project_key=key,
                context_name=None,
                prefer_local=True,
            )
        raise SystemExit("No SONARQUBE_TOKEN")
    return ResolvedCreds(
        url=url,
        token=token,
        project_key=key,
        context_name=None,
        prefer_local=_is_local_url(url),
    )


def resolve_creds(
    *,
    context: str | None = None,
    prefer_local: bool = False,
    project_key: str | None = None,
    store: PolicyStore | None = None,
) -> ResolvedCreds:
    """Resolve URL/token/project for CLI use.

    Precedence:
      --context NAME → active context → --local → legacy env cascade
    """
    apply_sonar_env()
    store = ensure_default_contexts(store)

    if prefer_local and not context:
        return _local_forced_creds(project_key)

    ctx = get_context(context, store)
    if ctx is not None:
        return _creds_from_context(ctx, project_key)

    return _legacy_creds(project_key)


def context_env_exports(ctx: AnalysisContext) -> dict[str, str]:
    """Environment variables to apply for scan wrappers."""
    out: dict[str, str] = {}
    if ctx.url:
        out["SONARQUBE_URL"] = ctx.url
    token = _token_from_ref(ctx.token_ref)
    if token:
        out["SONARQUBE_TOKEN"] = token
    if ctx.project_key:
        out["SONARQUBE_PROJECT_KEY"] = ctx.project_key
    if ctx.org:
        out["SONARQUBE_ORG"] = ctx.org
    out["SFT_SONAR_CONTEXT"] = ctx.name
    return out


def default_token_ref_for_url(url: str) -> str:
    if _is_local_url(url):
        return str(LOCAL_ENV)
    return str(REMOTE_ENV)


def config_dir() -> Path:
    return CONFIG_DIR
