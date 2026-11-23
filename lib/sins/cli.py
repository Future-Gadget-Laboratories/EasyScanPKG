"""Command line interface for sins."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path, PureWindowsPath

from . import RULES_VERSION
from .exitcodes import ERROR, OK, select_exit_code
from .regex_engine import _included, scan_rule
from .report import make_report, text_report, write_json
from .review import ReviewToolError, check_fixes, extract_review, validate_review
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
    review = commands.add_parser("review", help="validate reviewer responses and proposed fixes")
    review_commands = review.add_subparsers(dest="review_command", required=True)
    validate = review_commands.add_parser("validate", help="validate an fgl-review@1 response")
    validate.add_argument("file", type=Path)
    validate.add_argument("--json", action="store_true")
    check = review_commands.add_parser("check-fixes", help="preflight proposed fixes without applying them")
    check.add_argument("file", type=Path)
    check.add_argument("--repo", type=Path, required=True)
    check.add_argument("--json", action="store_true")
    template = review_commands.add_parser("template", help="print the reviewer prompt block")
    template.add_argument("--writer-family", default="<writer-family>")
    template.add_argument("--reviewer-model", default="<reviewer-model>")
    template.add_argument("--reviewer-family", default="<reviewer-family>")
    template.add_argument("--max-findings", default="20")
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
    if args.json_out != "-":
        print(json.dumps(report, indent=2) if args.format == "json" else text_report(report))
    failed = bool(report["totals"]["mortal_over"] if args.fail_on == "mortal"
                  else report["totals"]["unforgiven"])
    return select_exit_code(findings=failed)


def _suffix(language: str) -> str:
    return {"python": ".py", "cpp": ".cpp", "text": ".txt"}.get(language, ".txt")


def _example_path(rule: Rule) -> Path:
    """Choose a synthetic filename that matches the rule's include globs."""
    for pattern in rule.include:
        if pattern.startswith("/") or PureWindowsPath(pattern).is_absolute() or any(
            part == ".." for part in pattern.split("/")
        ):
            raise RuleError(f"{rule.source}: paths.include: unsafe example pattern {pattern!r}")
    default_path = "example" + _suffix(rule.language)
    if _included(default_path, rule):
        return Path(default_path)
    for pattern in rule.include:
        cleaned = pattern
        if cleaned.endswith("/"):
            cleaned += default_path
        parts = ["sample" if part == "**" else
                 re.sub(r"\[[^\]]+\]", "a", part).replace("*", "example").replace("?", "a")
                 for part in cleaned.split("/") if part]
        candidate = "/".join(parts)
        if candidate and _included(candidate, rule):
            return Path(candidate)
    return Path(default_path)


def _run_examples(rule: Rule) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="sins-test-") as folder:
        repo = Path(folder)
        relative = _example_path(rule)

        def count(text: str) -> int:
            file = (repo / relative).resolve()
            if not file.is_relative_to(repo.resolve()):
                raise RuleError(f"{rule.source}: paths.include: example path escapes temporary repo")
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


def _read_review(path: Path) -> dict:
    """Load a review JSON file, raising on I/O or JSON decoding failures."""
    value = extract_review(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("review JSON must be an object")
    return value


def _review_template(args: argparse.Namespace) -> int:
    """Render and print the reviewer prompt template."""
    template_path = Path(__file__).parent / "templates" / "review-prompt.md"
    prompt = template_path.read_text(encoding="utf-8")
    substitutions = {"{writer_family}": args.writer_family,
                     "{reviewer_model}": args.reviewer_model,
                     "{reviewer_family}": args.reviewer_family,
                     "{max_findings}": args.max_findings}
    for marker, value in substitutions.items():
        prompt = prompt.replace(marker, value)
    print(prompt, end="")
    return OK


def _review_validate(review: dict, as_json: bool) -> int:
    """Validate a parsed review dictionary and report errors."""
    errors = validate_review(review)
    result = {"valid": not errors, "errors": errors}
    if as_json:
        print(json.dumps(result, indent=2))
    elif errors:
        print("Invalid review:")
        print("\n".join(f"- {error}" for error in errors))
    else:
        print("Review is valid.")
    return OK if not errors else 1


def _review_check_fixes(review: dict, repo_arg: Path, as_json: bool) -> int:
    """Run fix preflight checks against repo and report exit status."""
    repo = repo_arg.resolve()
    if not repo.is_dir():
        print(f"sins review: repo is not a directory: {repo}", file=sys.stderr)
        return ERROR
    errors = validate_review(review)
    if errors:
        if as_json:
            print(json.dumps({"error": "invalid review", "errors": errors}, indent=2), file=sys.stderr)
        else:
            print("sins review: invalid review:\n" + "\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return ERROR
    try:
        outcomes = check_fixes(review, repo)
    except ReviewToolError as exc:
        print(f"sins review: {exc}", file=sys.stderr)
        return ERROR
    result = {"results": outcomes, "all_ok": not any(item["status"] == "fails" for item in outcomes)}
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        for item in outcomes:
            print(f"{item['status'].upper()} {item['id']}: {item['message']}")
    return OK if result["all_ok"] else 1


def _review(args: argparse.Namespace) -> int:
    """Dispatch sins review subcommands."""
    if args.review_command == "template":
        return _review_template(args)
    try:
        review = _read_review(args.file)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"sins review: {exc}", file=sys.stderr)
        return ERROR
    if args.review_command == "validate":
        return _review_validate(review, args.json)
    return _review_check_fixes(review, args.repo, args.json)


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
        if args.command == "review":
            return _review(args)
        parser.print_help()
        return OK
    except (RuleError, OSError) as exc:
        print(f"sins: {exc}", file=sys.stderr)
        return ERROR
