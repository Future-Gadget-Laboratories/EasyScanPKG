"""Bounded, local-only regex scanner."""

from __future__ import annotations

import re
import os
from dataclasses import dataclass
from pathlib import Path

from .rules import Rule, _regex_flags

MAX_BYTES = 2 * 1024 * 1024
SKIP_DIRS = {".git", ".sft", "node_modules", "build", "dist", "target", "obj"}


@dataclass(frozen=True)
class Finding:
    """One normalized sin finding with 1-based coordinates."""

    sin: str
    path: str
    line: int
    col: int
    end_line: int
    end_col: int
    message: str
    severity: str
    forgiven: bool = False


def _included(relative: str, rule: Rule) -> bool:
    path = Path(relative)
    def matches(pattern: str) -> bool:
        return path.match(pattern) or (pattern.startswith("**/") and path.match(pattern[3:]))

    return any(matches(pattern) for pattern in rule.include) and not any(
        matches(pattern) for pattern in rule.exclude
    ) and not any(part in SKIP_DIRS for part in path.parts)


def scan_rule(repo: Path, rule: Rule) -> list[Finding]:
    """Scan eligible UTF-8 files while skipping binary and oversized inputs."""
    findings: list[Finding] = []
    matcher = re.compile(rule.pattern, re.MULTILINE | _regex_flags(rule.flags))
    for directory, subdirectories, filenames in os.walk(repo, topdown=True, onerror=_raise_walk_error):
        subdirectories[:] = sorted(name for name in subdirectories if name not in SKIP_DIRS)
        base = Path(directory)
        for filename in sorted(filenames):
            file = base / filename
            if file.is_symlink():
                continue
            relative = file.relative_to(repo).as_posix()
            if not _included(relative, rule):
                continue
            try:
                if file.stat().st_size > MAX_BYTES:
                    continue
                raw = file.read_bytes()
            except OSError:
                raise
            if b"\0" in raw[:8192]:
                continue
            content = raw.decode("utf-8", errors="replace")
            for match in matcher.finditer(content):
                start_line = content.count("\n", 0, match.start()) + 1
                end_line = content.count("\n", 0, match.end()) + 1
                start_offset = content.rfind("\n", 0, match.start())
                end_offset = content.rfind("\n", 0, match.end())
                findings.append(Finding(rule.id, relative, start_line, match.start() - start_offset,
                                        end_line, match.end() - end_offset, rule.phrase, rule.severity))
    return findings


def _raise_walk_error(error: OSError) -> None:
    """Propagate traversal errors so a partial scan cannot report clean."""
    raise error
