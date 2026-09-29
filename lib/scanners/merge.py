"""Merge and light-dedupe findings across scanners."""

from __future__ import annotations

from typing import Iterable

from scanners.base import Finding

# Normalize overlapping rule families across tools (token after optional prefix).
_ALIAS_TOKENS = {
    "use-after-move": "use-after-move",
    "bugprone-use-after-move": "use-after-move",
    "unaddressable-access": "unaddressable-access",
    "unaddressable": "unaddressable-access",
    "heap-use-after-free": "use-after-free",
    "use-after-free": "use-after-free",
    "nulldereference": "null-dereference",
    "null-dereference": "null-dereference",
    "clang-analyzer-core.nulldereference": "null-dereference",
    "core.nulldereference": "null-dereference",
    "nullpointer": "null-dereference",
    "uninitvar": "uninitialized",
    "uninitdata": "uninitialized",
    "uninitialized-read": "uninitialized",
    "uninitializedvariable": "uninitialized",
    "memleak": "leak",
    "memoryleak": "leak",
    "leak": "leak",
    "doublefree": "double-free",
    "double-free": "double-free",
    "bufferoverrun": "buffer-overflow",
    "arrayindexoutofbounds": "buffer-overflow",
    "bufferaccessoutofbounds": "buffer-overflow",
    "strcpy": "unsafe-copy",
    "strcat": "unsafe-copy",
}


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
    raw = raw.replace("_", "-")
    return _ALIAS_TOKENS.get(raw, raw)


def findings_to_issues(findings: Iterable[Finding]) -> list[dict]:
    return [f.to_issue_dict() for f in findings]
