"""Scan harness: decide which scanners run and which files each one receives.

Every scanner defaults to ``enabled: "auto"``. In auto mode the harness turns a
scanner on when the workspace has files it understands *and* its tool is
installed, and hands it only those files. An explicit ``True``/``False`` from
policy, env, or CLI always wins; explicitly enabled scanners still get routed
paths when the user did not set any.
"""

from __future__ import annotations

import copy
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from scanners._util import find_binary
from scanners.config import AUTO, _parse_bool
from scanners.detect import ProjectProfile

MODE_AUTO = "auto"
MODE_MANUAL = "manual"
INSTALL_HINT = "not installed — run bin/easyscan-install-scanners"

# Scanners whose tool binary is named differently from the scanner id.
_DEFAULT_BINARY = {
    "osv": "osv-scanner",
    "clang-analyzer": "scan-build",
}
# Scanners that need no host binary lookup (compiler runtime / Docker-backed).
_NO_BINARY = {"asan", "ubsan", "sonar"}


@dataclass
class RouteDecision:
    name: str
    enabled: bool
    origin: str  # "auto" | "explicit"
    reason: str
    targets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Route:
    applicable: bool
    reason: str
    updates: dict[str, Any] = field(default_factory=dict)
    targets: list[str] = field(default_factory=list)
    binary_needed: bool = True


SonarProbe = Callable[[Mapping[str, Any]], tuple[bool, str]]


def default_sonar_probe(context: Mapping[str, Any]) -> tuple[bool, str]:
    """Sonar is usable when Docker is present and the server accepts the token."""
    if not shutil.which("docker"):
        return False, "docker not found (sonar-scan runs the scanner in Docker)"
    try:
        from context_resolve import peek_creds
        from server_health import AuthStatus, check_server

        url, token = peek_creds(
            context=context.get("context_name"),
            prefer_local=bool(context.get("prefer_local")),
        )
        health = check_server(url, token, timeout=3.0)
    except Exception as exc:  # noqa: BLE001 — probe must never crash the plan
        return False, f"sonar credentials unavailable: {exc}"
    if health.status != AuthStatus.OK:
        return False, f"sonar server not ready at {url} ({health.detail}) — run sonar-local-up"
    return True, f"server ready at {url}"


def _paths_unset(cfg: Mapping[str, Any]) -> bool:
    return not cfg.get("paths")


def _route_paths(cfg: Mapping[str, Any], targets: list[str], reason: str) -> _Route:
    if _paths_unset(cfg):
        return _Route(True, reason, {"paths": list(targets)}, list(targets))
    return _Route(True, reason, {}, [str(p) for p in cfg.get("paths") or []])


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _route(name: str, cfg: Mapping[str, Any], profile: ProjectProfile) -> _Route:  # noqa: C901
    p = profile
    if name == "sonar":
        if not p.has_code:
            return _Route(False, "no source files detected")
        return _Route(True, f"languages: {', '.join(p.languages())}", binary_needed=False)

    if name == "ruff":
        if not p.has_python:
            return _Route(False, "no Python files")
        n = len(p.python_files) + len(p.python_scripts)
        return _route_paths(cfg, p.python_targets(), _count(n, "Python file"))

    if name == "bandit":
        # Security rules (assert, /tmp, fake passwords) are noise in test code.
        targets = p.python_targets(include_tests=False)
        if not targets:
            return _Route(False, "no non-test Python files" if p.has_python else "no Python files")
        return _route_paths(cfg, targets, f"{_count(len(targets), 'target')} (tests excluded)")

    if name == "shellcheck":
        if not p.shell_files:
            return _Route(False, "no shell scripts")
        return _route_paths(cfg, sorted(p.shell_files), _count(len(p.shell_files), "shell script"))

    if name in {"cppcheck", "flawfinder"}:
        if not p.has_c_family:
            return _Route(False, "no C/C++ files")
        n = len(p.c_files) + len(p.cpp_files)
        return _route_paths(cfg, p.c_family_targets(), _count(n, "C/C++ file"))

    if name == "clang-tidy":
        if not p.has_c_family:
            return _Route(False, "no C/C++ files")
        cc = cfg.get("compile_commands") or p.compile_commands
        if not cc:
            return _Route(
                False,
                "no compile_commands.json (cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON, or --compile-commands)",
            )
        cc_path = Path(str(cc)) if Path(str(cc)).is_absolute() else p.workspace / str(cc)
        if not cc_path.is_file():
            return _Route(False, f"compile database not found: {cc}")
        updates = {} if cfg.get("compile_commands") else {"compile_commands": cc}
        return _Route(True, f"compile database {cc}", updates)

    if name == "clang-analyzer":
        if not p.has_c_family:
            return _Route(False, "no C/C++ files")
        if cfg.get("report_dir") or cfg.get("sarif"):
            return _Route(True, "reading existing analyzer report", binary_needed=False)
        if cfg.get("build_command") or cfg.get("command"):
            return _Route(True, "wrapping configured build_command")
        return _Route(False, "needs build_command, report_dir, or sarif in scan policy")

    if name == "hadolint":
        if not p.dockerfiles:
            return _Route(False, "no Dockerfiles")
        return _route_paths(cfg, sorted(p.dockerfiles), _count(len(p.dockerfiles), "Dockerfile"))

    if name == "semgrep":
        if not p.has_code:
            return _Route(False, "no source files detected")
        return _Route(True, f"languages: {', '.join(p.languages())}", targets=["."])

    if name == "gitleaks":
        if not p.file_count:
            return _Route(False, "workspace is empty")
        return _Route(True, _count(p.file_count, "file"), targets=["."])

    if name == "pip-audit":
        if cfg.get("requirements"):
            return _Route(True, f"auditing {cfg['requirements']}")
        if not p.requirements_files:
            return _Route(False, "no requirements*.txt")
        req = "requirements.txt" if "requirements.txt" in p.requirements_files else p.requirements_files[0]
        return _Route(True, f"auditing {req}", {"requirements": req})

    if name == "osv":
        if not p.dependency_manifests:
            return _Route(False, "no lockfiles or dependency manifests")
        return _Route(
            True,
            _count(len(p.dependency_manifests), "manifest"),
            targets=sorted(p.dependency_manifests),
        )

    if name in {"asan", "ubsan", "valgrind", "drmemory"}:
        if not cfg.get("command"):
            return _Route(False, f"dynamic analysis needs a run command (--{name}-command -- ./binary)")
        return _Route(True, "configured run command", binary_needed=name not in {"asan", "ubsan"})

    return _Route(True, "custom scanner", binary_needed=False)


