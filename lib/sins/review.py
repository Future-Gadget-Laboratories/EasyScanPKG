"""Validation and safe preflight checks for fgl-review@1 responses."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

SCHEMA = "fgl-review@1"
SEVERITIES = {"blocker", "major", "minor", "nit"}
KINDS = {"unified_diff", "replacement", "instructions"}


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_relative(value: Any) -> bool:
    if not _is_text(value):
        return False
    path = PurePosixPath(value)
    return (not path.is_absolute() and not PureWindowsPath(value).is_absolute()
            and ".." not in path.parts and "\\" not in value)


def _diff_errors(diff: Any) -> list[str]:
    if not _is_text(diff):
        return ["diff: must be a non-empty unified diff"]
    lines = diff.splitlines()
    errors: list[str] = []
    headers: list[str] = []
    for index, line in enumerate(lines):
        if line.startswith(("--- ", "+++ ")):
            headers.append(line[4:].split("\t", 1)[0].strip())
        if line.startswith(("--- ", "+++ ")) and not _safe_relative(line[4:].split("\t", 1)[0].strip()):
            errors.append(f"diff: file path on line {index + 1} must be relative and contain no '..'")
    if len(headers) < 2 or not any(line.startswith("--- ") for line in lines) or not any(
        line.startswith("+++ ") for line in lines
    ):
        errors.append("diff: requires --- and +++ file headers")
    if not any(line.startswith("@@ ") for line in lines):
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
        replacement = fix.get("replacement")
        rp = f"{path}.replacement"
        if not isinstance(replacement, dict):
            return [f"{rp}: must be an object"]
        if not _safe_relative(replacement.get("file")):
            errors.append(f"{rp}.file: must be a safe relative path")
        for field in ("start_line", "end_line"):
            value = replacement.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                errors.append(f"{rp}.{field}: must be an integer >= 1")
        start, end = replacement.get("start_line"), replacement.get("end_line")
        if isinstance(start, int) and not isinstance(start, bool) and isinstance(end, int) and not isinstance(end, bool) and start > end:
            errors.append(f"{rp}.start_line: must be <= end_line")
        for field in ("old_code", "new_code"):
            if not isinstance(replacement.get(field), str):
                errors.append(f"{rp}.{field}: must be a string")
    else:
        steps = fix.get("steps")
        if not isinstance(steps, list) or not steps or any(not _is_text(step) for step in steps):
            errors.append(f"{path}.steps: must be a non-empty list of non-empty strings")
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
    verdict = review.get("verdict")
    if not isinstance(verdict, str) or verdict not in {"READY", "CHANGES", "BLOCKED"}:
        errors.append("verdict: must be READY, CHANGES, or BLOCKED")
    return errors


def _validate_finding(finding: Any, index: int, seen: set[str]) -> tuple[list[str], bool]:
    """Validate one finding and report whether it blocks READY."""
    prefix = f"findings[{index}]"
    if not isinstance(finding, dict):
        return [f"{prefix}: must be an object"], False
    errors: list[str] = []
    for field in ("id", "file", "problem", "why"):
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
    return errors, is_blocking


def _validate_finding_location(finding: dict[str, Any], prefix: str, errors: list[str]) -> None:
    """Validate the sin and source location fields."""
    if "sin" not in finding or (finding.get("sin") is not None and not isinstance(finding.get("sin"), str)):
        errors.append(f"{prefix}.sin: must be a string or null")
    line = finding.get("line")
    if "line" not in finding or (line is not None and (
        not isinstance(line, int) or isinstance(line, bool) or line < 1
    )):
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


def extract_review(text: str) -> Any:
    """Extract the last JSON fenced value, or parse the whole string as JSON."""
    fences = re.findall(r"```json\s*\n?(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fences:
        return json.loads(fences[-1].strip())
    return json.loads(text.strip())


def _run_git_check(diff: str, repo: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(["git", "apply", "--check", "--recount", "-"], cwd=repo,
                                input=diff, text=True, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"git apply check failed: {exc}"
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
    count = content.count(replacement["old_code"])
    if count == 1:
        return True, "old_code anchor occurs exactly once"
    return False, f"old_code anchor occurs {count} times; expected exactly once"


def check_fixes(review: dict[str, Any], repo: Path) -> list[dict[str, str]]:
    """Check actionable patches without changing the repository."""
    outcomes: list[dict[str, str]] = []
    for finding in review.get("findings", []):
        fix = finding.get("proposed_fix")
        item = {"id": str(finding.get("id", "")), "status": "skipped", "message": "no applicable fix"}
        if isinstance(fix, dict) and fix.get("kind") == "unified_diff":
            ok, message = _run_git_check(fix.get("diff", ""), repo)
            item.update(status="ok" if ok else "fails", message=message)
        elif isinstance(fix, dict) and fix.get("kind") == "replacement":
            ok, message = _check_replacement(fix, repo)
            item.update(status="ok" if ok else "fails", message=message)
        elif isinstance(fix, dict) and fix.get("kind") == "instructions":
            item["message"] = "instructions require human application"
        elif finding.get("severity") == "nit":
            item["message"] = "nit has no proposed fix"
        outcomes.append(item)
    return outcomes
