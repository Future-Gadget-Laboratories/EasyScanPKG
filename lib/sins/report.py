"""JSON and compact text reporters."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import RULES_VERSION
from .regex_engine import Finding
from .rules import Rule


def make_report(repo: Path, rules: list[Rule], findings: list[Finding]) -> dict[str, Any]:
    """Build the stable report schema and internally consistent totals."""
    try:
        ref = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        ref = None
    by_rule: dict[str, list[Finding]] = {rule.id: [] for rule in rules}
    for finding in findings:
        by_rule[finding.sin].append(finding)
    sins = []
    for rule in rules:
        entries = by_rule[rule.id]
        paths = Counter(item.path for item in entries if not item.forgiven)
        sins.append({"id": rule.id, "phrase": rule.phrase, "mortal": rule.mortal,
                     "threshold": rule.threshold, "count_total": len(entries),
                     "count_forgiven": sum(item.forgiven for item in entries),
                     "count_unforgiven": sum(not item.forgiven for item in entries),
                     "count_new": sum(bool(getattr(item, "is_new", False)) for item in entries),
                     "by_path": dict(sorted(paths.items())),
                     "fixable": {"rewrite": "auto", "template": "template"}.get(rule.fix_kind or "", "manual")})
    unforgiven = sum(not item.forgiven for item in findings)
    over = sum(bool((entry["mortal"] and entry["count_unforgiven"]) or
                    (not entry["mortal"] and entry["threshold"] is not None and
                     entry["count_unforgiven"] > entry["threshold"])) for entry in sins)
    return {"schema": "fgl-sins/report@1", "repo": str(repo), "ref": ref,
            "rules_version": RULES_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
            "engine_versions": {"regex": "python-re"},
            "totals": {"findings": len(findings), "unforgiven": unforgiven,
                       "new": sum(bool(getattr(item, "is_new", False)) for item in findings),
                       "mortal_over": over, "sins_with_findings": sum(entry["count_total"] > 0 for entry in sins)},
            "policy_tampering": [], "warnings": [], "engine_errors": [], "sins": sins,
            "findings": [{"sin": item.sin, "path": item.path, "line": item.line, "col": item.col,
                          "end_line": item.end_line, "end_col": item.end_col, "severity": item.severity,
                          "message": item.message, "forgiven": item.forgiven,
                          "fingerprint": item.fingerprint, "new": bool(getattr(item, "is_new", False)),
                          "fix": None} for item in findings],
            "errors": []}


def text_report(report: dict[str, Any]) -> str:
    """Render the compact count table."""
    lines = ["SIN | COUNT | THRESH | MORTAL | FIXABLE | TOP PATHS"]
    for entry in sorted(report["sins"], key=lambda item: (-item["count_unforgiven"], item["id"])):
        tops = ", ".join(f"{path} ({count})" for path, count in
                          sorted(entry["by_path"].items(), key=lambda item: (-item[1], item[0]))[:3]) or "-"
        threshold = "∞" if entry["threshold"] is None else str(entry["threshold"])
        lines.append(f"{entry['id']} | {entry['count_unforgiven']} | {threshold} | "
                     f"{'yes' if entry['mortal'] else 'no'} | {entry['fixable']} | {tops}")
    return "\n".join(lines)


def write_json(report: dict[str, Any], output: str) -> None:
    """Write report JSON to stdout for '-' or to a file."""
    serialized = json.dumps(report, indent=2) + "\n"
    if output == "-":
        print(serialized, end="")
    else:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(serialized, encoding="utf-8")
