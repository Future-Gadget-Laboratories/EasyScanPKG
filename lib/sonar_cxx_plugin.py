"""Install and configure SonarOpenCommunity/sonar-cxx on the local Community stack.

Downloads the compatible plugin JAR from GitHub releases into the SonarQube
extensions volume (never vendors the JAR in git), restarts the server, enables
CXX file suffixes, and bootstraps a usable quality profile (default Sonar way
ships with all rules disabled).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docker_util import run_docker
from env_write import LOCAL_ENV, read_env_file
from server_health import wait_for_system_up
from sonar_api import api

DEFAULT_LOCAL_URL = "http://127.0.0.1:9000"
SQ_CONTAINER = "sft-sonarqube"

# Default pin: cxx-2.3.0 tested against Community Build 25.8 / 26.1 (Java 21).
DEFAULT_CXX_TAG = "cxx-2.3.0"
DEFAULT_CXX_JAR = "sonar-cxx-plugin-2.3.0.1496.jar"
DEFAULT_CXX_URL = (
    f"https://github.com/SonarOpenCommunity/sonar-cxx/releases/download/"
    f"{DEFAULT_CXX_TAG}/{DEFAULT_CXX_JAR}"
)
SETTING_CXX_FILE_SUFFIXES = "sonar.cxx.file.suffixes"

# Map major.minor Community Build / Server version prefixes → release tag.
# Falls back to DEFAULT_CXX_TAG when no entry matches.
COMPATIBILITY: list[tuple[str, str]] = [
    ("26.", DEFAULT_CXX_TAG),
    ("25.8", DEFAULT_CXX_TAG),
    ("25.", "cxx-2.2.1"),
    ("24.", "cxx-2.1.1"),
    ("10.", "cxx-2.1.1"),
    ("9.", "cxx-2.0.7"),
]

PLUGIN_CACHE = Path.home() / ".config" / "sft" / "plugins"
CONTAINER_PLUGINS_DIR = "/opt/sonarqube/extensions/plugins"
EASYSCAN_CXX_PROFILE = "EasyScan CXX"
DEFAULT_CXX_SUFFIXES = ".cxx,.cpp,.cc,.c,.hxx,.hpp,.hh,.h"

# Built-in cxx rule repositories (exclude external-tool sensors).
BUILTIN_CXX_REPOS = ("cxx",)


@dataclass
class CxxInstallResult:
    ok: bool
    detail: str
    jar_name: str | None = None
    tag: str | None = None
    profile: str | None = None
    has_cxx: bool = False
    restarted: bool = False


def install_sonar_cxx_enabled() -> bool:
    """Preference: default on; disable with SFT_INSTALL_SONAR_CXX=0/false/no."""
    raw = os.environ.get("SFT_INSTALL_SONAR_CXX", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def is_plugin_jar_name(name: str) -> bool:
    """True only for installable sonar-cxx plugin JARs (never the sslr toolkit)."""
    lower = name.lower()
    if "sslr-toolkit" in lower:
        return False
    return bool(re.match(r"^sonar-cxx-plugin-[\d.]+\.jar$", lower))


def resolve_cxx_tag(server_version: str | None) -> str:
    override = os.environ.get("SFT_SONAR_CXX_VERSION", "").strip()
    if override:
        return override if override.startswith("cxx-") else f"cxx-{override}"
    if not server_version:
        return DEFAULT_CXX_TAG
    ver = server_version.strip()
    for prefix, tag in COMPATIBILITY:
        if ver.startswith(prefix):
            return tag
    return DEFAULT_CXX_TAG


def resolve_plugin_url(server_version: str | None = None) -> tuple[str, str, str]:
    """Return (download_url, jar_filename, release_tag)."""
    env_url = os.environ.get("SFT_SONAR_CXX_PLUGIN_URL", "").strip()
    if env_url:
        jar = Path(urllib.parse.urlparse(env_url).path).name
        if not is_plugin_jar_name(jar):
            raise ValueError(
                f"SFT_SONAR_CXX_PLUGIN_URL must point at sonar-cxx-plugin-*.jar "
                f"(not toolkit): {jar!r}"
            )
        return env_url, jar, resolve_cxx_tag(server_version)

    tag = resolve_cxx_tag(server_version)
    if tag == DEFAULT_CXX_TAG:
        return DEFAULT_CXX_URL, DEFAULT_CXX_JAR, tag

    # Resolve asset from GitHub releases API for non-default tags.
    api_url = (
        f"https://api.github.com/repos/SonarOpenCommunity/sonar-cxx/releases/tags/{tag}"
    )
    req = urllib.request.Request(
        api_url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "EasyScanPKG"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    for asset in data.get("assets", []):
        name = asset.get("name") or ""
        if is_plugin_jar_name(name):
            return asset["browser_download_url"], name, tag
    raise RuntimeError(f"No sonar-cxx-plugin JAR found for release {tag}")


def fetch_server_version(url: str = DEFAULT_LOCAL_URL) -> str | None:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/server/version", timeout=10) as resp:
            return resp.read().decode("utf-8").strip() or None
    except OSError:
        return None


def download_plugin_jar(url: str, jar_name: str, *, cache_dir: Path | None = None) -> Path:
    dest_dir = cache_dir or PLUGIN_CACHE
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / jar_name
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    req = urllib.request.Request(url, headers={"User-Agent": "EasyScanPKG"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        dest.write_bytes(resp.read())
    return dest


def _list_container_plugin_jars() -> list[str]:
    result = run_docker(
        [
            "exec",
            SQ_CONTAINER,
            "bash",
            "-lc",
            f"ls -1 {CONTAINER_PLUGINS_DIR}/sonar-cxx-plugin-*.jar 2>/dev/null || true",
        ]
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def _plugin_already_installed(jar_name: str) -> bool:
    target = f"{CONTAINER_PLUGINS_DIR}/{jar_name}"
    for path in _list_container_plugin_jars():
        if path.endswith(f"/{jar_name}") or path == jar_name or path.endswith(jar_name):
            # Exact path match from ls
            inspect = run_docker(
                ["exec", SQ_CONTAINER, "test", "-f", target]
            )
            if inspect.returncode == 0:
                return True
            # ls may return basename only
            if Path(path).name == jar_name:
                return True
    # Fallback: test -f
    return run_docker(["exec", SQ_CONTAINER, "test", "-f", target]).returncode == 0


def _remove_old_cxx_plugins(*, keep: str) -> None:
    result = run_docker(
        [
            "exec",
            SQ_CONTAINER,
            "bash",
            "-lc",
            f"for f in {CONTAINER_PLUGINS_DIR}/sonar-cxx-plugin-*.jar "
            f"{CONTAINER_PLUGINS_DIR}/cxx-sslr-toolkit-*.jar; do "
            f'[ -e "$f" ] || continue; '
            f'base=$(basename "$f"); '
            f'[ "$base" = "{keep}" ] || rm -f "$f"; '
            f"done",
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout or "failed to prune old cxx plugins")[-300:]
        )


def _docker_cp_into_plugins(local_jar: Path) -> None:
    # Ensure plugins dir exists
    run_docker(["exec", SQ_CONTAINER, "mkdir", "-p", CONTAINER_PLUGINS_DIR])
    dest = f"{SQ_CONTAINER}:{CONTAINER_PLUGINS_DIR}/{local_jar.name}"
    result = run_docker(["cp", str(local_jar), dest])
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout or "docker cp failed")[-300:]
        )
    # Sonar runs as sonarqube user; make readable
    run_docker(
        [
            "exec",
            SQ_CONTAINER,
            "chmod",
            "644",
            f"{CONTAINER_PLUGINS_DIR}/{local_jar.name}",
        ]
    )


def _restart_sonarqube(*, wait: bool = True) -> None:
    result = run_docker(["restart", SQ_CONTAINER])
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout or "docker restart failed")[-300:]
        )
    if wait and not wait_for_system_up(DEFAULT_LOCAL_URL, timeout_s=300.0):
        raise RuntimeError(
            f"local SonarQube did not become UP after cxx plugin install at {DEFAULT_LOCAL_URL}"
        )


def language_has_cxx(url: str, token: str | None = None) -> bool:
    code, data = api(url, token or "", "GET", "/api/languages/list") if token else (0, {})
    if not token:
        # Unauthenticated languages/list usually works
        try:
            with urllib.request.urlopen(
                f"{url.rstrip('/')}/api/languages/list", timeout=10
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                code = 200
        except OSError:
            return False
    if code != 200 or not isinstance(data, dict):
        return False
    keys = {x.get("key") for x in data.get("languages", [])}
    return "cxx" in keys


def _admin_token() -> str:
    from local_server import ensure_token  # lazy: avoid import cycle

    local = read_env_file(LOCAL_ENV)
    return local.get("SONARQUBE_TOKEN") or ensure_token()


def configure_cxx_file_suffixes(url: str, token: str, suffixes: str = DEFAULT_CXX_SUFFIXES) -> None:
    """Enable the cxx language sensor (disabled by default with suffixes=-)."""
    values = [s.strip() for s in suffixes.split(",") if s.strip()]
    code, body = api(
        url,
        token,
        "POST",
        "/api/settings/set",
        form={"key": SETTING_CXX_FILE_SUFFIXES, "value": suffixes},
    )
    if code not in (200, 204):
        code2, body2 = _settings_set_multi(url, token, SETTING_CXX_FILE_SUFFIXES, values)
        if code2 not in (200, 204):
            raise RuntimeError(
                f"failed to set {SETTING_CXX_FILE_SUFFIXES}: "
                f"HTTP {code} {body!r} / {code2} {body2!r}"
            )


def _settings_set_multi(
    url: str, token: str, key: str, values: list[str]
) -> tuple[int, Any]:
    import base64

    qs = urllib.parse.urlencode([("key", key)] + [("values", v) for v in values])
    endpoint = f"{url.rstrip('/')}/api/settings/set?{qs}"
    req = urllib.request.Request(endpoint, data=b"", method="POST")
    req.add_header(
        "Authorization",
        "Basic " + base64.b64encode(f"{token}:".encode()).decode("ascii"),
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _find_profile(url: str, token: str, language: str, name: str) -> dict | None:
    code, data = api(
        url,
        token,
        "GET",
        "/api/qualityprofiles/search",
        query={"language": language},
    )
    if code != 200 or not isinstance(data, dict):
        return None
    for p in data.get("profiles", []):
        if p.get("name") == name and p.get("language") == language:
            return p
    return None


def _copy_profile(url: str, token: str, from_key: str, to_name: str) -> dict | None:
    existing = _find_profile(url, token, "cxx", to_name)
    if existing:
        return existing
    code, data = api(
        url,
        token,
        "POST",
        "/api/qualityprofiles/copy",
        form={"fromKey": from_key, "toName": to_name},
    )
    if code not in (200, 204) or not isinstance(data, dict):
        # Some versions return the profile JSON; others need re-search
        return _find_profile(url, token, "cxx", to_name)
    if data.get("key"):
        return data
    return _find_profile(url, token, "cxx", to_name)


def _activate_rules_page(
    url: str, token: str, profile_key: str, page: int
) -> tuple[int, int]:
    """Activate one page of built-in cxx rules. Returns (activated, total)."""
    code, data = api(
        url,
        token,
        "GET",
        "/api/rules/search",
        query={
            "languages": "cxx",
            "repositories": ",".join(BUILTIN_CXX_REPOS),
            "ps": 500,
            "p": page,
        },
    )
    if code != 200 or not isinstance(data, dict):
        return 0, 0
    activated = 0
    for rule in data.get("rules", []):
        key = rule.get("key")
        if not key or rule.get("isTemplate"):
            continue
        acode, _ = api(
            url,
            token,
            "POST",
            "/api/qualityprofiles/activate_rule",
            form={"key": profile_key, "rule": key},
        )
        if acode in (200, 204):
            activated += 1
    return activated, int(data.get("total", 0))


def _activate_builtin_cxx_rules(url: str, token: str, profile_key: str) -> int:
    """Activate built-in cxx repository rules on the profile. Returns count activated."""
    activated = 0
    page = 1
    while True:
        page_count, total = _activate_rules_page(url, token, profile_key, page)
        activated += page_count
        if total == 0 or page * 500 >= total:
            break
        page += 1
    return activated


def _set_default_profile(url: str, token: str, *, language: str, profile_name: str) -> None:
    # SonarQube expects language + qualityProfile (name), not profile key alone.
    code, body = api(
        url,
        token,
        "POST",
        "/api/qualityprofiles/set_default",
        form={"language": language, "qualityProfile": profile_name},
    )
    if code not in (200, 204):
        raise RuntimeError(f"set_default failed: HTTP {code} {body!r}")


def bootstrap_cxx_quality_profile(
    url: str = DEFAULT_LOCAL_URL, token: str | None = None
) -> str | None:
    """Copy CXX Sonar way → EasyScan CXX, activate built-in rules, set default."""
    tok = token or _admin_token()
    base = url.rstrip("/")
    sonar_way = _find_profile(base, tok, "cxx", "Sonar way")
    if not sonar_way or not sonar_way.get("key"):
        code, data = api(
            base, tok, "GET", "/api/qualityprofiles/search", query={"language": "cxx"}
        )
        if code == 200 and isinstance(data, dict) and data.get("profiles"):
            sonar_way = data["profiles"][0]
        else:
            return None
    profile = _copy_profile(base, tok, sonar_way["key"], EASYSCAN_CXX_PROFILE)
    if not profile or not profile.get("key"):
        return None
    _activate_builtin_cxx_rules(base, tok, profile["key"])
    name = profile.get("name") or EASYSCAN_CXX_PROFILE
    _set_default_profile(base, tok, language="cxx", profile_name=name)
    return name


def _configure_cxx_runtime(url: str, token: str, *, bootstrap_profile: bool) -> str | None:
    configure_cxx_file_suffixes(url, token)
    if bootstrap_profile:
        return bootstrap_cxx_quality_profile(url, token)
    return None


def ensure_cxx_plugin(
    *,
    url: str = DEFAULT_LOCAL_URL,
    force: bool = False,
    bootstrap_profile: bool = True,
) -> CxxInstallResult:
    """Download/install sonar-cxx when enabled; idempotent if JAR already present."""
    if not install_sonar_cxx_enabled() and not force:
        return CxxInstallResult(ok=True, detail="skipped (SFT_INSTALL_SONAR_CXX disabled)")

    from local_server import is_running  # lazy: avoid import cycle

    if not is_running():
        return CxxInstallResult(ok=False, detail="local SonarQube container not running")

    version = fetch_server_version(url)
    try:
        plugin_url, jar_name, tag = resolve_plugin_url(version)
    except (OSError, RuntimeError, ValueError) as exc:
        return CxxInstallResult(ok=False, detail=f"resolve failed: {exc}")

    if not is_plugin_jar_name(jar_name):
        return CxxInstallResult(ok=False, detail=f"refusing non-plugin JAR: {jar_name}")

    restarted = False
    already = _plugin_already_installed(jar_name)
    has = language_has_cxx(url)

    if already and has and not force:
        try:
            token = _admin_token()
            profile = _configure_cxx_runtime(url, token, bootstrap_profile=bootstrap_profile)
            return CxxInstallResult(
                ok=True,
                detail="already installed",
                jar_name=jar_name,
                tag=tag,
                profile=profile,
                has_cxx=True,
            )
        except Exception as exc:  # noqa: BLE001 — surface config errors
            return CxxInstallResult(
                ok=False,
                detail=f"installed but configure failed: {exc}",
                jar_name=jar_name,
                tag=tag,
                has_cxx=has,
            )

    try:
        local_jar = download_plugin_jar(plugin_url, jar_name)
        _remove_old_cxx_plugins(keep=jar_name)
        if not already or force:
            _docker_cp_into_plugins(local_jar)
            _restart_sonarqube(wait=True)
            restarted = True
        token = _admin_token()
        profile = _configure_cxx_runtime(url, token, bootstrap_profile=bootstrap_profile)
        has = language_has_cxx(url, token)
        if not has:
            return CxxInstallResult(
                ok=False,
                detail="plugin copied but language 'cxx' not listed after restart",
                jar_name=jar_name,
                tag=tag,
                profile=profile,
                restarted=restarted,
            )
        return CxxInstallResult(
            ok=True,
            detail="installed" if restarted else "configured",
            jar_name=jar_name,
            tag=tag,
            profile=profile,
            has_cxx=True,
            restarted=restarted,
        )
    except Exception as exc:  # noqa: BLE001
        return CxxInstallResult(
            ok=False,
            detail=str(exc),
            jar_name=jar_name,
            tag=tag,
            restarted=restarted,
        )
