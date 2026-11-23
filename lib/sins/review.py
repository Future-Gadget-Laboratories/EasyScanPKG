"""Validation and safe preflight checks for fgl-review@1 responses."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

SCHEMA = "fgl-review@1"
SEVERITIES = {"blocker", "major", "minor", "nit"}
KINDS = {"unified_diff", "replacement", "instructions"}
_REVIEW_KEYS = {"schema", "pr", "head", "reviewer", "writer_family", "verdict", "findings", "summary"}
_REVIEWER_KEYS = {"model", "family"}
_FINDING_KEYS = {"id", "severity", "sin", "file", "line", "problem", "why", "proposed_fix", "new_sin_proposal"}
_REPLACEMENT_KEYS = {"file", "start_line", "end_line", "old_code", "new_code"}
_FIX_KEYS = {
    "unified_diff": {"kind", "diff", "test", "confidence", "degraded_fix"},
    "replacement": {"kind", "replacement", "test", "confidence", "degraded_fix"},
    "instructions": {"kind", "steps", "test", "confidence", "degraded_fix"},
}


def _extra_key_errors(obj: dict[str, Any], allowed: set[str], path: str) -> list[str]:
    """Return errors for keys outside the allowed schema property set."""
    return [f"{path}.{key}: unexpected field" for key in sorted(obj) if key not in allowed]


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_pos_int(value: Any) -> bool:
    """Return True for an integer of at least one, excluding booleans."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _safe_relative(value: Any) -> bool:
    """Return True when value is a normalized, non-escaping relative POSIX path."""
    if not _is_text(value) or value != value.strip() or any(ch in value for ch in ("\\", "\x00", "\n", "\r")):
        return False
    path = PurePosixPath(value)
    win = PureWindowsPath(value)
    return (
        bool(path.parts)
        and not path.is_absolute()
        and not win.is_absolute()
        and not win.drive
        and ".." not in path.parts
    )


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _unquote_diff_path(raw: str) -> str:
    """Extract and unquote the path portion from a --- or +++ diff header."""
    path = raw.split("\t", 1)[0].strip()
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        return path[1:-1].replace('\\"', '"')
    return path


def _hunk_counts(line: str) -> tuple[int, int] | None:
    """Return (old_lines, new_lines) for a @@ hunk header, or None."""
    match = _HUNK_RE.match(line)
    if not match:
        return None
    old_count = int(match.group(2)) if match.group(2) is not None else 1
    new_count = int(match.group(4)) if match.group(4) is not None else 1
    return old_count, new_count


def _step_hunk(line: str, old_rem: int, new_rem: int) -> tuple[int, int]:
    """Decrement remaining old/new hunk line counts for one hunk line."""
    if line.startswith("\\"):
        return old_rem, new_rem
    if line.startswith("-"):
        return max(0, old_rem - 1), new_rem
    if line.startswith("+"):
        return old_rem, max(0, new_rem - 1)
    return max(0, old_rem - 1), max(0, new_rem - 1)


def _header_kind_and_path(line: str) -> tuple[str, str] | None:
    """Return ('old'|'new', path) if line is a --- or +++ header, else None."""
    if line.startswith("--- "):
        return "old", _unquote_diff_path(line[4:])
    if line.startswith("+++ "):
        return "new", _unquote_diff_path(line[4:])
    return None


def _has_valid_header_pairs(old_headers: list[str], new_headers: list[str]) -> bool:
    """Return True when --- and +++ headers match up and are not both /dev/null."""
    if not old_headers or len(old_headers) != len(new_headers):
        return False
    return not any(old == "/dev/null" and new == "/dev/null" for old, new in zip(old_headers, new_headers))


def _record_diff_header(
    line: str, index: int, old_headers: list[str], new_headers: list[str]
) -> str | None:
    """Validate and record a --- or +++ header line; return an error string if invalid."""
    header = _header_kind_and_path(line)
    if header is None:
        return None
    kind, path = header
    target = old_headers if kind == "old" else new_headers
    target.append(path)
    if path != "/dev/null" and not _safe_relative(path):
        return f"diff: file path on line {index + 1} must be relative and contain no '..'"
    return None


