"""Named local-only Sonar credential series.

Files live under ~/.config/sft/credentials/ (mode 600). Tokens are never
returned by list/index helpers. URLs must be loopback — this store refuses
remote hosts.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from env_write import CONFIG_DIR, LOCAL_ENV, read_env_file, update_local_credentials

CREDENTIALS_DIR = CONFIG_DIR / "credentials"
INDEX_PATH = CREDENTIALS_DIR / "index.json"
INDEX_SCHEMA = "easyscan.local-credentials/v1"
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Purpose labels minted for a fresh local server. Kinds match Sonar token types.
SERIES_SPECS: tuple[tuple[str, str, str], ...] = (
    ("agent", "USER_TOKEN", "API, MCP, and project admin for local agents"),
    ("scanner", "GLOBAL_ANALYSIS_TOKEN", "SonarScanner analysis only"),
    ("issues", "USER_TOKEN", "List, export, and resolve local issues"),
)


class LocalCredentialError(ValueError):
    """Rejected local credential input (name, URL, or missing token)."""


@dataclass
class StoredCredential:
    name: str
    url: str
    kind: str
    path: Path
    project_key: str | None
    active: bool
    token_present: bool
    purpose: str = ""


def assert_credential_name(name: str) -> str:
    cleaned = (name or "").strip().lower()
    if not _NAME_RE.fullmatch(cleaned):
        raise LocalCredentialError(
            "credential name must match [a-z][a-z0-9-]{0,31}"
        )
    return cleaned


def assert_local_url(url: str) -> str:
    """Accept only http(s) loopback URLs. Remote hosts are refused."""
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in _LOOPBACK_HOSTS:
        raise LocalCredentialError(
            "local credentials accept only loopback URLs "
            "(http://127.0.0.1, http://localhost, or http://[::1])"
        )
    if parsed.username or parsed.password:
        raise LocalCredentialError("put the token in SONARQUBE_TOKEN, not the URL")
    return (url or "").strip()


def _chmod_private(path: Path) -> None:
    os.chmod(path, 0o600)


def _empty_index() -> dict:
    return {"schema": INDEX_SCHEMA, "active": None, "entries": []}


def _read_index() -> dict:
    if not INDEX_PATH.is_file():
        return _empty_index()
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _empty_index()
    if not isinstance(data, dict):
        return _empty_index()
    data.setdefault("schema", INDEX_SCHEMA)
    data.setdefault("active", None)
    data.setdefault("entries", [])
    return data


def _write_index(data: dict) -> None:
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    # Index is metadata only — drop any accidental secret keys.
    safe_entries = []
    for entry in data.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        safe_entries.append(
            {
                "name": entry.get("name"),
                "url": entry.get("url"),
                "kind": entry.get("kind") or "USER_TOKEN",
                "file": entry.get("file"),
                "project_key": entry.get("project_key"),
                "purpose": entry.get("purpose") or "",
            }
        )
    payload = {
        "schema": INDEX_SCHEMA,
        "active": data.get("active"),
        "entries": safe_entries,
    }
    INDEX_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _chmod_private(INDEX_PATH)


def _env_path(name: str) -> Path:
    return CREDENTIALS_DIR / f"{name}.env"


def _token_present(path: Path) -> bool:
    token = read_env_file(path).get("SONARQUBE_TOKEN", "")
    return len(token) > 8


def list_local_credentials() -> list[StoredCredential]:
    """Public catalog. Does not include token values."""
    index = _read_index()
    active = index.get("active")
    found: list[StoredCredential] = []
    for entry in index.get("entries") or []:
        name = str(entry.get("name") or "")
        path = _env_path(name) if name else CREDENTIALS_DIR / str(entry.get("file") or "")
        found.append(
            StoredCredential(
                name=name,
                url=str(entry.get("url") or ""),
                kind=str(entry.get("kind") or "USER_TOKEN"),
                path=path,
                project_key=entry.get("project_key"),
                active=name == active,
                token_present=path.is_file() and _token_present(path),
                purpose=str(entry.get("purpose") or ""),
            )
        )
    return found


def public_catalog() -> dict:
    rows = list_local_credentials()
    active = next((row.name for row in rows if row.active), None)
    return {
        "schema": INDEX_SCHEMA,
        "directory": str(CREDENTIALS_DIR),
        "active": active,
        "local_only": True,
        "entries": [
            {
                "name": row.name,
                "url": row.url,
                "kind": row.kind,
                "project_key": row.project_key,
                "active": row.active,
                "token_present": row.token_present,
                "purpose": row.purpose,
                "path": str(row.path),
            }
            for row in rows
        ],
    }


def store_local_credential(
    name: str,
    url: str,
    token: str,
    *,
    kind: str = "USER_TOKEN",
    project_key: str | None = None,
    purpose: str = "",
    activate: bool = False,
) -> Path:
    """Write one loopback credential. Refuses remote URLs and empty tokens."""
    cleaned = assert_credential_name(name)
    local_url = assert_local_url(url)
    secret = (token or "").strip()
    if len(secret) < 8:
        raise LocalCredentialError("token is missing or too short")

    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    path = _env_path(cleaned)
    lines = [
        "# Local-only Sonar credential. Loopback URL. Never commit.",
        f"SONARQUBE_URL={local_url}",
        f"SONARQUBE_TOKEN={secret}",
        f"SONARQUBE_TOKEN_KIND={kind or 'USER_TOKEN'}",
        "LOCAL_ONLY=1",
    ]
    if project_key:
        lines.append(f"SONARQUBE_PROJECT_KEY={project_key}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _chmod_private(path)

    index = _read_index()
    entries = [e for e in index.get("entries") or [] if e.get("name") != cleaned]
    entries.append(
        {
            "name": cleaned,
            "url": local_url,
            "kind": kind or "USER_TOKEN",
            "file": path.name,
            "project_key": project_key,
            "purpose": purpose,
        }
    )
    entries.sort(key=lambda item: item["name"])
    index["entries"] = entries
    if activate or not index.get("active"):
        index["active"] = cleaned
    _write_index(index)
    if index.get("active") == cleaned:
        _activate_file(cleaned, project_key=project_key)
    return path


def _activate_file(name: str, *, project_key: str | None = None) -> None:
    data = read_env_file(_env_path(name))
    url = data.get("SONARQUBE_URL")
    token = data.get("SONARQUBE_TOKEN")
    if not url or not token:
        raise LocalCredentialError(f"credential '{name}' has no URL/token")
    assert_local_url(url)
    key = project_key or data.get("SONARQUBE_PROJECT_KEY")
    update_local_credentials(url, token, project_key=key)


def use_local_credential(name: str) -> StoredCredential:
    """Point ~/.config/sft/sonar-local.env at a stored local credential."""
    cleaned = assert_credential_name(name)
    index = _read_index()
    match = next((e for e in index.get("entries") or [] if e.get("name") == cleaned), None)
    if match is None or not _env_path(cleaned).is_file():
        raise LocalCredentialError(f"no local credential named '{cleaned}'")
    _activate_file(cleaned, project_key=match.get("project_key"))
    index["active"] = cleaned
    _write_index(index)
    row = next(item for item in list_local_credentials() if item.name == cleaned)
    return row


def load_local_token(name: str) -> str:
    """Return the token for a stored name. Callers must not print it."""
    cleaned = assert_credential_name(name)
    data = read_env_file(_env_path(cleaned))
    token = data.get("SONARQUBE_TOKEN", "")
    if len(token) < 8:
        raise LocalCredentialError(f"no token stored for '{cleaned}'")
    assert_local_url(data.get("SONARQUBE_URL", ""))
    return token


def credential_paths() -> dict[str, str]:
    return {
        "directory": str(CREDENTIALS_DIR),
        "index": str(INDEX_PATH),
        "active_env": str(LOCAL_ENV),
    }
