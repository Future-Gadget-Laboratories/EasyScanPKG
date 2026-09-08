"""Resolve per-scanner enable/disable and tool-specific settings."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

DEFAULT_SCANNER_CONFIG: dict[str, dict[str, Any]] = {
    "sonar": {
        "enabled": True,
        "run_scan": True,
        "limit": 500,
    },
    "clang-tidy": {
        "enabled": False,
        "compile_commands": None,
        "checks": None,
        "config_file": None,
        "header_filter": None,
        "paths": [],
        "binary": "clang-tidy",
        "timeout_sec": 600,
    },
    "drmemory": {
        "enabled": False,
        "command": None,
        "args": [],
        "cwd": None,
        "binary": "drmemory",
        "timeout_sec": 600,
        "extra_flags": ["-batch", "-brief"],
    },
    "cppcheck": {
        "enabled": False,
        "paths": ["."],
        "std": None,
        "suppressions": None,
        "binary": "cppcheck",
        "timeout_sec": 600,
    },
    "ruff": {
        "enabled": False,
        "paths": ["lib", "bin", "hooks"],
        "select": None,
        "ignore": None,
        "config": None,
        "binary": "ruff",
        "timeout_sec": 300,
    },
    "shellcheck": {
        "enabled": False,
        "paths": [],
        "binary": "shellcheck",
        "timeout_sec": 300,
    },
    "semgrep": {
        "enabled": False,
        "config": "p/default",
        "exclude": [],
        "binary": "semgrep",
        "timeout_sec": 900,
    },
    "bandit": {
        "enabled": False,
        "paths": ["lib", "bin"],
        "skips": None,
        "binary": "bandit",
        "timeout_sec": 300,
    },
    "asan": {
        "enabled": False,
        "command": None,
        "args": [],
        "cwd": None,
        "asan_options": "halt_on_error=0:detect_leaks=1:abort_on_error=0",
        "timeout_sec": 600,
    },
    "ubsan": {
        "enabled": False,
        "command": None,
        "args": [],
        "cwd": None,
        "ubsan_options": "print_stacktrace=1:halt_on_error=0",
        "timeout_sec": 600,
    },
    "valgrind": {
        "enabled": False,
        "command": None,
        "args": [],
        "cwd": None,
        "binary": "valgrind",
        "timeout_sec": 600,
    },
    "gitleaks": {
        "enabled": False,
        "no_git": True,
        "binary": "gitleaks",
        "timeout_sec": 600,
    },
    "pip-audit": {
        "enabled": False,
        "requirements": None,
        "binary": "pip-audit",
        "timeout_sec": 600,
    },
    "osv": {
        "enabled": False,
        "binary": "osv-scanner",
        "timeout_sec": 600,
    },
    "flawfinder": {
        "enabled": False,
        "paths": ["."],
        "minlevel": "1",
        "binary": "flawfinder",
        "timeout_sec": 300,
    },
    "clang-analyzer": {
        "enabled": False,
        "report_dir": None,
        "sarif": None,
        "build_command": None,
        "binary": "scan-build",
        "timeout_sec": 1200,
    },
    "hadolint": {
        "enabled": False,
        "paths": [],
        "binary": "hadolint",
        "timeout_sec": 300,
    },
}

_ENV_ENABLE = {
    "sonar": "EASYSCAN_ENABLE_SONAR",
    "clang-tidy": "EASYSCAN_ENABLE_CLANG_TIDY",
    "drmemory": "EASYSCAN_ENABLE_DRMEMORY",
    "cppcheck": "EASYSCAN_ENABLE_CPPCHECK",
    "ruff": "EASYSCAN_ENABLE_RUFF",
    "shellcheck": "EASYSCAN_ENABLE_SHELLCHECK",
    "semgrep": "EASYSCAN_ENABLE_SEMGREP",
    "bandit": "EASYSCAN_ENABLE_BANDIT",
    "asan": "EASYSCAN_ENABLE_ASAN",
    "ubsan": "EASYSCAN_ENABLE_UBSAN",
    "valgrind": "EASYSCAN_ENABLE_VALGRIND",
    "gitleaks": "EASYSCAN_ENABLE_GITLEAKS",
    "pip-audit": "EASYSCAN_ENABLE_PIP_AUDIT",
    "osv": "EASYSCAN_ENABLE_OSV",
    "flawfinder": "EASYSCAN_ENABLE_FLAWFINDER",
    "clang-analyzer": "EASYSCAN_ENABLE_CLANG_ANALYZER",
    "hadolint": "EASYSCAN_ENABLE_HADOLINT",
}


def _deep_merge_scanners(
    base: Mapping[str, dict[str, Any]],
    overlay: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    out = copy.deepcopy(dict(base))
    for name, cfg in overlay.items():
        if not isinstance(cfg, Mapping):
            continue
        key = str(name)
        if key not in out:
            out[key] = dict(cfg)
        else:
            out[key] = {**out[key], **dict(cfg)}
    return out


def _parse_bool(raw: str | None) -> bool | None:
    if raw is None:
        return None
    val = raw.strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    return None


def load_workspace_scanner_overlay(workspace: Path) -> dict[str, Any]:
    """Read scanners block from .sft/sonar-policy.json or .sft/scan-policy.json."""
    workspace = workspace.resolve()
    for rel in (Path(".sft") / "sonar-policy.json", Path(".sft") / "scan-policy.json"):
        path = workspace / rel
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        scanners = data.get("scanners")
        if isinstance(scanners, dict):
            return scanners
        scan = data.get("scan")
        if isinstance(scan, dict) and isinstance(scan.get("scanners"), dict):
            return scan["scanners"]
    return {}


def load_policy_scanner_overlay(workspace: Path | None = None) -> dict[str, Any]:
    """Best-effort pull of scanners prefs from policy DB + project overlay."""
    try:
        from policy_db import resolve_store
    except ImportError:
        return {}
    try:
        store = resolve_store()
        if workspace is not None:
            snap = store.merge_project_overlay(workspace)
        else:
            snap = store.snapshot()
        scan = snap.get("scan") or {}
        scanners = scan.get("scanners")
        return dict(scanners) if isinstance(scanners, dict) else {}
    except Exception:  # noqa: BLE001 — policy is optional for scan config
        return {}


def _apply_scanners_csv(scanners: dict[str, dict[str, Any]], csv: str) -> None:
    wanted = {part.strip() for part in csv.split(",") if part.strip()}
    for name in scanners:
        scanners[name]["enabled"] = name in wanted


def _apply_env_flags(scanners: dict[str, dict[str, Any]]) -> None:
    for name, env_key in _ENV_ENABLE.items():
        flag = _parse_bool(os.environ.get(env_key))
        if flag is None or name not in scanners:
            continue
        scanners[name]["enabled"] = flag


def _apply_env_tool_paths(scanners: dict[str, dict[str, Any]]) -> None:
    cc = os.environ.get("EASYSCAN_COMPILE_COMMANDS")
    if cc and "clang-tidy" in scanners:
        scanners["clang-tidy"]["compile_commands"] = cc
    cmd = os.environ.get("EASYSCAN_DRMEMORY_COMMAND")
    if cmd and "drmemory" in scanners:
        scanners["drmemory"]["command"] = cmd
    for key, scanner, field in (
        ("EASYSCAN_ASAN_COMMAND", "asan", "command"),
        ("EASYSCAN_UBSAN_COMMAND", "ubsan", "command"),
        ("EASYSCAN_VALGRIND_COMMAND", "valgrind", "command"),
        ("EASYSCAN_SEMGREP_CONFIG", "semgrep", "config"),
    ):
        val = os.environ.get(key)
        if val and scanner in scanners:
            scanners[scanner][field] = val


def apply_env_overrides(scanners: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = copy.deepcopy(scanners)
    csv = os.environ.get("EASYSCAN_SCANNERS")
    if csv:
        _apply_scanners_csv(out, csv)
    _apply_env_flags(out)
    _apply_env_tool_paths(out)
    return out


def _apply_only_filter(scanners: dict[str, dict[str, Any]], only: Sequence[str]) -> None:
    wanted = {s.strip() for s in only if s.strip()}
    for name in scanners:
        scanners[name]["enabled"] = name in wanted


def _apply_enable_list(scanners: dict[str, dict[str, Any]], enable: Sequence[str]) -> None:
    for name in enable:
        key = name.strip()
        if key in scanners:
            scanners[key]["enabled"] = True
        else:
            scanners[key] = {"enabled": True}


def _apply_disable_list(scanners: dict[str, dict[str, Any]], disable: Sequence[str]) -> None:
    for name in disable:
        key = name.strip()
        if key in scanners:
            scanners[key]["enabled"] = False


def _set_run_command(
    scanners: dict[str, dict[str, Any]],
    name: str,
    command: Sequence[str] | None,
) -> None:
    if command is None or name not in scanners:
        return
    cmd_list = [str(x) for x in command]
    if not cmd_list:
        return
    scanners[name]["command"] = cmd_list[0] if len(cmd_list) == 1 else list(cmd_list)
    if len(cmd_list) > 1 and isinstance(scanners[name].get("command"), str):
        scanners[name]["command"] = cmd_list[0]
        scanners[name]["args"] = cmd_list[1:]
    elif len(cmd_list) > 1:
        scanners[name]["command"] = cmd_list
        scanners[name]["args"] = []


def _apply_tool_cli_overrides(
    scanners: dict[str, dict[str, Any]],
    *,
    clang_tidy_compile_commands: str | None,
    drmemory_command: Sequence[str] | None,
    asan_command: Sequence[str] | None = None,
    ubsan_command: Sequence[str] | None = None,
    valgrind_command: Sequence[str] | None = None,
) -> None:
    if clang_tidy_compile_commands and "clang-tidy" in scanners:
        scanners["clang-tidy"]["compile_commands"] = clang_tidy_compile_commands
    _set_run_command(scanners, "drmemory", drmemory_command)
    _set_run_command(scanners, "asan", asan_command)
    _set_run_command(scanners, "ubsan", ubsan_command)
    _set_run_command(scanners, "valgrind", valgrind_command)


def apply_cli_overrides(
    scanners: dict[str, dict[str, Any]],
    *,
    enable: Sequence[str] | None = None,
    disable: Sequence[str] | None = None,
    only: Sequence[str] | None = None,
    clang_tidy_compile_commands: str | None = None,
    drmemory_command: Sequence[str] | None = None,
    asan_command: Sequence[str] | None = None,
    ubsan_command: Sequence[str] | None = None,
    valgrind_command: Sequence[str] | None = None,
) -> dict[str, dict[str, Any]]:
    out = copy.deepcopy(scanners)
    if only is not None:
        _apply_only_filter(out, only)
    if enable:
        _apply_enable_list(out, enable)
    if disable:
        _apply_disable_list(out, disable)
    _apply_tool_cli_overrides(
        out,
        clang_tidy_compile_commands=clang_tidy_compile_commands,
        drmemory_command=drmemory_command,
        asan_command=asan_command,
        ubsan_command=ubsan_command,
        valgrind_command=valgrind_command,
    )
    return out


def resolve_scanner_config(
    workspace: Path | None = None,
    *,
    enable: list[str] | None = None,
    disable: list[str] | None = None,
    only: list[str] | None = None,
    clang_tidy_compile_commands: str | None = None,
    drmemory_command: list[str] | None = None,
    asan_command: list[str] | None = None,
    ubsan_command: list[str] | None = None,
    valgrind_command: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Merge defaults ← policy ← workspace overlay ← env ← CLI."""
    cfg = copy.deepcopy(DEFAULT_SCANNER_CONFIG)
    cfg = _deep_merge_scanners(cfg, load_policy_scanner_overlay(workspace))
    if workspace is not None:
        cfg = _deep_merge_scanners(cfg, load_workspace_scanner_overlay(workspace))
    cfg = apply_env_overrides(cfg)
    cfg = apply_cli_overrides(
        cfg,
        enable=enable,
        disable=disable,
        only=only,
        clang_tidy_compile_commands=clang_tidy_compile_commands,
        drmemory_command=drmemory_command,
        asan_command=asan_command,
        ubsan_command=ubsan_command,
        valgrind_command=valgrind_command,
    )
    return cfg


def enabled_scanner_names(config: Mapping[str, Mapping[str, Any]]) -> list[str]:
    return [name for name, cfg in config.items() if cfg.get("enabled")]


def skipped_scanner_reasons(config: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    return {name: "disabled" for name, cfg in config.items() if not cfg.get("enabled")}
