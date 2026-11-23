"""Strict TOML rule loading and validation."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ENGINES = {"regex", "ast-grep", "opengrep", "paladin", "external"}
SEVERITIES = {"info", "minor", "major", "critical", "blocker"}
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class RuleError(ValueError):
    """Invalid rule pack or unsupported selected engine."""


@dataclass(frozen=True)
class Rule:
    """Validated sin rule and its source path."""

    id: str
    version: int
    phrase: str
    description: str
    language: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    severity: str
    mortal: bool
    threshold: int | None
    engine: str
    pattern: str
    flags: str
    fix_kind: str | None
    template: str | None
    fix_hint: str | None
    bad: tuple[str, ...]
    good: tuple[str, ...]
    fixed: tuple[str, ...]
    tags: tuple[str, ...]
    superseded_by: str | None
    source: Path


def _fail(path: Path, field: str, message: str) -> RuleError:
    return RuleError(f"{path}: {field}: {message}")


def _strings(data: Any, path: Path, field: str, *, required: bool = False) -> tuple[str, ...]:
    if data is None and not required:
        return ()
    if not isinstance(data, list) or (required and not data) or any(not isinstance(item, str) for item in data):
        raise _fail(path, field, "must be a non-empty list of strings" if required else "must be a list of strings")
    return tuple(data)


_REQUIRED_FIELDS: dict[str, type] = {
    "id": str, "version": int, "phrase": str, "description": str,
    "language": str, "severity": str, "mortal": bool, "engine": str,
}


def _validate_scalars(data: dict[str, Any], path: Path) -> tuple[int | None, str | None]:
    for key, kind in _REQUIRED_FIELDS.items():
        value = data.get(key)
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise _fail(path, key, f"is required and must be {kind.__name__}")
    phrase = data["phrase"].strip()
    checks = (
        ("id", not ID_PATTERN.fullmatch(data["id"]), "must be kebab-case"),
        ("version", data["version"] < 1, "must be >= 1"),
        ("description", not data["description"].strip(), "must not be empty"),
        ("phrase", not phrase or phrase.lower().startswith("thou shalt not"),
         "must be non-empty and omit the prefix 'Thou shalt not'"),
        ("language", not re.fullmatch(r"[a-z][a-z0-9_-]*", data["language"]),
         "must be one language identifier string"),
        ("severity", data["severity"] not in SEVERITIES,
         "must be info, minor, major, critical, or blocker"),
        ("engine", data["engine"] not in ENGINES, f"unknown engine {data['engine']!r}"),
    )
    for field, failed, message in checks:
        if failed:
            raise _fail(path, field, message)
    threshold = data.get("mortal_threshold")
    if threshold is not None and (not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 0):
        raise _fail(path, "mortal_threshold", "must be an integer >= 0")
    superseded_by = data.get("superseded_by")
    if superseded_by is not None and not isinstance(superseded_by, str):
        raise _fail(path, "superseded_by", "must be a string")
    return threshold, superseded_by


def _sections(data: dict[str, Any], path: Path) -> tuple[dict[str, Any], ...]:
    sections = tuple(data.get(name, {}) for name in ("paths", "match", "fix", "examples"))
    if not all(isinstance(item, dict) for item in sections):
        raise _fail(path, "sections", "paths, match, fix, and examples must be tables")
    allowed_root = set(_REQUIRED_FIELDS) | {
        "mortal_threshold", "paths", "match", "fix", "examples", "tags", "superseded_by"
    }
    unknown = sorted(set(data) - allowed_root)
    if unknown:
        raise _fail(path, unknown[0], "unknown field")
    paths, match, fix, examples = sections
    allowed = [("paths", paths, {"include", "exclude"}),
               ("fix", fix, {"kind", "template", "fix_hint"}),
               ("examples", examples, {"bad", "good", "fixed"})]
    if data["engine"] == "regex":
        allowed.append(("match", match, {"pattern", "flags"}))
    for section, values, fields in allowed:
        extra = sorted(set(values) - fields)
        if extra:
            raise _fail(path, f"{section}.{extra[0]}", "unknown field")
    return sections


def _validate_fix(fix: dict[str, Any], path: Path) -> tuple[str | None, str | None, str | None]:
    kind, template = fix.get("kind"), fix.get("template")
    if kind is not None and (not isinstance(kind, str) or kind not in {"template", "rewrite"}):
        raise _fail(path, "fix.kind", "must be template or rewrite")
    if kind and (not isinstance(template, str) or not template):
        raise _fail(path, "fix.template", "is required when fix.kind is set")
    if not kind and template is not None:
        raise _fail(path, "fix.kind", "is required when fix.template is set")
    hint = fix.get("fix_hint")
    if hint is not None and not isinstance(hint, str):
        raise _fail(path, "fix.fix_hint", "must be a string")
    return kind, template, hint


def _validate_regex_match(engine: str, match: dict[str, Any], path: Path) -> tuple[str, str]:
    if engine != "regex":
        return "", ""
    pattern, flags = match.get("pattern"), match.get("flags", "")
    if not isinstance(pattern, str) or not pattern or not isinstance(flags, str):
        raise _fail(path, "match", "regex requires pattern and optional string flags")
    try:
        compiled_flags = re.MULTILINE | _regex_flags(flags)
    except RuleError as exc:
        raise _fail(path, "match.flags", str(exc)) from exc
    try:
        re.compile(pattern, compiled_flags)
    except re.error as exc:
        raise _fail(path, "match.pattern", str(exc)) from exc
    return pattern, flags


def load_rule(path: Path) -> Rule:
    """Parse and validate one TOML sin rule."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise _fail(path, "file", str(exc)) from exc
    threshold, superseded_by = _validate_scalars(data, path)
    rule_id = data["id"]
    paths, match, fix, examples = _sections(data, path)
    include = _strings(paths.get("include", ["**/*"]), path, "paths.include")
    exclude = _strings(paths.get("exclude", []), path, "paths.exclude")
    bad = _strings(examples.get("bad"), path, "examples.bad", required=True)
    good = _strings(examples.get("good"), path, "examples.good", required=True)
    fixed = _strings(examples.get("fixed"), path, "examples.fixed")
    fix_kind, template, fix_hint = _validate_fix(fix, path)
    if fix_kind == "rewrite" and (len(fixed) != len(bad) or not fixed):
        raise _fail(path, "examples.fixed", "rewrite requires one fixed example per bad example")
    if fix_kind != "rewrite" and "fixed" in examples:
        raise _fail(path, "examples.fixed", "is only allowed for rewrite fixes")
    pattern, flags = _validate_regex_match(data["engine"], match, path)
    return Rule(rule_id, data["version"], data["phrase"], data["description"], data["language"],
                include, exclude, data["severity"], data["mortal"], threshold, data["engine"], pattern,
                flags, fix_kind, template, fix_hint, bad, good, fixed,
                _strings(data.get("tags"), path, "tags"), superseded_by, path)


