"""Load Sonar env without printing secrets."""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_ENV_FILES = (
    Path.home() / ".config" / "sft" / "sonar.env",
    Path.home() / ".config" / "sft" / "sonar-local.env",
    Path.home() / ".config" / "sft" / "sonar-policy" / "sonar.env",
)


def load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    loaded: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            loaded[key] = value
    return loaded


def apply_sonar_env(extra_files: list[Path] | None = None) -> dict[str, str]:
    """Load env files into os.environ if keys are unset. Returns effective sonar-related env."""
    files = list(DEFAULT_ENV_FILES)
    if extra_files:
        files.extend(extra_files)
    for path in files:
        for key, value in load_env_file(path).items():
            os.environ.setdefault(key, value)
    keys = (
        "SONARQUBE_TOKEN",
        "SONARQUBE_URL",
        "SONARQUBE_ORG",
        "SONARQUBE_IDE_PORT",
        "SONARQUBE_PROJECT_KEY",
        "SONARQUBE_TOOLSETS",
        "SONARQUBE_READ_ONLY",
        "SONAR_TOKEN",
        "SONAR_HOST_URL",
        "SONAR_PROJECT_KEY",
    )
    # Alias common CI names into MCP names when MCP ones are missing
    if not os.environ.get("SONARQUBE_TOKEN") and os.environ.get("SONAR_TOKEN"):
        os.environ["SONARQUBE_TOKEN"] = os.environ["SONAR_TOKEN"]
    if not os.environ.get("SONARQUBE_URL") and os.environ.get("SONAR_HOST_URL"):
        os.environ["SONARQUBE_URL"] = os.environ["SONAR_HOST_URL"]
    if not os.environ.get("SONARQUBE_PROJECT_KEY") and os.environ.get("SONAR_PROJECT_KEY"):
        os.environ["SONARQUBE_PROJECT_KEY"] = os.environ["SONAR_PROJECT_KEY"]

    return {k: os.environ[k] for k in keys if os.environ.get(k)}


def connected_mode_available(env: dict[str, str] | None = None) -> bool:
    env = env or apply_sonar_env()
    has_token = bool(env.get("SONARQUBE_TOKEN") or os.environ.get("SONARQUBE_TOKEN"))
    has_url_or_org = bool(
        env.get("SONARQUBE_URL")
        or os.environ.get("SONARQUBE_URL")
        or env.get("SONARQUBE_ORG")
        or os.environ.get("SONARQUBE_ORG")
    )
    return has_token and has_url_or_org


def redact_env(env: dict[str, str]) -> dict[str, str]:
    redacted = {}
    for key, value in env.items():
        if "token" in key.lower() or "password" in key.lower():
            redacted[key] = "***" if value else ""
        else:
            redacted[key] = value
    return redacted