def _diff_errors(diff: Any) -> list[str]:
    """Validate unified diff headers, hunk presence, and relative header paths."""
    if not _is_text(diff):
        return ["diff: must be a non-empty unified diff"]
    errors: list[str] = []
    old_headers: list[str] = []
    new_headers: list[str] = []
    old_rem = new_rem = 0
    has_hunk = False
    for index, line in enumerate(diff.splitlines()):
        counts = _hunk_counts(line)
        if counts is not None:
            old_rem, new_rem = counts
            has_hunk = True
            continue
        if old_rem or new_rem:
            old_rem, new_rem = _step_hunk(line, old_rem, new_rem)
            continue
        error = _record_diff_header(line, index, old_headers, new_headers)
        if error:
            errors.append(error)
    if not _has_valid_header_pairs(old_headers, new_headers):
        errors.append("diff: requires --- and +++ file headers")
    if not has_hunk:
        errors.append("diff: requires at least one @@ hunk")
    return errors


def _fix_errors(fix: Any, path: str) -> list[str]:
    if not isinstance(fix, dict):
        return [f"{path}: must be an object"]
    kind = fix.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        return [f"{path}.kind: must be unified_diff, replacement, or instructions"]
    errors: list[str] = []
    if kind == "unified_diff":
        errors.extend(f"{path}.{error}" for error in _diff_errors(fix.get("diff")))
    elif kind == "replacement":
        errors.extend(_replacement_errors(fix.get("replacement"), f"{path}.replacement"))
    else:
        steps = fix.get("steps")
        if not isinstance(steps, list) or not steps or any(not _is_text(step) for step in steps):
            errors.append(f"{path}.steps: must be a non-empty list of non-empty strings")
    errors.extend(_fix_metadata_errors(fix, path))
    errors.extend(_extra_key_errors(fix, _FIX_KEYS[kind], path))
    return errors


def _replacement_errors(replacement: Any, path: str) -> list[str]:
    """Validate a replacement anchor and its line range."""
    if not isinstance(replacement, dict):
        return [f"{path}: must be an object"]
    errors: list[str] = []
    if not _safe_relative(replacement.get("file")):
        errors.append(f"{path}.file: must be a safe relative path")
    for field in ("start_line", "end_line"):
        if not _is_pos_int(replacement.get(field)):
            errors.append(f"{path}.{field}: must be an integer >= 1")
    start, end = replacement.get("start_line"), replacement.get("end_line")
    if _is_pos_int(start) and _is_pos_int(end) and start > end:
        errors.append(f"{path}.start_line: must be <= end_line")
    if not isinstance(replacement.get("old_code"), str) or not replacement.get("old_code"):
        errors.append(f"{path}.old_code: must be a non-empty string")
    if not isinstance(replacement.get("new_code"), str):
        errors.append(f"{path}.new_code: must be a string")
    errors.extend(_extra_key_errors(replacement, _REPLACEMENT_KEYS, path))
    return errors


def _fix_metadata_errors(fix: dict[str, Any], path: str) -> list[str]:
    """Validate optional metadata shared by proposed fix kinds."""
    errors: list[str] = []
    if "degraded_fix" in fix and not isinstance(fix["degraded_fix"], bool):
        errors.append(f"{path}.degraded_fix: must be a boolean")
    if "test" in fix and not isinstance(fix["test"], str):
        errors.append(f"{path}.test: must be a string")
    if "confidence" in fix:
        confidence = fix["confidence"]
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            errors.append(f"{path}.confidence: must be between 0 and 1")
    return errors