def _binary_for(name: str, cfg: Mapping[str, Any]) -> str:
    return str(cfg.get("binary") or _DEFAULT_BINARY.get(name) or name)


def plan_scanners(
    config: Mapping[str, Mapping[str, Any]],
    profile: ProjectProfile,
    *,
    mode: str = MODE_AUTO,
    context: Mapping[str, Any] | None = None,
    sonar_probe: SonarProbe | None = None,
) -> tuple[dict[str, dict[str, Any]], list[RouteDecision]]:
    """Resolve ``auto`` flags and route files. Returns (runnable config, decisions)."""
    ctx = context or {}
    probe = sonar_probe or default_sonar_probe
    resolved: dict[str, dict[str, Any]] = {}
    decisions: list[RouteDecision] = []

    for name, raw_cfg in config.items():
        cfg = copy.deepcopy(dict(raw_cfg))
        flag = cfg.get("enabled")
        if isinstance(flag, str) and flag != AUTO:
            parsed = _parse_bool(flag)  # "yes"/"off"/… from sonar-policy set
            flag = AUTO if parsed is None else parsed
        origin = "auto" if flag == AUTO else "explicit"
        if flag == AUTO and mode == MODE_MANUAL:
            # Legacy behaviour: Sonar on, every other tool opt-in.
            flag, origin = name == "sonar", "manual"

        if flag is False or flag is None:
            cfg["enabled"] = False
            resolved[name] = cfg
            decisions.append(RouteDecision(name, False, origin, "disabled"))
            continue

        route = _route(name, cfg, profile)
        cfg.update(route.updates)

        if flag is True:
            cfg["enabled"] = True
            resolved[name] = cfg
            reason = route.reason if route.applicable else f"forced on ({route.reason})"
            decisions.append(RouteDecision(name, True, origin, reason, route.targets))
            continue

        # auto
        enabled, reason = route.applicable, route.reason
        if enabled and name == "sonar":
            enabled, probe_reason = probe(ctx)
            reason = f"{reason}; {probe_reason}"
        elif enabled and route.binary_needed and name not in _NO_BINARY:
            binary = _binary_for(name, cfg)
            if not find_binary(binary):
                enabled, reason = False, f"{INSTALL_HINT} ({binary})"
        cfg["enabled"] = enabled
        resolved[name] = cfg
        decisions.append(RouteDecision(name, enabled, origin, reason, route.targets if enabled else []))

    return resolved, decisions


def format_plan(decisions: list[RouteDecision], profile: ProjectProfile) -> str:
    langs = ", ".join(profile.languages()) or "none"
    lines = [f"Scan plan — {profile.workspace} (languages: {langs})"]
    width = max((len(d.name) for d in decisions), default=8)
    for d in decisions:
        mark = "RUN " if d.enabled else "skip"
        targets = ""
        if d.enabled and d.targets:
            shown = ", ".join(d.targets[:4])
            more = f" (+{len(d.targets) - 4} more)" if len(d.targets) > 4 else ""
            targets = f" → {shown}{more}"
        lines.append(f"  [{mark}] {d.name.ljust(width)}  {d.origin:8}  {d.reason}{targets}")
    return "\n".join(lines)
