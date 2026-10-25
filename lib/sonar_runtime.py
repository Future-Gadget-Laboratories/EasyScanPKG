"""Runtime knobs for the local SonarQube stack: memory, autostart, on-demand.

Settings come from (highest first) explicit arguments such as sonar-local-up
flags, the process environment, then the optional non-secret settings file
``~/.config/sft/sonar-runtime.env`` (KEY=VALUE lines). No credentials live there.

* ``SFT_SONAR_MEM_PROFILE``  low (default) | normal | high
* ``SFT_SONAR_CXX_SET_DEFAULT``  1 (default) | 0: never change the cxx default profile
* ``SFT_SONAR_MEM_LIMIT``    container memory cap, e.g. ``3g`` or ``3072m``;
  JVM heaps are fitted inside it when the profile's heaps do not fit.
* ``SFT_SONAR_AUTOSTART``    1 (default, restart: unless-stopped) | 0 (restart: "no")
* ``SFT_SONAR_ON_DEMAND``    0 (default) | 1: easyscan-scan / easyscan-check
  --require-local start the stack, hold a lease, and stop it when the last
  lease is released (see sonar_lease).

Restart policy and memory are applied when the container is created. Compose
recreates sft-sonarqube by itself when they change (volumes are kept).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

SETTINGS_FILE = Path.home() / ".config" / "sft" / "sonar-runtime.env"

DEFAULT_PROFILE = "low"
MIN_LIMIT_MB = 1536
# Non-heap budget for the three JVMs (metaspace, threads, code cache, direct
# buffers) plus the launcher process.
JVM_OVERHEAD_MB = 768


@dataclass(frozen=True)
class MemoryProfile:
    limit_mb: int
    web_mb: int
    ce_mb: int
    search_mb: int
    scanner_mb: int

    @property
    def heap_mb(self) -> int:
        return self.web_mb + self.ce_mb + self.search_mb


# low is sized for an 8 GB host running other work: SonarQube (with sonar-cxx)
# capped at 2.5 GB, a 1 GB scanner JVM, and room left for the OS and editors.
PROFILES: dict[str, MemoryProfile] = {
    "low": MemoryProfile(limit_mb=2560, web_mb=512, ce_mb=640, search_mb=512, scanner_mb=1024),
    "normal": MemoryProfile(limit_mb=4096, web_mb=768, ce_mb=1024, search_mb=1024, scanner_mb=2048),
    "high": MemoryProfile(limit_mb=6144, web_mb=1024, ce_mb=2048, search_mb=1536, scanner_mb=3072),
}

_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?) ?([kmgt]?)(?:ib|b)?", re.IGNORECASE)


def parse_size_mb(text: str) -> int:
    """'3g' -> 3072, '2560m' -> 2560, '2560' -> 2560 (MB). Raises ValueError."""
    match = _SIZE_RE.fullmatch((text or "").strip())
    if not match:
        raise ValueError(f"invalid memory size {text!r} (use e.g. 3g or 3072m)")
    value = float(match.group(1))
    unit = match.group(2).lower()
    factor = {"k": 1 / 1024, "": 1, "m": 1, "g": 1024, "t": 1024 * 1024}[unit]
    return int(value * factor)


def read_settings(path: Path | None = None) -> dict[str, str]:
    path = path or SETTINGS_FILE
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def setting(name: str, default: str = "", *, settings: dict[str, str] | None = None) -> str:
    if os.environ.get(name, "").strip():
        return os.environ[name].strip()
    values = read_settings() if settings is None else settings
    return values.get(name, default).strip() or default


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def autostart_enabled(*, settings: dict[str, str] | None = None) -> bool:
    return _truthy(setting("SFT_SONAR_AUTOSTART", "1", settings=settings))


def on_demand_enabled(*, settings: dict[str, str] | None = None) -> bool:
    return _truthy(setting("SFT_SONAR_ON_DEMAND", "0", settings=settings))


def cxx_set_default_enabled(*, settings: dict[str, str] | None = None) -> bool:
    """False when SFT_SONAR_CXX_SET_DEFAULT=0: never change the cxx default profile."""
    return _truthy(setting("SFT_SONAR_CXX_SET_DEFAULT", "1", settings=settings))


def restart_policy(*, settings: dict[str, str] | None = None) -> str:
    return "unless-stopped" if autostart_enabled(settings=settings) else "no"


def _round64(mb: float) -> int:
    return max(256, int(mb) // 64 * 64)


def fit_heaps(limit_mb: int) -> MemoryProfile:
    """Split what the cap leaves after JVM overhead: web 30%, CE 40%, search 30%."""
    heap = max(limit_mb - JVM_OVERHEAD_MB, 768)
    return MemoryProfile(
        limit_mb=limit_mb,
        web_mb=_round64(heap * 0.3),
        ce_mb=_round64(heap * 0.4),
        search_mb=_round64(heap * 0.3),
        scanner_mb=1024 if limit_mb < 4096 else 2048,
    )


def resolve_memory(
    profile: str | None = None,
    limit: str | None = None,
    *,
    settings: dict[str, str] | None = None,
) -> MemoryProfile:
    """Profile table, optionally overridden by an explicit container cap."""
    name = (profile or setting("SFT_SONAR_MEM_PROFILE", DEFAULT_PROFILE, settings=settings)).lower()
    if name not in PROFILES:
        raise ValueError(f"SFT_SONAR_MEM_PROFILE must be one of {', '.join(PROFILES)}; got {name!r}")
    base = PROFILES[name]
    raw_limit = limit or setting("SFT_SONAR_MEM_LIMIT", "", settings=settings)
    if not raw_limit:
        return base
    limit_mb = parse_size_mb(raw_limit)
    if limit_mb < MIN_LIMIT_MB:
        raise ValueError(f"SFT_SONAR_MEM_LIMIT {raw_limit!r} is below the {MIN_LIMIT_MB}m minimum")
    if base.heap_mb + JVM_OVERHEAD_MB <= limit_mb:
        return MemoryProfile(limit_mb, base.web_mb, base.ce_mb, base.search_mb, base.scanner_mb)
    return fit_heaps(limit_mb)


def _java_opts(heap_mb: int, *, fixed: bool = False) -> str:
    xms = heap_mb if fixed else min(128, heap_mb)
    return f"-Xmx{heap_mb}m -Xms{xms}m"


def compose_env(mem: MemoryProfile, *, settings: dict[str, str] | None = None) -> dict[str, str]:
    """Variables interpolated by docker/docker-compose.sonar-local.yml."""
    return {
        "SFT_SQ_MEM_LIMIT": f"{mem.limit_mb}m",
        # memswap == mem: the container never swaps (the host was thrashing).
        "SFT_SQ_MEMSWAP_LIMIT": f"{mem.limit_mb}m",
        "SFT_SQ_WEB_JAVAOPTS": _java_opts(mem.web_mb),
        "SFT_SQ_CE_JAVAOPTS": _java_opts(mem.ce_mb),
        # Elasticsearch wants Xms == Xmx.
        "SFT_SQ_SEARCH_JAVAOPTS": _java_opts(mem.search_mb, fixed=True),
        "SFT_SQ_RESTART": restart_policy(settings=settings),
    }


def native_run_args(mem: MemoryProfile, *, settings: dict[str, str] | None = None) -> list[str]:
    """docker run flags equivalent to compose_env (for hosts without compose)."""
    env = compose_env(mem, settings=settings)
    return [
        "--memory",
        env["SFT_SQ_MEM_LIMIT"],
        "--memory-swap",
        env["SFT_SQ_MEMSWAP_LIMIT"],
        "-e",
        f"SONAR_WEB_JAVAOPTS={env['SFT_SQ_WEB_JAVAOPTS']}",
        "-e",
        f"SONAR_CE_JAVAOPTS={env['SFT_SQ_CE_JAVAOPTS']}",
        "-e",
        f"SONAR_SEARCH_JAVAOPTS={env['SFT_SQ_SEARCH_JAVAOPTS']}",
    ]


def scanner_java_opts(*, settings: dict[str, str] | None = None) -> str:
    """Default SONAR_SCANNER_JAVA_OPTS for sonar-scan under the active profile."""
    try:
        mem = resolve_memory(settings=settings)
    except ValueError:
        mem = PROFILES[DEFAULT_PROFILE]
    return f"-Xmx{mem.scanner_mb}m"


def describe(mem: MemoryProfile, *, settings: dict[str, str] | None = None) -> str:
    return (
        f"mem_limit={mem.limit_mb}m (no swap) heaps web={mem.web_mb}m ce={mem.ce_mb}m "
        f"search={mem.search_mb}m scanner={mem.scanner_mb}m restart={restart_policy(settings=settings)}"
    )
