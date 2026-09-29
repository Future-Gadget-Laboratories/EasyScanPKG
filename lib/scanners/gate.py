"""Severity gate: fail a scan when findings reach a threshold."""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping

# Checklist severities (Sonar scale). Every adapter maps its tool's levels onto these,
# e.g. bandit/semgrep MEDIUM → MAJOR, HIGH/ERROR → CRITICAL, LOW → MINOR.
SEVERITY_RANK = {"INFO": 1, "MINOR": 2, "MAJOR": 3, "CRITICAL": 4, "BLOCKER": 5}
_ALIASES = {"LOW": "MINOR", "MEDIUM": "MAJOR", "HIGH": "CRITICAL"}


def parse_threshold(raw: str) -> str:
    """Normalize a CLI/env threshold (accepts low/medium/high aliases)."""
    key = raw.strip().upper()
    key = _ALIASES.get(key, key)
    if key not in SEVERITY_RANK:
        choices = ", ".join([*SEVERITY_RANK, *(a.lower() for a in _ALIASES)])
        raise ValueError(f"unknown severity {raw!r} (choose from: {choices})")
    return key


def gated_issues(issues: Iterable[Mapping], threshold: str) -> list[Mapping]:
    """Open issues at or above ``threshold``."""
    floor = SEVERITY_RANK[threshold]
    return [
        issue
        for issue in issues
        if SEVERITY_RANK.get(str(issue.get("severity") or "").upper(), 0) >= floor
        and str(issue.get("status") or "OPEN").upper() not in {"RESOLVED", "CLOSED"}
    ]


def summarize(issues: Iterable[Mapping]) -> str:
    by_source = Counter(str(i.get("source") or "sonar") for i in issues)
    return ", ".join(f"{src} {n}" for src, n in by_source.most_common())
