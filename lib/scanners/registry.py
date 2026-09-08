"""Scanner registry and orchestrated run helper."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scanners.bandit import BanditScanner
from scanners.base import Finding, Scanner
from scanners.clang_analyzer import ClangAnalyzerScanner
from scanners.clang_tidy import ClangTidyScanner
from scanners.cppcheck import CppcheckScanner
from scanners.deps import OsvScanner, PipAuditScanner
from scanners.drmemory import DrMemoryScanner
from scanners.flawfinder import FlawfinderScanner
from scanners.gitleaks import GitleaksScanner
from scanners.hadolint import HadolintScanner
from scanners.ruff import RuffScanner
from scanners.sanitizers import ASanScanner, UBSanScanner
from scanners.semgrep import SemgrepScanner
from scanners.shellcheck import ShellCheckScanner
from scanners.sonar import SonarScanner
from scanners.valgrind import ValgrindScanner

_REGISTRY: dict[str, Scanner] = {
    "sonar": SonarScanner(),
    "clang-tidy": ClangTidyScanner(),
    "drmemory": DrMemoryScanner(),
    "cppcheck": CppcheckScanner(),
    "ruff": RuffScanner(),
    "shellcheck": ShellCheckScanner(),
    "semgrep": SemgrepScanner(),
    "bandit": BanditScanner(),
    "asan": ASanScanner(),
    "ubsan": UBSanScanner(),
    "valgrind": ValgrindScanner(),
    "gitleaks": GitleaksScanner(),
    "pip-audit": PipAuditScanner(),
    "osv": OsvScanner(),
    "flawfinder": FlawfinderScanner(),
    "clang-analyzer": ClangAnalyzerScanner(),
    "hadolint": HadolintScanner(),
}


def available_scanners() -> list[str]:
    return sorted(_REGISTRY)


def get_scanner(name: str) -> Scanner:
    if name not in _REGISTRY:
        raise KeyError(f"unknown scanner: {name}")
    return _REGISTRY[name]


def run_scanners(
    workspace: Path,
    scanner_config: Mapping[str, Mapping[str, Any]],
    *,
    context: Mapping[str, Any] | None = None,
) -> tuple[list[Finding], list[str], dict[str, str]]:
    """Run enabled scanners. Returns (findings, sources_run, sources_skipped)."""
    findings: list[Finding] = []
    sources_run: list[str] = []
    sources_skipped: dict[str, str] = {}
    ctx: dict[str, Any] = dict(context or {})

    for name, cfg in scanner_config.items():
        if not cfg.get("enabled"):
            sources_skipped[name] = "disabled"
            continue
        if name not in _REGISTRY:
            sources_skipped[name] = "unknown-scanner"
            continue
        scanner = _REGISTRY[name]
        try:
            batch = scanner.run(workspace, cfg, context=ctx)
        except Exception as exc:  # noqa: BLE001 — surface as skip with reason
            sources_skipped[name] = f"error: {exc}"
            continue
        sources_run.append(name)
        findings.extend(batch)
    if context is not None and hasattr(context, "update"):
        context.update(ctx)  # type: ignore[arg-type]
    return findings, sources_run, sources_skipped
