"""Bounded, local-only regex scanner."""

from __future__ import annotations

import fnmatch
import os
import re
import stat
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .policy import context_anchor, fingerprint
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
    fingerprint: str = ""
    is_new: bool = False


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
        return fnmatch.fnmatchcase(path_parts[-1], normalized)
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


def _scan_file(file: Path, relative: str, rule: Rule, matcher: re.Pattern[str],
               global_excludes: tuple[str, ...] = ()) -> list[Finding]:
    """Read one regular, bounded UTF-8 file and emit 1-based findings."""
    file_stat = file.lstat()
    if (not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > MAX_BYTES
            or not _included(relative, rule) or any(_glob_match(relative, item) for item in global_excludes)):
        return []
    with file.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or b"\0" in raw[:8192]:
        return []
    content = raw.decode("utf-8", errors="replace")
    line_starts = [0] + [index + 1 for index, char in enumerate(content) if char == "\n"]
    findings: list[Finding] = []
    for match in matcher.finditer(content):
        start_index = bisect_right(line_starts, match.start()) - 1
        end_index = bisect_right(line_starts, match.end()) - 1
        start_offset = line_starts[start_index]
        end_offset = line_starts[end_index]
        span = match.group(0)
        findings.append(Finding(rule.id, relative, start_index + 1, match.start() - start_offset + 1,
                                end_index + 1, match.end() - end_offset + 1, rule.phrase, rule.severity,
                                fingerprint=fingerprint(rule.id, relative, span,
                                                        context_anchor(content, start_index + 1))))
    return findings


def scan_rule(repo: Path, rule: Rule, global_excludes: tuple[str, ...] = ()) -> list[Finding]:
    """Scan eligible UTF-8 files while pruning skipped directories."""
    findings: list[Finding] = []
    matcher = re.compile(rule.pattern, re.MULTILINE | _regex_flags(rule.flags))
    for directory, subdirectories, filenames in os.walk(repo, topdown=True, onerror=_raise_walk_error):
        base = Path(directory)
        rel_dir = () if base == repo else base.relative_to(repo).parts
        subdirectories[:] = sorted(name for name in subdirectories
                                   if not _is_skipped_path(rel_dir + (name,)))
        for filename in sorted(filenames):
            file = base / filename
            relative = file.relative_to(repo).as_posix()
            findings.extend(_scan_file(file, relative, rule, matcher, global_excludes))
    return findings


def _raise_walk_error(error: OSError) -> None:
    """Propagate traversal errors so a partial scan cannot report clean."""
    raise error