def _header_errors(review: dict[str, Any]) -> list[str]:
    """Validate review-level fields and cross-family policy."""
    errors: list[str] = []
    required = ("schema", "pr", "head", "reviewer", "writer_family", "verdict", "findings", "summary")
    for key in required:
        if key not in review:
            errors.append(f"{key}: required")
    if review.get("schema") != SCHEMA:
        errors.append("schema: must be fgl-review@1")
    for key in ("pr", "head", "writer_family", "summary"):
        if key in review and not _is_text(review[key]):
            errors.append(f"{key}: must be a non-empty string")
    reviewer = review.get("reviewer")
    if not isinstance(reviewer, dict):
        errors.append("reviewer: must be an object")
    else:
        for key in ("model", "family"):
            if not _is_text(reviewer.get(key)):
                errors.append(f"reviewer.{key}: must be a non-empty string")
        if _is_text(reviewer.get("family")) and _is_text(review.get("writer_family")) and reviewer["family"].casefold() == review["writer_family"].casefold():
            errors.append("reviewer.family: same model family")
        errors.extend(_extra_key_errors(reviewer, _REVIEWER_KEYS, "reviewer"))
    verdict = review.get("verdict")
    if not isinstance(verdict, str) or verdict not in {"READY", "CHANGES", "BLOCKED"}:
        errors.append("verdict: must be READY, CHANGES, or BLOCKED")
    errors.extend(_extra_key_errors(review, _REVIEW_KEYS, "$"))
    return errors


def _validate_finding(finding: Any, index: int, seen: set[str]) -> tuple[list[str], bool]:
    """Validate one finding and report whether it blocks READY."""
    prefix = f"findings[{index}]"
    if not isinstance(finding, dict):
        return [f"{prefix}: must be an object"], False
    errors: list[str] = []
    for field in ("id", "problem", "why"):
        if not _is_text(finding.get(field)):
            errors.append(f"{prefix}.{field}: must be a non-empty string")
    finding_id = finding.get("id")
    if _is_text(finding_id):
        if finding_id in seen:
            errors.append(f"{prefix}.id: duplicate id")
        seen.add(finding_id)
    severity = finding.get("severity")
    if not isinstance(severity, str) or severity not in SEVERITIES:
        errors.append(f"{prefix}.severity: must be blocker, major, minor, or nit")
    is_blocking = isinstance(severity, str) and severity in {"blocker", "major"}
    _validate_finding_location(finding, prefix, errors)
    if isinstance(severity, str) and severity in {"blocker", "major", "minor"} and "proposed_fix" not in finding:
        errors.append(f"{prefix}.proposed_fix: required for actionable findings")
    if "proposed_fix" in finding:
        errors.extend(_fix_errors(finding["proposed_fix"], f"{prefix}.proposed_fix"))
    proposal = finding.get("new_sin_proposal")
    if "new_sin_proposal" in finding and proposal is not None and not isinstance(proposal, (dict, str)):
        errors.append(f"{prefix}.new_sin_proposal: must be an object, string, or null")
    errors.extend(_extra_key_errors(finding, _FINDING_KEYS, prefix))
    return errors, is_blocking


def _validate_finding_location(finding: dict[str, Any], prefix: str, errors: list[str]) -> None:
    """Validate the sin and source location fields."""
    if "sin" not in finding or (finding.get("sin") is not None and not isinstance(finding.get("sin"), str)):
        errors.append(f"{prefix}.sin: must be a string or null")
    line = finding.get("line")
    if "line" not in finding or (line is not None and not _is_pos_int(line)):
        errors.append(f"{prefix}.line: must be an integer >= 1 or null")
    if not _safe_relative(finding.get("file")):
        errors.append(f"{prefix}.file: must be a safe relative path")


