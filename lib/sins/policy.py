"""Repository policy, forgiveness comments, and stable finding fingerprints."""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .rules import Rule, RuleError


@dataclass(frozen=True)
class Policy:
    """Validated repo-local policy overlay."""

    overrides: dict[str, dict[str, object]]
    excludes: tuple[str, ...]


def load_policy(repo: Path, rules: list[Rule]) -> Policy:
    """Read strict `.sins/policy.toml`, rejecting unknown IDs and fields."""
    path = repo / ".sins" / "policy.toml"
    if not path.exists():
        return Policy({}, ())
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise RuleError(f"{path}: invalid policy: {exc}") from exc
    if set(data) - {"sins", "paths"}:
        raise RuleError(f"{path}: unknown key {sorted(set(data) - {'sins', 'paths'})[0]!r}")
    overrides = data.get("sins", {})
    if not isinstance(overrides, dict):
        raise RuleError(f"{path}: [sins.<id>] entries must be tables")
    validated = _validate_overrides(path, overrides, rules)
    excludes = _validate_global_excludes(path, data.get("paths", {}))
    return Policy(validated, excludes)


def _validate_overrides(path: Path, overrides: dict[str, Any], rules: list[Rule]) -> dict[str, dict[str, object]]:
    """Validate per-sin override IDs and their supported values."""
    known = {rule.id for rule in rules}
    unknown_ids = sorted(set(overrides) - known)
    if unknown_ids:
        raise RuleError(f"{path}: unknown sin id {unknown_ids[0]!r}")
    validated: dict[str, dict[str, object]] = {}
    for sin_id, values in overrides.items():
        if not isinstance(values, dict) or set(values) - {"mortal_threshold", "enabled", "mortal"}:
            raise RuleError(f"{path}: invalid keys for sins.{sin_id}")
        validated[sin_id] = _validate_override_values(path, sin_id, values)
    return validated


def _validate_override_values(path: Path, sin_id: str, values: dict[str, Any]) -> dict[str, object]:
    """Validate threshold and boolean values for one policy entry."""
    item: dict[str, object] = {}
    threshold = values.get("mortal_threshold")
    if "mortal_threshold" in values:
        if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 0:
            raise RuleError(f"{path}: sins.{sin_id}.mortal_threshold must be an integer >= 0")
        item["threshold"] = threshold
    for field in ("enabled", "mortal"):
        if field in values:
            if not isinstance(values[field], bool):
                raise RuleError(f"{path}: sins.{sin_id}.{field} must be boolean")
            item[field] = values[field]
    return item


def _validate_global_excludes(path: Path, paths: object) -> tuple[str, ...]:
    """Validate the global paths.exclude list."""
    if not isinstance(paths, dict) or set(paths) - {"exclude"}:
        raise RuleError(f"{path}: [paths] only accepts exclude")
    excludes = paths.get("exclude", [])
    if not isinstance(excludes, list) or any(not isinstance(value, str) for value in excludes):
        raise RuleError(f"{path}: paths.exclude must be a list of strings")
    return tuple(excludes)


def overlay_rules(rules: list[Rule], policy: Policy) -> list[Rule]:
    """Apply policy values while dropping disabled rules."""
    result = []
    for rule in rules:
        values = policy.overrides.get(rule.id, {})
        if values.get("enabled", True):
            result.append(replace(rule, mortal=values.get("mortal", rule.mortal),
                                  threshold=values.get("threshold", rule.threshold)))
    return result


def normalized(text: str) -> str:
    """Collapse all whitespace for stable fingerprint components."""
    return " ".join(text.split())


def fingerprint(sin_id: str, path: str, span: str, anchor: str) -> str:
    """Hash the stable identity tuple, truncated to 16 hexadecimal chars."""
    payload = "\0".join((sin_id, path, normalized(span), normalized(anchor)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


_FORGIVE = re.compile(r"sins:\s*forgive\s+([a-z0-9,-]+)\s*--\s*(.*)$")


def _comment_text(line: str, language: str) -> str:
    """Return comment portions after skipping quoted strings on a source line."""
    return " ".join(text for _kind, text in _comment_segments(line, language))


def _comment_segments(line: str, language: str) -> list[tuple[str, str]]:
    """Tokenize comment regions while skipping quoted source strings."""
    quoted = ""
    escaped = False
    comments: list[tuple[str, str]] = []
    index = 0
    while index < len(line):
        char = line[index]
        pair = line[index:index + 2]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quoted:
                quoted = ""
            index += 1
            continue
        if char in "\"'`":
            quoted = char
            index += 1
            continue
        if language == "cpp" and pair == "/*":
            end = line.find("*/", index + 2)
            if end < 0:
                comments.append(("block", line[index + 2:]))
                break
            comments.append(("block", line[index + 2:end]))
            index = end + 2
            continue
        if language == "cpp" and pair == "//":
            comments.append(("line", line[index + 2:]))
            break
        if language != "cpp" and char == "#":
            comments.append(("hash", line[index + 1:]))
            break
        index += 1
    return comments


def forgiveness_for_line(lines: list[str], line_number: int, language: str,
                         sin_id: str) -> bool:
    """Return whether a comment forgives a finding on its source line."""
    source = lines[line_number - 1]
    comments = [_comment_text(source, language)]
    previous = line_number - 2
    if previous >= 0 and not lines[previous].strip():
        while previous >= 0 and not lines[previous].strip():
            previous -= 1
    if previous >= 0 and not source.strip().startswith(("#", "//", "/*")):
        comments.append(_comment_text(lines[previous], language))
    for comment in comments:
        if language == "cpp" and _cpp_slash_forgiveness(source):
            return True
        marker = re.search(r"sins:\s*forgive\b", comment)
        if not marker:
            continue
        match = _FORGIVE.search(comment)
        if not match or not match.group(2).strip():
            return False
        ids = {item.strip() for item in match.group(1).split(",")}
        if sin_id in ids:
            return True
    return False


def _cpp_slash_forgiveness(line: str) -> bool:
    """Recognize Paladin's marker only after a real C++ // comment opener."""
    return any(kind == "line" and "forgive me" in text
               for kind, text in _comment_segments(line, "cpp"))


def malformed_forgiveness(line: str, language: str) -> bool:
    """Return whether a real comment has a forgiveness marker without a reason."""
    comment = _comment_text(line, language)
    if not re.search(r"sins:\s*forgive\b", comment):
        return False
    match = _FORGIVE.search(comment)
    return not match or not match.group(2).strip()


