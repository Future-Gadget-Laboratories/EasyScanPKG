"""Multi-scanner adapters for EasyScanPKG all-in-one scan stage."""

from __future__ import annotations

from scanners.base import Finding, Scanner
from scanners.config import DEFAULT_SCANNER_CONFIG, resolve_scanner_config
from scanners.merge import dedupe_findings, findings_to_issues, merge_findings
from scanners.registry import available_scanners, get_scanner, run_scanners

__all__ = [
    "DEFAULT_SCANNER_CONFIG",
    "Finding",
    "Scanner",
    "available_scanners",
    "dedupe_findings",
    "findings_to_issues",
    "get_scanner",
    "merge_findings",
    "resolve_scanner_config",
    "run_scanners",
]
