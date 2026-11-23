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
    threshold: int
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


def load_rule(path: Path) -> Rule:
    """Parse and validate one TOML sin rule."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise _fail(path, "file", str(exc)) from exc
    required = {"id": str, "version": int, "phrase": str, "description": str, "language": str,
                "severity": str, "mortal": bool, "engine": str}
    for key, kind in required.items():
        if not isinstance(data.get(key), kind) or (kind is int and isinstance(data.get(key), bool)):
            raise _fail(path, key, f"is required and must be {kind.__name__}")
    rule_id = data["id"]
    if not ID_PATTERN.fullmatch(rule_id):
        raise _fail(path, "id", "must be kebab-case")
    if data["version"] < 1:
        raise _fail(path, "version", "must be >= 1")
    if not data["description"].strip():
        raise _fail(path, "description", "must not be empty")
    if not data["phrase"].strip() or data["phrase"].lower().startswith("thou shalt not"):
        raise _fail(path, "phrase", "must be non-empty and omit the prefix 'Thou shalt not'")
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", data["language"]):
        raise _fail(path, "language", "must be one language identifier string")
    if data["severity"] not in SEVERITIES:
        raise _fail(path, "severity", "must be info, minor, major, critical, or blocker")
    if data["engine"] not in ENGINES:
        raise _fail(path, "engine", f"unknown engine {data['engine']!r}")
    threshold = data.get("mortal_threshold", 0)
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 0:
        raise _fail(path, "mortal_threshold", "must be an integer >= 0")
    paths = data.get("paths", {})
    match = data.get("match", {})
    fix = data.get("fix", {})
    examples = data.get("examples", {})
    if not all(isinstance(item, dict) for item in (paths, match, fix, examples)):
        raise _fail(path, "sections", "paths, match, fix, and examples must be tables")
    allowed_root = set(required) | {"mortal_threshold", "paths", "match", "fix", "examples", "tags", "superseded_by"}
    unknown = sorted(set(data) - allowed_root)
    if unknown:
        raise _fail(path, unknown[0], "unknown field")
    for section, values, allowed in (("paths", paths, {"include", "exclude"}),
                                     ("match", match, {"pattern", "flags"}),
                                     ("fix", fix, {"kind", "template", "fix_hint"}),
                                     ("examples", examples, {"bad", "good", "fixed"})):
        extra = sorted(set(values) - allowed)
        if extra:
            raise _fail(path, f"{section}.{extra[0]}", "unknown field")
    include = _strings(paths.get("include", ["**/*"]), path, "paths.include")
    exclude = _strings(paths.get("exclude", []), path, "paths.exclude")
    bad = _strings(examples.get("bad"), path, "examples.bad", required=True)
    good = _strings(examples.get("good"), path, "examples.good", required=True)
    fixed = _strings(examples.get("fixed"), path, "examples.fixed")
    fix_kind = fix.get("kind")
    template = fix.get("template")
    if fix_kind is not None and (not isinstance(fix_kind, str) or fix_kind not in {"template", "rewrite"}):
        raise _fail(path, "fix.kind", "must be template or rewrite")
    if fix_kind and (not isinstance(template, str) or not template):
        raise _fail(path, "fix.template", "is required when fix.kind is set")
    if not fix_kind and template is not None:
        raise _fail(path, "fix.kind", "is required when fix.template is set")
    fix_hint = fix.get("fix_hint")
    if fix_hint is not None and not isinstance(fix_hint, str):
        raise _fail(path, "fix.fix_hint", "must be a string")
    superseded_by = data.get("superseded_by")
    if superseded_by is not None and not isinstance(superseded_by, str):
        raise _fail(path, "superseded_by", "must be a string")
    if fix_kind == "rewrite" and (len(fixed) != len(bad) or not fixed):
        raise _fail(path, "examples.fixed", "rewrite requires one fixed example per bad example")
    if fix_kind != "rewrite" and "fixed" in examples:
        raise _fail(path, "examples.fixed", "is only allowed for rewrite fixes")
    if data["engine"] == "regex":
        pattern = match.get("pattern")
        flags = match.get("flags", "")
        if not isinstance(pattern, str) or not pattern or not isinstance(flags, str):
            raise _fail(path, "match", "regex requires pattern and optional string flags")
        try:
            re.compile(pattern, re.MULTILINE | _regex_flags(flags))
        except (re.error, RuleError) as exc:
            raise _fail(path, "match.pattern", str(exc)) from exc
    else:
        pattern, flags = "", ""
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


def load_rules(repo: Path, extra_dirs: list[Path], selected: list[str]) -> list[Rule]:
    """Load built-in and local addition packs, rejecting duplicate IDs."""
    builtin = Path(__file__).parent / "rules"
    sources = [(builtin, True), *((item, False) for item in extra_dirs)]
    local = repo / ".sins" / "rules"
    if local.is_dir() and local.resolve() not in {directory.resolve() for directory, _ in sources}:
        sources.append((local, False))
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
    if selected:
        missing = sorted(set(selected) - rules.keys())
        if missing:
            raise RuleError(f"unknown rule id(s): {', '.join(missing)}")
        rules = {key: value for key, value in rules.items() if key in selected}
    unsupported = sorted({rule.engine for rule in rules.values() if rule.engine != "regex"})
    if unsupported:
        raise RuleError(f"engine {unsupported[0]!r} is recognized but not implemented in S1")
    return sorted(rules.values(), key=lambda rule: rule.id)
