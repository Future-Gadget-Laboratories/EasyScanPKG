"""Command line interface for sins."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import stat
import sys
import tempfile
import tomllib
import tokenize
from collections import Counter
from dataclasses import replace
from pathlib import Path, PureWindowsPath

from . import RULES_VERSION
from .exitcodes import ERROR, OK, select_exit_code
from .regex_engine import Finding, _glob_match, _included, scan_rule
from .report import make_report, text_report, write_json
from .review import ReviewToolError, check_fixes, extract_review, validate_review
from .rules import Rule, RuleError, load_rules
from .policy import (_comment_segments, forgiveness_for_line, load_policy, malformed_forgiveness,
                     fingerprint, overlay_rules)
from .git_tools import (baseline_counts, git, load_baseline, mark_new, merge_base, resolve_ref,
                        worktree)


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
        sub.add_argument("--baseline", type=Path)
        sub.add_argument("--base")
        sub.add_argument("--allow-policy-change", action="store_true")
    baseline = commands.add_parser("baseline", help="manage finding baselines")
    baseline_commands = baseline.add_subparsers(dest="baseline_command", required=True)
    baseline_write = baseline_commands.add_parser("write", help="write baseline fingerprints")
    baseline_write.add_argument("--repo", type=Path, required=True)
    baseline_write.add_argument("--out", type=Path, default=Path(".sins/baseline.json"))
    diff = commands.add_parser("diff", help="compare a git ref with worktree or another ref")
    diff.add_argument("--repo", type=Path, required=True)
    diff.add_argument("--base", required=True)
    diff.add_argument("--head")
    diff.add_argument("--json", action="store_true")
    diff.add_argument("--rules-dir", type=Path, action="append", default=[])
    diff.add_argument("--rule", action="append", default=[])
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
    repo = args.repo.resolve()
    if not repo.is_dir():
        raise RuleError(f"{repo}: repo: directory does not exist or is unreadable")
    report = _scan_report(repo, args.rules_dir, args.rule)
    baseline_data = load_baseline(args.baseline) if args.baseline is not None else None
    if args.fail_on == "new":
        if args.base is None and git(repo, "show-ref", "--verify", "--quiet", "refs/remotes/origin/main", check=False).returncode != 0:
            raise RuleError("--fail-on new needs --base REF or an existing origin/main")
        if baseline_data is None:
            baseline_data = _base_report_path(args, repo)
        report["policy_tampering"] = _policy_tampering(repo, args.base or "origin/main")
        if report["policy_tampering"] and args.allow_policy_change:
            report["warnings"] = ["policy changes were allowed for this scan"]
    if baseline_data is not None:
        mark_new(report, baseline_data)
    if args.json_out:
        write_json(report, args.json_out)
    if args.json_out != "-":
        print(json.dumps(report, indent=2) if args.format == "json" else text_report(report))
    failed = bool((report["totals"].get("new", 0) or
                   (report["policy_tampering"] and not args.allow_policy_change)) if args.fail_on == "new" else
                  report["totals"]["mortal_over"] if args.fail_on == "mortal"
                  else report["totals"]["unforgiven"])
    return select_exit_code(findings=failed)


def _forgive_findings(repo: Path, rules: list[Rule], findings: list[Finding],
                      excludes: tuple[str, ...] = ()) -> list[Finding]:
    """Apply scoped source comments and emit malformed-comment meta findings."""
    languages = {rule.id: rule.language for rule in rules}
    result: list[Finding] = []
    by_path: dict[str, list[str]] = {}
    for item in findings:
        if item.path not in by_path:
            by_path[item.path] = (repo / item.path).read_text(encoding="utf-8", errors="replace").splitlines()
        forgiven = forgiveness_for_line(by_path[item.path], item.line, languages[item.sin], item.sin)
        result.append(replace(item, forgiven=forgiven))
    malformed = _malformed_findings(repo, by_path, excludes)
    if malformed and not any(rule.id == "sins-forgive-without-reason" for rule in rules):
        rules.append(Rule("sins-forgive-without-reason", 1, "add a reason to forgiveness comments",
                          "Forgiveness comments must explain why the finding is accepted.", "text",
                          ("**/*",), (), "minor", False, None, "regex", "", "", None, None,
                          None, (), (), (), (), None, Path("<built-in-meta>")))
    result.extend(malformed)
    return result


def _read_scannable_lines(file: Path) -> list[str] | None:
    """Read a bounded, non-binary regular file into lines, or None if skipped."""
    try:
        info = file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
            return None
        raw = file.read_bytes()
    except OSError:
        return None
    if len(raw) > 2 * 1024 * 1024 or b"\0" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace").splitlines()


def _malformed_findings(repo: Path, by_path: dict[str, list[str]],
                        excludes: tuple[str, ...]) -> list[Finding]:
    """Find malformed forgiveness comments in eligible source and text files."""
    extensions = {".py": "python", ".cpp": "cpp", ".cc": "cpp", ".c": "cpp", ".h": "cpp",
                  ".hpp": "cpp", ".cxx": "cpp", ".hh": "cpp", ".hxx": "cpp", ".c++": "cpp",
                  ".h++": "cpp", ".txt": "text", ".toml": "text", ".yaml": "text", ".yml": "text",
                  ".sh": "text", ".md": "text"}
    malformed: list[Finding] = []
    for directory, dirs, files in os.walk(repo):
        dirs[:] = sorted(name for name in dirs if name not in {
            ".git", ".sft", "node_modules", "build", "dist", "target", "obj", "__pycache__", ".venv"})
        for name in sorted(files):
            file = Path(directory) / name
            language = extensions.get(file.suffix.lower())
            if language is None:
                continue
            relative = file.relative_to(repo).as_posix()
            if any(_glob_match(relative, pattern) for pattern in excludes):
                continue
            lines = by_path[relative] if relative in by_path else _read_scannable_lines(file)
            if lines is None:
                continue
            malformed.extend(_malformed_file_findings(relative, language, lines))
    return malformed


def _malformed_file_findings(relative: str, language: str,
                             lines: list[str]) -> list[Finding]:
    """Build meta-sin findings for malformed comments in one file."""
    findings: list[Finding] = []
    anchor = "<bof>"
    for number, line in enumerate(lines, 1):
        if malformed_forgiveness(line, language):
            findings.append(Finding("sins-forgive-without-reason", relative, number, 1, number,
                                    len(line) + 1, "forgiveness comment requires a reason", "minor",
                                    fingerprint=fingerprint("sins-forgive-without-reason", relative,
                                                            line, anchor)))
        if line.strip():
            anchor = line
    return findings


def _base_report_path(args: argparse.Namespace, repo: Path) -> dict[str, Counter[str]]:
    """Scan merge-base for --fail-on new using isolated worktree."""
    ref = args.base or "origin/main"
    base_commit = merge_base(repo, ref)
    with worktree(repo, base_commit) as snapshot:
        return load_baseline_data(baseline_counts(_scan_report(snapshot, args.rules_dir, args.rule)))


def load_baseline_data(data: dict) -> dict[str, Counter[str]]:
    """Convert generated baseline data to the internal multiset form."""
    return {sin_id: Counter(counts) for sin_id, counts in data["counts"].items()}


_SUPPRESSION = re.compile(r"sins:\s*forgive\b|nosemgrep|\bnosem\b|ast-grep-ignore|\bnoqa\b|NOLINT")
_CPP_FORGIVE_ME = re.compile(r"\bforgive\s+me\b")
_CPP_SUFFIXES = {".cpp", ".cc", ".c", ".h", ".hpp", ".cxx", ".hh", ".hxx", ".c++", ".h++",
                 ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".cs", ".kt", ".swift"}
_HASH_CODE_SUFFIXES = {".py", ".sh", ".bash", ".rb", ".pl", ".r"}


def _policy_tampering(repo: Path, base_ref: str) -> list[dict]:
    """Find added suppressions and changed policy artifacts since merge-base."""
    base = merge_base(repo, base_ref)
    diff = git(repo, "diff", "--no-ext-diff", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/",
               "--unified=0", base, "--", ".").stdout
    return (_added_suppressions(repo, diff) + _tracked_policy_tampering(repo, base)
            + _untracked_tampering(repo))


def _added_suppressions(repo: Path, diff: str) -> list[dict]:
    """Parse added diff lines for suppression markers."""
    entries: list[dict] = []
    py_cache: dict[str, dict[int, list[str]] | None] = {}
    path = ""
    line = 0
    for content in diff.splitlines():
        if content.startswith("+++ b/"):
            path = content[6:]
        elif content.startswith("@@"):
            match = re.search(r"\+(\d+)", content)
            line = int(match.group(1)) if match else 0
        elif content.startswith("+") and not content.startswith("+++"):
            entries.extend(_suppression_entries(path, line, content[1:], repo, py_cache))
            line += 1
        elif content.startswith(" "):
            line += 1
    return entries


def _tracked_policy_tampering(repo: Path, base: str) -> list[dict]:
    """Classify weakening changes to tracked policy files."""
    entries = []
    names = git(repo, "diff", "--name-status", "--no-renames", base, "--", ".").stdout.splitlines()
    for row in names:
        bits = row.split("\t")
        status = bits[0]
        changed = bits[-1]
        tampered = False
        detail = ""
        if changed == ".sins/policy.toml":
            old = git(repo, "show", f"{base}:{changed}", check=False).stdout
            current_path = repo / changed
            new = current_path.read_text(encoding="utf-8") if current_path.is_file() else ""
            tampered = _policy_weakened(old, new, repo)
            detail = "policy change weakens a sin or broadens excludes"
        elif changed.startswith(".sins/rules/") and changed.endswith(".toml"):
            if status.startswith("D"):
                tampered = True
                detail = "rule file deleted"
            elif status.startswith("M"):
                old = git(repo, "show", f"{base}:{changed}", check=False).stdout
                new_path = repo / changed
                new = new_path.read_text(encoding="utf-8") if new_path.is_file() else ""
                tampered = _rule_narrowed(old, new)
                detail = "rule enforcement, match, or paths weakened"
        elif changed.startswith(".sins/") and "baseline" in changed:
            tampered = True
            detail = "baseline file changed"
        if tampered:
            entries.append({"kind": "sins-policy-change", "path": changed, "line": 0,
                            "detail": f"{detail} ({status})"})
    return entries


def _untracked_tampering(repo: Path) -> list[dict]:
    """Inspect untracked policy and source files omitted by git diff."""
    entries = []
    py_cache: dict[str, dict[int, list[str]] | None] = {}
    records = git(repo, "status", "--porcelain", "-uall", "-z").stdout.split("\0")
    index = 0
    while index < len(records):
        row = records[index]
        index += 1
        if not row:
            continue
        if len(row) >= 2 and (row[0] in "RC" or row[1] in "RC"):
            index += 1
            continue
        if row.startswith("?? "):
            relative = row[3:]
            entries.extend(_inspect_untracked(repo, relative, py_cache))
    return entries


def _inspect_untracked(repo: Path, relative: str,
                       py_cache: dict[str, dict[int, list[str]] | None]) -> list[dict]:
    """Inspect one bounded regular untracked file and any policy artifact."""
    entries: list[dict] = []
    if relative.startswith(".sins/") and "baseline" in relative and not relative.startswith(".sins/rules/"):
        entries.append({"kind": "sins-policy-change", "path": relative, "line": 0,
                        "detail": "untracked .sins artifact"})
    candidate = repo / relative
    lines = _read_scannable_lines(candidate)
    if lines is None:
        return entries
    if relative == ".sins/policy.toml" and _policy_weakened(
            "", "\n".join(lines), repo):
        entries.append({"kind": "sins-policy-change", "path": relative, "line": 0,
                        "detail": "new policy disables or weakens a sin"})
    for number, text in enumerate(lines, 1):
        entries.extend(_suppression_entries(relative, number, text, repo, py_cache))
    return entries


def _comment_language(path: str) -> str | None:
    """Return supported source comment syntax, excluding docs and data files."""
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in _CPP_SUFFIXES:
        return "cpp"
    return "hash" if suffix in _HASH_CODE_SUFFIXES else None


def _python_comments(repo: Path, path: str) -> dict[int, list[str]] | None:
    """Tokenize real Python comments by source line."""
    try:
        lines = _read_scannable_lines(repo / path)
        if lines is None:
            return None
        comments: dict[int, list[str]] = {}
        for token in tokenize.generate_tokens(io.StringIO("\n".join(lines) + "\n").readline):
            if token.type == tokenize.COMMENT:
                comments.setdefault(token.start[0], []).append(token.string[1:])
        return comments
    except (OSError, tokenize.TokenError, IndentationError):
        return None


def _suppression_entries(path: str, line: int, text: str, repo: Path,
                         py_cache: dict[str, dict[int, list[str]] | None]) -> list[dict]:
    """Create tampering rows only for suppression markers in real comments."""
    language = _comment_language(path)
    if language is None:
        return []
    if language == "python":
        if path not in py_cache:
            py_cache[path] = _python_comments(repo, path)
        parsed = py_cache[path]
        segments = [("hash", part) for part in parsed.get(line, ())] if parsed is not None else []
    else:
        segments = _comment_segments(text, "cpp" if language == "cpp" else "text")
    matches = []
    for _kind, comment in segments:
        matches.extend(match.group(0) for match in _SUPPRESSION.finditer(comment))
        if language == "cpp":
            matches.extend(match.group(0) for match in _CPP_FORGIVE_ME.finditer(comment))
    return [{"kind": "added-suppression", "path": path, "line": line, "detail": value}
            for value in matches]


def _policy_weakened(old_text: str, new_text: str, repo: Path) -> bool:
    """Compare policy TOML and detect overrides that reduce enforcement."""
    try:
        old, new = tomllib.loads(old_text or ""), tomllib.loads(new_text or "")
    except tomllib.TOMLDecodeError:
        return True
    old_sins, new_sins = old.get("sins", {}), new.get("sins", {})
    if not isinstance(old_sins, dict) or not isinstance(new_sins, dict):
        return True
    defaults = _rule_defaults(repo)
    for sin_id in set(old_sins) | set(new_sins):
        default_mortal, default_threshold = defaults.get(sin_id, (False, None))
        prior = old_sins.get(sin_id, {})
        current = new_sins.get(sin_id, {})
        prior = prior if isinstance(prior, dict) else {}
        current = current if isinstance(current, dict) else {}
        prior_threshold = prior.get("mortal_threshold", default_threshold)
        new_threshold = current.get("mortal_threshold", default_threshold)
        if prior_threshold is not None and (new_threshold is None or new_threshold > prior_threshold):
            return True
        if prior.get("enabled", True) is True and current.get("enabled", True) is False:
            return True
        if prior.get("mortal", default_mortal) is True and current.get("mortal", default_mortal) is False:
            return True
    old_excludes = _policy_excludes(old)
    new_excludes = _policy_excludes(new)
    if old_excludes is None or new_excludes is None:
        return True
    return bool(new_excludes - old_excludes)


def _policy_excludes(policy: dict) -> set[str] | None:
    """Read well-formed global exclude strings from policy data."""
    paths = policy.get("paths", {})
    if not isinstance(paths, dict):
        return None
    excludes = paths.get("exclude", [])
    if not isinstance(excludes, list) or any(not isinstance(value, str) for value in excludes):
        return None
    return set(excludes)


def _rule_defaults(repo: Path) -> dict[str, tuple[bool, int | None]]:
    """Read default enforcement values for built-in and repo-local rules."""
    sources = [Path(__file__).parent / "rules", repo / ".sins" / "rules"]
    result: dict[str, tuple[bool, int | None]] = {}
    for directory in sources:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.toml"):
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, tomllib.TOMLDecodeError):
                continue
            if isinstance(data.get("id"), str):
                result[data["id"]] = (data.get("mortal", False), data.get("mortal_threshold"))
    return result


def _rule_narrowed(old_text: str, new_text: str) -> bool:
    """Detect changed matching or path scope in an existing local rule."""
    try:
        old, new = tomllib.loads(old_text), tomllib.loads(new_text)
    except tomllib.TOMLDecodeError:
        return True
    if (old.get("id") != new.get("id")
            or (old.get("enabled", True) is True and new.get("enabled", True) is False)
            or (old.get("mortal", False) is True and new.get("mortal", False) is False)):
        return True
    old_threshold, new_threshold = old.get("mortal_threshold"), new.get("mortal_threshold")
    if old_threshold is not None and (new_threshold is None or new_threshold > old_threshold):
        return True
    return old.get("match") != new.get("match") or old.get("paths") != new.get("paths")


def _scan_report(repo: Path, dirs: list[Path], selected: list[str]) -> dict:
    """Scan a snapshot and return its report without printing."""
    all_rules = load_rules(repo, dirs, [], require_implemented=False)
    rules = load_rules(repo, dirs, selected)
    policy = load_policy(repo, all_rules)
    effective = overlay_rules(rules, policy)
    findings = [finding for rule in effective for finding in scan_rule(repo, rule, policy.excludes)]
    return make_report(repo, effective,
                       _forgive_findings(repo, effective, findings, policy.excludes))


def _baseline_write(args: argparse.Namespace) -> int:
    """Write a baseline fingerprint multiset for the selected repository."""
    repo = args.repo.resolve()
    if not repo.is_dir():
        raise RuleError(f"{repo}: repo: directory does not exist or is unreadable")
    report = _scan_report(repo, [], [])
    target = args.out if args.out.is_absolute() else repo / args.out
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(baseline_counts(report), indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise RuleError(f"{target}: cannot write baseline: {exc}") from exc
    print(target)
    return OK


def _diff(args: argparse.Namespace) -> int:
    """Compare a detached base snapshot with a ref or the untouched worktree."""
    repo = args.repo.resolve()
    base_ref = args.base
    base_revision = merge_base(repo, base_ref, args.head or "HEAD")
    base_snapshot = worktree(repo, base_revision)
    with base_snapshot as base_path:
        before = _scan_report(base_path, args.rules_dir, args.rule)
    if args.head:
        with worktree(repo, resolve_ref(repo, args.head)) as head_path:
            after = _scan_report(head_path, args.rules_dir, args.rule)
    else:
        after = _scan_report(repo, args.rules_dir, args.rule)
    before_counts = _fingerprint_counts(before)
    after_counts = _fingerprint_counts(after)
    rows = []
    for sin_id in sorted(set(before_counts) | set(after_counts)):
        old, new = before_counts.get(sin_id, Counter()), after_counts.get(sin_id, Counter())
        added = sum(max(0, count - old[fp]) for fp, count in new.items())
        rows.append({"sin": sin_id, "before": sum(old.values()), "after": sum(new.values()), "new": added})
    payload = {"base": base_revision, "head": after.get("ref"), "sins": rows,
               "totals": {key: sum(row[key] for row in rows) for key in ("before", "after", "new")}}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("SIN | BEFORE | AFTER | NEW")
        for row in rows:
            print(f"{row['sin']} | {row['before']} | {row['after']} | {row['new']}")
        print(f"TOTAL | {payload['totals']['before']} | {payload['totals']['after']} | {payload['totals']['new']}")
    return OK


def _fingerprint_counts(report: dict) -> dict[str, Counter[str]]:
    """Count unforgiven fingerprints per sin."""
    values: dict[str, Counter[str]] = {}
    for finding in report["findings"]:
        if not finding["forgiven"]:
            values.setdefault(finding["sin"], Counter())[finding["fingerprint"]] += 1
    return values


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
        if args.command == "baseline":
            return _baseline_write(args)
        if args.command == "diff":
            return _diff(args)
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