def validate_review(review: Any) -> list[str]:
    """Return path-qualified contract errors; an empty list means valid."""
    if not isinstance(review, dict):
        return ["$: must be an object"]
    errors = _header_errors(review)
    verdict = review.get("verdict")
    findings = review.get("findings")
    if not isinstance(findings, list):
        errors.append("findings: must be a list")
        findings = []
    ids: set[str] = set()
    has_blocking = False
    for index, finding in enumerate(findings):
        finding_errors, blocking = _validate_finding(finding, index, ids)
        errors.extend(finding_errors)
        has_blocking |= blocking
    if verdict == "READY" and has_blocking:
        errors.append("verdict: READY cannot include blocker or major findings")
    return errors


_JSON_FENCE_OPEN_RE = re.compile(r"```json\s*", flags=re.IGNORECASE)
_JSON_FENCE_CLOSE_RE = re.compile(r"\s*```")


def extract_review(text: str) -> Any:
    """Extract the last JSON fenced value, or parse the whole string as JSON."""
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    last_error: json.JSONDecodeError | None = None
    for match in reversed(list(_JSON_FENCE_OPEN_RE.finditer(text))):
        try:
            value, end = decoder.raw_decode(text, match.end())
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if _JSON_FENCE_CLOSE_RE.match(text, end):
            return value
    if last_error is not None:
        raise last_error
    return json.loads(stripped)


class ReviewToolError(RuntimeError):
    """A preflight tool could not run or its working directory was invalid."""


def _run_git_check(diff: str, repo: Path) -> tuple[bool, str]:
    """Check a diff, raising ReviewToolError when git cannot perform the check."""
    env = None
    if not (repo / ".git").exists():
        env = os.environ.copy()
        env["GIT_CEILING_DIRECTORIES"] = str(repo.resolve().parent)
    try:
        repository = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo,
                                    text=True, capture_output=True, timeout=30, check=False, env=env)
        if repository.returncode:
            detail = (repository.stderr or repository.stdout or "not a git repository").strip()
            raise ReviewToolError(f"git repository check failed: {detail}")
        result = subprocess.run(["git", "apply", "--check", "--recount", "-"], cwd=repo,
                                input=diff, text=True, capture_output=True, timeout=30, check=False, env=env)
    except ReviewToolError:
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewToolError(f"git apply check could not run: {exc}") from exc
    if result.returncode == 128:
        detail = (result.stderr or result.stdout or "git repository is unavailable").strip()
        raise ReviewToolError(f"git apply check could not run: {detail}")
    if result.returncode:
        return False, (result.stderr or result.stdout or "git apply --check rejected the diff").strip()
    return True, "diff applies cleanly"


def _check_replacement(fix: dict[str, Any], repo: Path) -> tuple[bool, str]:
    replacement = fix["replacement"]
    target = (repo / replacement["file"]).resolve()
    try:
        target.relative_to(repo.resolve())
        content = target.read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeError) as exc:
        return False, f"cannot read replacement target: {exc}"
    old_code = replacement["old_code"]
    if not old_code:
        return False, "old_code must be non-empty"
    count = content.count(old_code)
    if count == 1:
        return True, "old_code anchor occurs exactly once"
    return False, f"old_code anchor occurs {count} times; expected exactly once"


def check_fixes(review: dict[str, Any], repo: Path) -> list[dict[str, str]]:
    """Check actionable patches without changing the repository."""
    outcomes: list[dict[str, str]] = []
    for finding in review.get("findings", []):
        fix = finding.get("proposed_fix")
        item = {"id": str(finding.get("id", "")), "status": "skipped", "message": "no applicable fix"}
        kind = fix.get("kind") if isinstance(fix, dict) else None
        if kind == "unified_diff":
            ok, message = _run_git_check(fix.get("diff", ""), repo)
            item.update(status="ok" if ok else "fails", message=message)
        elif kind == "replacement":
            ok, message = _check_replacement(fix, repo)
            item.update(status="ok" if ok else "fails", message=message)
        elif kind == "instructions":
            item["message"] = "instructions require human application"
        elif finding.get("severity") == "nit":
            item["message"] = "nit has no proposed fix"
        outcomes.append(item)
    return outcomes
