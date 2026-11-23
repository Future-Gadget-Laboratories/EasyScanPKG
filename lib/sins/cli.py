"""Command line interface for sins."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from . import RULES_VERSION
from .exitcodes import ERROR, OK, select_exit_code
from .regex_engine import _included, scan_rule
from .report import make_report, text_report, write_json
from .rules import Rule, RuleError, load_rules


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sins", description="Paladin-style local code-sin scanner")
    parser.add_argument("--version", action="store_true", help="print package and engine versions")
    parser.add_argument("--json", action="store_true", help="print version information as JSON")
    commands = parser.add_subparsers(dest="command")
    for name in ("scan", "count"):
        sub = commands.add_parser(name, help="scan a repository" if name == "scan" else "scan and print counts")
        sub.add_argument("--repo", type=Path, required=True)
        sub.add_argument("--rules-dir", type=Path, action="append", default=[])
        sub.add_argument("--rule", action="append", default=[])
        sub.add_argument("--json", dest="json_out")
        sub.add_argument("--format", choices=("text", "json"), default="text")
        sub.add_argument("--fail-on", choices=("mortal", "any", "new"), default="mortal")
    test = commands.add_parser("test", help="run rule examples")
    test.add_argument("--rules-dir", type=Path, action="append", default=[])
    test.add_argument("--rule", action="append", default=[])
    listing = commands.add_parser("list", help="list available rules")
    listing.add_argument("--repo", type=Path, default=Path.cwd())
    listing.add_argument("--rules-dir", type=Path, action="append", default=[])
    listing.add_argument("--rule", action="append", default=[])
    new = commands.add_parser("new", help="scaffold a TOML rule")
    new.add_argument("id")
    new.add_argument("--language", required=True)
    new.add_argument("--engine", default="regex")
    new.add_argument("--out", type=Path, default=Path("."))
    return parser


def _rules(repo: Path, dirs: list[Path], selected: list[str]) -> list[Rule]:
    return load_rules(repo, dirs, selected)


def _scan(args: argparse.Namespace) -> int:
    if args.fail_on == "new":
        raise RuleError("--fail-on new is not implemented until S3")
    repo = args.repo.resolve()
    if not repo.is_dir():
        raise RuleError(f"{repo}: repo: directory does not exist or is unreadable")
    rules = _rules(repo, args.rules_dir, args.rule)
    findings = [finding for rule in rules for finding in scan_rule(repo, rule)]
    report = make_report(repo, rules, findings)
    if args.json_out:
        write_json(report, args.json_out)
    if args.json_out == "-":
        pass
    elif args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(text_report(report))
    failed = any((rule.mortal and any(f.sin == rule.id and not f.forgiven for f in findings)) or
                 (not rule.mortal and rule.threshold is not None and
                  sum(f.sin == rule.id and not f.forgiven for f in findings) > rule.threshold)
                 for rule in rules) if args.fail_on == "mortal" else any(not item.forgiven for item in findings)
    return select_exit_code(findings=failed)


def _suffix(language: str) -> str:
    return {"python": ".py", "cpp": ".cpp", "text": ".txt"}.get(language, ".txt")


def _example_path(rule: Rule) -> Path:
    """Choose a synthetic filename that matches the rule's include globs."""
    default_path = "example" + _suffix(rule.language)
    if _included(default_path, rule):
        return Path(default_path)
    for pattern in rule.include:
        cleaned = pattern.strip("/")
        if cleaned.endswith("/"):
            cleaned += default_path
        parts = []
        for part in cleaned.split("/"):
            if part == "**":
                parts.append("sample")
            else:
                parts.append(re.sub(r"\[[^\]]+\]", "a", part).replace("*", "example").replace("?", "a"))
        candidate = "/".join(parts)
        if candidate and _included(candidate, rule):
            return Path(candidate)
    return Path(default_path)


def _run_examples(rule: Rule) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="sins-test-") as folder:
        repo = Path(folder)
        relative = _example_path(rule)

        def count(text: str) -> int:
            file = repo / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text, encoding="utf-8")
            try:
                return len(scan_rule(repo, rule))
            finally:
                file.unlink(missing_ok=True)

        for index, sample in enumerate(rule.bad, start=1):
            if count(sample) < 1:
                return False, f"bad example {index} produced no finding"
        for index, sample in enumerate(rule.good, start=1):
            if count(sample) != 0:
                return False, f"good example {index} produced a finding"
        if rule.fix_kind == "rewrite":
            for index, sample in enumerate(rule.fixed, start=1):
                if count(sample) != 0:
                    return False, f"fixed example {index} is not clean"
    return True, "examples match expected findings"


def _test(args: argparse.Namespace) -> int:
    rules = _rules(Path.cwd(), args.rules_dir, args.rule)
    passed = True
    for rule in rules:
        ok, detail = _run_examples(rule)
        print(f"{'PASS' if ok else 'FAIL'} {rule.id}: {detail}")
        passed = passed and ok
    return OK if passed else ERROR


def _list(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    if not repo.is_dir():
        raise RuleError(f"{repo}: repo: directory does not exist or is unreadable")
    for rule in load_rules(repo, args.rules_dir, args.rule, require_implemented=False):
        print(f"{rule.id}\t{rule.language}\t{rule.engine}\t{rule.severity}\t"
              f"{'yes' if rule.mortal else 'no'}\t{rule.phrase}")
    return OK


def _new(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", args.language):
        raise RuleError("--language: expected one language identifier")
    if args.engine not in {"regex", "ast-grep", "opengrep", "paladin", "external"}:
        raise RuleError(f"--engine: unknown engine {args.engine!r}")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.id):
        raise RuleError("id: must be kebab-case")
    if args.engine != "regex":
        raise RuleError(f"engine {args.engine!r} is recognized but not implemented in S1")
    target_dir = args.out / args.language
    target = target_dir / f"{args.id}.toml"
    if target.exists() or target.is_symlink():
        raise RuleError(f"{target}: refusing to overwrite existing rule")
    content = f'''id = "{args.id}"
version = 1
phrase = "replace with a concise phrase"
description = "TODO (owner): describe the sin"
language = "{args.language}"
severity = "minor"
mortal = false
engine = "regex"

[paths]
include = ["**/*"]
exclude = []

[match]
pattern = "PLACEHOLDER_PATTERN"

[examples]
bad = ["PLACEHOLDER_BAD"]
good = ["PLACEHOLDER_GOOD"]
'''
    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(target)
    return OK


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and convert expected tool failures to exit 2."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.version:
        details = {"package_version": RULES_VERSION, "rules_version": RULES_VERSION,
                   "engines": {"regex": "available", "ast-grep": "not implemented", "opengrep": "not implemented",
                               "paladin": "not implemented", "external": "not implemented"}}
        print(json.dumps(details, indent=2) if args.json else f"sins {RULES_VERSION} (regex available)")
        return OK
    try:
        if args.command in {"scan", "count"}:
            return _scan(args)
        if args.command == "test":
            return _test(args)
        if args.command == "list":
            return _list(args)
        if args.command == "new":
            return _new(args)
        parser.print_help()
        return OK
    except (RuleError, OSError) as exc:
        print(f"sins: {exc}", file=sys.stderr)
        return ERROR
