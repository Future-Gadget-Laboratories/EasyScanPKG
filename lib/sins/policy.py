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


_FORGIVE_MARKER = re.compile(r"sins:\s*forgive\s+")
_SIN_ID = re.compile(r"[a-z0-9_]+(?:-[a-z0-9_]+)*")


def _parse_forgiveness(comment: str) -> tuple[set[str], str] | None:
    """Split `sins: forgive <id[,id...]> -- <reason>` using only linear-time patterns."""
    marker = _FORGIVE_MARKER.search(comment)
    if marker is None:
        return None
    ids_text, separator, reason = comment[marker.end():].partition("--")
    ids = [item.strip() for item in ids_text.split(",")]
    if not separator or not all(_SIN_ID.fullmatch(item) for item in ids):
        return None
    return set(ids), reason.strip()


def _comment_segments(line: str, language: str) -> list[tuple[str, str]]:
    """Tokenize comment regions while skipping quoted source strings."""
    quoted = ""
    escaped = False
    comments: list[tuple[str, str]] = []
    index = 0
    while index < len(line):
        if quoted:
            quoted, escaped = _advance_quote(line[index], quoted, escaped)
            index += 1
            continue
        char = line[index]
        if char in "\"'" and not _cpp_digit_separator(line, index, language):
            quoted = char
            index += 1
            continue
        segment = _comment_at(line, index, language)
        if segment is not None:
            comment, index, stop = segment
            comments.append(comment)
            if stop:
                break
            continue
        index += 1
    return comments


def _advance_quote(char: str, quoted: str, escaped: bool) -> tuple[str, bool]:
    """Advance one character inside a quoted string."""
    if escaped:
        return quoted, False
    if char == "\\":
        return quoted, True
    return ("", False) if char == quoted else (quoted, False)


def _cpp_digit_separator(line: str, index: int, language: str) -> bool:
    """Recognize a C++ digit separator instead of opening a character literal."""
    return (language == "cpp" and line[index] == "'" and index > 0 and index + 1 < len(line)
            and line[index - 1] in "0123456789abcdefABCDEF"
            and line[index + 1] in "0123456789abcdefABCDEF"
            and (index + 2 >= len(line) or line[index + 2] != "'"))


def _comment_at(line: str, index: int, language: str) -> tuple[tuple[str, str], int, bool] | None:
    """Return a comment segment beginning at index and its scan position."""
    pair = line[index:index + 2]
    if language == "cpp" and pair == "/*":
        end = line.find("*/", index + 2)
        if end < 0:
            return ("block", line[index + 2:]), len(line), True
        return ("block", line[index + 2:end]), end + 2, False
    if language == "cpp" and pair == "//":
        return ("line", line[index + 2:]), len(line), True
    if language != "cpp" and line[index] == "#":
        return ("hash", line[index + 1:]), len(line), True
    return None


def forgiveness_for_line(lines: list[str], line_number: int, language: str,
                         sin_id: str) -> bool:
    """Return whether a comment forgives a finding on its source line."""
    source = lines[line_number - 1]
    if language == "cpp" and _cpp_slash_forgiveness(source):
        return True
    if any(_segment_forgives(comment, sin_id) for _kind, comment in _comment_segments(source, language)):
        return True
    if _is_standalone_comment(source, language):
        return False
    previous = line_number - 2
    while previous >= 0 and not lines[previous].strip():
        previous -= 1
    if previous < 0 or not _is_standalone_comment(lines[previous], language):
        return False
    return any(_segment_forgives(comment, sin_id)
               for _kind, comment in _comment_segments(lines[previous], language))


def _is_standalone_comment(line: str, language: str) -> bool:
    """Return whether a non-blank line contains only comment tokens."""
    rest = line.strip()
    if not rest:
        return False
    if language != "cpp":
        return rest.startswith("#")
    while rest:
        if rest.startswith("//"):
            return True
        if not rest.startswith("/*"):
            return False
        end = rest.find("*/", 2)
        if end < 0:
            return True
        rest = rest[end + 2:].lstrip()
    return True


def _segment_forgives(comment: str, sin_id: str) -> bool:
    """Return whether one comment segment validly forgives sin_id."""
    if not re.search(r"sins:\s*forgive\b", comment):
        return False
    parsed = _parse_forgiveness(comment)
    return bool(parsed and parsed[1] and sin_id in parsed[0])


def _cpp_slash_forgiveness(line: str) -> bool:
    """Recognize Paladin's marker only after a real C++ // comment opener."""
    return any(kind == "line" and "forgive me" in text
               for kind, text in _comment_segments(line, "cpp"))


def malformed_forgiveness(line: str, language: str) -> bool:
    """Return whether a real comment has a forgiveness marker without a reason."""
    for _kind, comment in _comment_segments(line, language):
        if re.search(r"sins:\s*forgive\b", comment):
            parsed = _parse_forgiveness(comment)
            if not parsed or not parsed[1]:
                return True
    return False


