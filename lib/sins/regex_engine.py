"""Bounded, local-only regex scanner."""

from __future__ import annotations

import fnmatch
import os
import re
import stat
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .rules import Rule, _regex_flags

MAX_BYTES = 2 * 1024 * 1024
SKIP_DIRS = {".git", ".sft", "node_modules", "build", "dist", "target", "obj"}
SKIP_PATHS = {(".sins",), ("lib", "sins", "rules")}


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


def _glob_match(relative: str, pattern: str) -> bool:
    """Match a root-relative path with recursive ``**`` segments."""
    normalized = pattern.lstrip("/")
    if normalized.endswith("/"):
        normalized += "**"
    normalized = normalized.rstrip("/")
    pattern_parts = tuple(part for part in normalized.split("/") if part)
    path_parts = tuple(part for part in relative.split("/") if part)
    if not pattern_parts:
        return False

    @lru_cache(maxsize=None)
    def matches(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        part = pattern_parts[pattern_index]
        if part == "**":
            return matches(path_index, pattern_index + 1) or (
                path_index < len(path_parts) and matches(path_index + 1, pattern_index)
            )
        if path_index >= len(path_parts):
            return False
        return fnmatch.fnmatchcase(path_parts[path_index], part) and matches(path_index + 1, pattern_index + 1)

    if "/" not in normalized and "**" not in pattern_parts:
        return any(fnmatch.fnmatchcase(part, normalized) for part in path_parts)
    return matches(0, 0)


def _is_skipped_path(parts: tuple[str, ...]) -> bool:
    """Return whether a relative path is in an always-skipped tree."""
    if any(part in SKIP_DIRS for part in parts):
        return True
    return any(parts[:len(prefix)] == prefix for prefix in SKIP_PATHS)


def _included(relative: str, rule: Rule) -> bool:
    parts = tuple(relative.split("/"))
    if _is_skipped_path(parts):
        return False
    return any(_glob_match(relative, pattern) for pattern in rule.include) and not any(
        _glob_match(relative, pattern) for pattern in rule.exclude
    )


def scan_rule(repo: Path, rule: Rule) -> list[Finding]:
    """Scan eligible UTF-8 files while skipping binary and oversized inputs."""
    findings: list[Finding] = []
    matcher = re.compile(rule.pattern, re.MULTILINE | _regex_flags(rule.flags))
    for directory, subdirectories, filenames in os.walk(repo, topdown=True, onerror=_raise_walk_error):
        subdirectories[:] = sorted(name for name in subdirectories if name not in SKIP_DIRS)
        base = Path(directory)
        for filename in sorted(filenames):
            file = base / filename
            file_stat = file.lstat()
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > MAX_BYTES:
                continue
            relative = file.relative_to(repo).as_posix()
            if not _included(relative, rule):
                continue
            with file.open("rb") as handle:
                raw = handle.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES or b"\0" in raw[:8192]:
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