def _regex_flags(value: str) -> int:
    """Translate a compact set of Python re flag names."""
    mapping = {"IGNORECASE": re.IGNORECASE, "DOTALL": re.DOTALL, "ASCII": re.ASCII}
    flags = 0
    for name in filter(None, re.split(r"[,| ]+", value.upper())):
        if name not in mapping:
            raise RuleError(f"unsupported regex flag {name!r}")
        flags |= mapping[name]
    return flags


def _rule_sources(repo: Path, extra_dirs: list[Path], builtin: Path) -> list[tuple[Path, bool]]:
    """Build the ordered built-in, explicit, and repo-local rule sources."""
    builtin_resolved = builtin.resolve()
    sources: list[tuple[Path, bool]] = [(builtin, True)]
    for item in extra_dirs:
        if not item.is_dir():
            raise RuleError(f"{item}: rules-dir: directory does not exist or is unreadable")
        if item.resolve() != builtin_resolved:
            sources.append((item, False))
    local = repo / ".sins" / "rules"
    if local.is_dir() and local.resolve() not in {directory.resolve() for directory, _ in sources}:
        sources.append((local, False))
    return sources


def _load_source_rules(sources: list[tuple[Path, bool]]) -> tuple[dict[str, Rule], set[str]]:
    """Load rule files and reject shadowed or duplicate IDs."""
    rules: dict[str, Rule] = {}
    builtin_ids: set[str] = set()
    for directory, is_builtin in sources:
        files = sorted(directory.rglob("*.toml")) if directory.is_dir() else []
        for file in files:
            rule = load_rule(file)
            if rule.id in rules:
                origin = "built-in id" if rule.id in builtin_ids or is_builtin else "duplicate id"
                raise _fail(file, "id", f"{origin} {rule.id!r}; rule packs are additions only")
            rules[rule.id] = rule
            if is_builtin:
                builtin_ids.add(rule.id)
    return rules, builtin_ids


def _select_rules(rules: dict[str, Rule], selected: list[str]) -> dict[str, Rule]:
    """Filter loaded rules after checking every requested ID exists."""
    if selected:
        missing = sorted(set(selected) - rules.keys())
        if missing:
            raise RuleError(f"unknown rule id(s): {', '.join(missing)}")
        return {key: value for key, value in rules.items() if key in selected}
    return rules


def _require_implemented(rules: dict[str, Rule]) -> None:
    """Reject selected engines that this S1 implementation cannot run."""
    unsupported = sorted({rule.engine for rule in rules.values() if rule.engine != "regex"})
    if unsupported:
        raise RuleError(f"engine {unsupported[0]!r} is recognized but not implemented in S1")


def load_rules(repo: Path, extra_dirs: list[Path], selected: list[str], *, require_implemented: bool = True) -> list[Rule]:
    """Load built-in and local addition packs, rejecting duplicate IDs."""
    builtin = Path(__file__).parent / "rules"
    rules, _ = _load_source_rules(_rule_sources(repo, extra_dirs, builtin))
    rules = _select_rules(rules, selected)
    if require_implemented:
        _require_implemented(rules)
    return sorted(rules.values(), key=lambda rule: rule.id)
