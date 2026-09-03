"""Merge and light-dedupe findings across scanners."""

from __future__ import annotations

from typing import Iterable

from scanners.base import Finding


def merge_findings(*batches: Iterable[Finding]) -> list[Finding]:
    out: list[Finding] = []
    for batch in batches:
        out.extend(batch)
    return out


def dedupe_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Drop near-duplicates: same file+line+normalized rule token across sources.

    Prefer keeping the first occurrence (stable order: typically sonar then others).
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[Finding] = []
    for finding in findings:
        rule_token = _rule_token(finding.rule)
        sig = (str(finding.file), str(finding.line), rule_token)
        if sig in seen and rule_token:
            continue
        if rule_token:
            seen.add(sig)
        out.append(finding)
    return out


def _rule_token(rule: str) -> str:
    raw = (rule or "").strip().lower()
    if not raw:
        return ""
    if ":" in raw:
        raw = raw.split(":", 1)[-1]
    return raw.replace("_", "-")


def findings_to_issues(findings: Iterable[Finding]) -> list[dict]:
    return [f.to_issue_dict() for f in findings]
