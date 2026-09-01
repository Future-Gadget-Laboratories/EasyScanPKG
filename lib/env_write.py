"""Read/write ~/.config/sft/*.env without exposing secrets in logs."""

from __future__ import annotations

import os
import re
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "sft"
REMOTE_ENV = CONFIG_DIR / "sonar.env"
LOCAL_ENV = CONFIG_DIR / "sonar-local.env"


def read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip("'").strip('"')
    return out


def write_env_file(path: Path, values: dict[str, str], header: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_env_file(path) if path.is_file() else {}
    merged = {**existing, **{k: v for k, v in values.items() if v is not None}}
    lines: list[str] = []
    if header:
        lines.append(header.rstrip())
        lines.append("")
    for key in sorted(merged.keys()):
        if merged[key] == "":
            lines.append(f"{key}=")
        else:
            lines.append(f"{key}={merged[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def update_remote_credentials(url: str | None = None, token: str | None = None, **extra: str) -> Path:
    values: dict[str, str] = {}
    if url is not None:
        values["SONARQUBE_URL"] = url
    if token is not None:
        values["SONARQUBE_TOKEN"] = token
    for k, v in extra.items():
        if v is not None:
            values[k.upper() if not k.startswith("SONAR") else k] = v
    write_env_file(
        REMOTE_ENV,
        values,
        header="# Sonar remote server credentials (user token — never commit)",
    )
    return REMOTE_ENV


def update_local_credentials(url: str, token: str, project_key: str | None = None) -> Path:
    values: dict[str, str] = {"SONARQUBE_URL": url, "SONARQUBE_TOKEN": token}
    if project_key:
        values["SONARQUBE_PROJECT_KEY"] = project_key
    write_env_file(
        LOCAL_ENV,
        values,
        header="# Auto-managed local SonarQube fallback credentials",
    )
    return LOCAL_ENV
