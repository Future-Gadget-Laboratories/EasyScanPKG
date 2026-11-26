"""Readable scan report built from the exported EasyScan issue checklist.

Reads only ``.sft/issue-checklist.json`` (schema ``easyscan.issue-checklist/*``)
plus the bundled ``templates/remediation.easyscanpkg.json`` hints. No network
calls, no credentials, and no server URL is printed: issue links are kept only
as plain http(s) URLs with any userinfo removed.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from scanners.gate import SEVERITY_RANK, gated_issues

SCHEMA_PREFIX = "easyscan.issue-checklist/"
SEVERITY_ORDER = ("BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO")
UNKNOWN = "UNKNOWN"
REPORT_BASENAME = "easyscan-report"
REMEDIATION_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "remediation.easyscanpkg.json"
NO_HINT = "No bundled fix hint for this rule; open the issue link for the rule's description."
NO_SUMMARY = "Rule description not bundled."
FORMATS = ("html", "markdown", "both")
# Same closed states as scanners.gate.gated_issues, so counts and the gate agree.
CLOSED_STATUSES = frozenset({"RESOLVED", "CLOSED"})
# Whitespace, control characters, and characters that would break HTML or Markdown links.
_UNSAFE_LINK = re.compile(r"[\s\x00-\x1f\x7f<>\"'`\\]")

_CSS = """
body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:1000px;color:#1d1d1f;background:#fff;line-height:1.45}
h1{font-size:1.5rem}h2{margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.3rem}
table{border-collapse:collapse}td,th{padding:.25rem .8rem;border-bottom:1px solid #eee;text-align:left}
th.n,td.n{text-align:right}code{background:#f4f4f5;padding:0 .25rem;border-radius:3px}
.badge{display:inline-block;min-width:4.5rem;text-align:center;color:#fff;border-radius:3px;font-size:.8rem;padding:0 .3rem}
.BLOCKER{background:#7a0c2e}.CRITICAL{background:#c0392b}.MAJOR{background:#a84300}
.MINOR{background:#2471a3}.INFO{background:#5d6d7e}.UNKNOWN{background:#596566}
.pass{color:#1e7b34;font-weight:600}.fail{color:#b00020;font-weight:600}
details{margin:.6rem 0;border-left:3px solid #ddd;padding-left:.7rem}summary{cursor:pointer;font-weight:600}
.rule{margin:.5rem 0 .5rem 1rem}.hint{color:#555;font-size:.92rem}.changed{color:#8a4b00;font-size:.85rem}
li{margin:.2rem 0}.src{color:#595959;font-size:.85rem}
"""


class ReportError(RuntimeError):
    """User-facing failure; the CLI prints it as one line and exits 2."""


def load_checklist(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReportError(
            f"no checklist at {path}: run easyscan-scan or sonar-issues export first"
        ) from exc
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ReportError(f"cannot read checklist {path}: {exc}") from exc
    if not isinstance(data, dict) or not str(data.get("schema") or "").startswith(SCHEMA_PREFIX):
        raise ReportError(f"{path} is not an EasyScan issue checklist JSON")
    if not isinstance(data.get("issues"), list):
        raise ReportError(f"{path} has no issues list")
    return data


def load_remediation(path: Path = REMEDIATION_TEMPLATE) -> dict[str, Any]:
    try:
        rules = json.loads(Path(path).read_text(encoding="utf-8")).get("rules")
    except (OSError, ValueError, AttributeError):
        return {}
    return rules if isinstance(rules, dict) else {}


def norm_path(raw: Any) -> str:
    text = str(raw or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text or "(unknown)"


def _git_paths(workspace: Path, args: list[str]) -> list[str]:
    """Run a read-only git command that prints NUL-separated paths (``-z``)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(workspace), "-c", "core.quotePath=false", *args],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReportError(f"cannot run git for --changed-from: {exc}") from exc
    if proc.returncode != 0:
        raise ReportError(f"git {args[0]} failed: {proc.stderr.strip()[:200]}")
    return [entry for entry in proc.stdout.split("\0") if entry.strip()]


def changed_files(workspace: Path, ref: str) -> set[str]:
    """Tracked files that differ from ``ref`` plus untracked files, relative to workspace."""
    if not ref or ref.startswith("-"):
        raise ReportError(f"invalid git ref {ref!r}")
    diff = _git_paths(workspace, ["diff", "--name-only", "-z", "--relative", ref, "--"])
    untracked = _git_paths(workspace, ["ls-files", "--others", "--exclude-standard", "-z"])
    return {norm_path(entry) for entry in (*diff, *untracked)}


def safe_link(raw: Any) -> str | None:
    """Keep only plain http(s) links; userinfo (credentials) is stripped.

    Links with whitespace, control characters, or quotes/angle brackets are dropped.
    Parentheses are percent-encoded so a link cannot close the Markdown ``[open](...)``.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if not text or _UNSAFE_LINK.search(text):
        return None
    try:
        parts = urlsplit(text)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        netloc = parts.netloc.rsplit("@", 1)[-1]
    except ValueError:
        return None
    link = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
    return link.replace("(", "%28").replace(")", "%29")


def _rank(issue: Mapping[str, Any]) -> int:
    return SEVERITY_RANK.get(str(issue.get("severity")), 0)


def _line_key(issue: Mapping[str, Any]) -> int:
    text = str(issue.get("line"))
    return int(text) if text.isdigit() else 0


def normalize_issues(raw_issues: Iterable[Any], changed: set[str] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in raw_issues:
        if not isinstance(raw, Mapping):
            continue
        path = norm_path(raw.get("file"))
        severity = str(raw.get("severity") or "").upper()
        out.append({
            "file": path,
            "line": raw.get("line") or "-",
            "severity": severity if severity in SEVERITY_RANK else UNKNOWN,
            "type": str(raw.get("type") or "?"),
            "rule": str(raw.get("rule") or "unknown"),
            "message": " ".join(str(raw.get("message") or "").split()),
            "source": str(raw.get("source") or "sonar"),
            "status": str(raw.get("status") or "OPEN").upper(),
            "url": safe_link(raw.get("url")),
            "changed": None if changed is None else path in changed,
        })
    return out


def group_by_file(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Files (worst first), then rules inside each file (worst first)."""
    by_file: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        by_file.setdefault(issue["file"], []).append(issue)
    groups = []
    for path, items in by_file.items():
        by_rule: dict[str, list[dict[str, Any]]] = {}
        for issue in items:
            by_rule.setdefault(issue["rule"], []).append(issue)
        rules = [
            {"rule": rule, "issues": sorted(found, key=lambda i: (-_rank(i), _line_key(i)))}
            for rule, found in by_rule.items()
        ]
        rules.sort(key=lambda r: (-_rank(r["issues"][0]), -len(r["issues"]), r["rule"]))
        worst = max(items, key=_rank)["severity"]
        groups.append({
            "file": path,
            "count": len(items),
            "max_severity": worst,
            "changed": any(i["changed"] for i in items),
            "rules": rules,
        })
    groups.sort(key=lambda g: (-SEVERITY_RANK.get(g["max_severity"], 0), -g["count"], g["file"]))
    return groups


def hint_for(rule: str, remediation: Mapping[str, Any]) -> dict[str, str]:
    entry = remediation.get(rule)
    if not isinstance(entry, Mapping):
        return {"summary": "", "hint": NO_HINT}
    guidance = entry.get("guidance") or []
    return {
        "summary": str(entry.get("summary") or ""),
        "hint": str(guidance[0]) if guidance else NO_HINT,
    }


def build_report(
    checklist: Mapping[str, Any],
    *,
    remediation: Mapping[str, Any] | None = None,
    changed_from: str | None = None,
    changed: set[str] | None = None,
    threshold: str | None = None,
    top: int = 5,
) -> dict[str, Any]:
    remediation = remediation or {}
    everything = normalize_issues(checklist.get("issues") or [], changed)
    issues = [issue for issue in everything if issue["status"] not in CLOSED_STATUSES]
    declared = checklist.get("open_count")
    open_count = declared if isinstance(declared, int) and not isinstance(declared, bool) else len(issues)
    counts = {level: 0 for level in (*SEVERITY_ORDER, UNKNOWN)}
    for issue in issues:
        counts[issue["severity"]] += 1
    groups = group_by_file(issues)
    changed_issues = [i for i in issues if i["changed"]]
    rules = {i["rule"] for i in issues}
    return {
        "project_key": str(checklist.get("project_key") or "(unknown)"),
        "context": checklist.get("context") or None,
        "generated_at": checklist.get("generated_at") or None,
        "sources_run": [str(s) for s in checklist.get("sources_run") or []],
        "open_count": open_count,
        "listed": len(issues),
        "resolved_hidden": len(everything) - len(issues),
        "counts": counts,
        "top_files": [(g["file"], g["count"], g["max_severity"]) for g in groups[: max(top, 0)]],
        "groups": groups,
        "changed_from": changed_from,
        "changed_groups": group_by_file(changed_issues) if changed_from else [],
        "changed_open": len(changed_issues),
        "changed_files": len({i["file"] for i in changed_issues}),
        "threshold": threshold,
        "failing": gated_issues(issues, threshold) if threshold else [],
        "hints": {rule: hint_for(rule, remediation) for rule in sorted(rules)},
    }


def _gate_text(report: Mapping[str, Any]) -> str | None:
    threshold = report["threshold"]
    if not threshold:
        return None
    failing = len(report["failing"])
    verdict = "FAIL" if failing else "PASS"
    return f"Gate {threshold}+: {failing} open finding(s) — {verdict}"


def _meta(report: Mapping[str, Any]) -> list[str]:
    sources = ", ".join(report["sources_run"]) or "(none)"
    lines = [
        f"- project_key: `{report['project_key']}`",
        f"- context: `{report['context'] or '(none)'}`",
        f"- generated_at: `{report['generated_at'] or '(unknown)'}`",
        f"- sources_run: `{sources}`",
        f"- open findings: **{report['listed']}** (checklist open_count {report['open_count']})",
    ]
    if report["resolved_hidden"]:
        lines.append(f"- resolved or closed findings hidden: {report['resolved_hidden']}")
    if report["open_count"] > report["listed"]:
        lines.append("- note: the checklist lists fewer findings than its open_count; re-export with a higher limit")
    return lines


def _md_issue(issue: Mapping[str, Any]) -> str:
    link = f" [[open]({issue['url']})]" if issue["url"] else ""
    return (
        f"- [ ] **{issue['severity']}** `{issue['file']}:{issue['line']}` — "
        f"{issue['message']} ({issue['rule']}) [{issue['source']}]{link}"
    )


def _md_groups(groups: list[dict[str, Any]], hints: Mapping[str, Any], *, show_changed: bool) -> list[str]:
    if not groups:
        return ["_No open findings._"]
    out: list[str] = []
    for group in groups:
        tag = " — changed" if show_changed and group["changed"] else ""
        out += [f"### `{group['file']}` — {group['count']} open, worst {group['max_severity']}{tag}", ""]
        for rule_group in group["rules"]:
            hint = hints[rule_group["rule"]]
            out += [
                f"#### `{rule_group['rule']}` — {len(rule_group['issues'])} open",
                "",
                f"- Rule: {hint['summary'] or NO_SUMMARY}",
                f"- Fix hint: {hint['hint']}",
                "",
            ]
            out += [_md_issue(issue) for issue in rule_group["issues"]]
            out.append("")
    return out


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [f"# EasyScan report: `{report['project_key']}`", "", *_meta(report), ""]
    lines += ["## Severity counts", "", "| Severity | Open |", "| --- | ---: |"]
    lines += [f"| {level} | {count} |" for level, count in report["counts"].items()]
    gate = _gate_text(report)
    if gate:
        lines += ["", f"**{gate}**"]
    if report["changed_from"]:
        lines += [
            "",
            f"## New in changed files (since `{report['changed_from']}`)",
            "",
            f"{report['changed_open']} open finding(s) in {report['changed_files']} changed file(s).",
        ]
        lines += [""] + _md_groups(report["changed_groups"], report["hints"], show_changed=False)
    lines += ["", "## Findings by file", ""]
    lines += _md_groups(report["groups"], report["hints"], show_changed=bool(report["changed_from"]))
    return "\n".join(lines).rstrip() + "\n"


def _h(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _html_meta(report: Mapping[str, Any]) -> str:
    sources = ", ".join(report["sources_run"]) or "(none)"
    items = [
        f"project_key: <code>{_h(report['project_key'])}</code>",
        f"context: <code>{_h(report['context'] or '(none)')}</code>",
        f"generated_at: <code>{_h(report['generated_at'] or '(unknown)')}</code>",
        f"sources_run: <code>{_h(sources)}</code>",
        f"open findings: <strong>{report['listed']}</strong> (checklist open_count {_h(report['open_count'])})",
    ]
    if report["resolved_hidden"]:
        items.append(f"resolved or closed findings hidden: {report['resolved_hidden']}")
    if report["open_count"] > report["listed"]:
        items.append("note: the checklist lists fewer findings than its open_count; re-export with a higher limit")
    return "".join(f"<li>{item}</li>" for item in items)


def _html_issue(issue: Mapping[str, Any]) -> str:
    link = f' <a href="{_h(issue["url"])}">open</a>' if issue["url"] else ""
    return (
        f'<li><span class="badge {_h(issue["severity"])}">{_h(issue["severity"])}</span> '
        f"<code>{_h(issue['line'])}</code> {_h(issue['message'])} "
        f'<span class="src">({_h(issue["rule"])}) [{_h(issue["source"])}]</span>{link}</li>'
    )


def _html_groups(groups: list[dict[str, Any]], hints: Mapping[str, Any], *, show_changed: bool) -> list[str]:
    if not groups:
        return ["<p><em>No open findings.</em></p>"]
    out: list[str] = []
    for group in groups:
        tag = ' <span class="changed">changed</span>' if show_changed and group["changed"] else ""
        out.append(
            f"<details open><summary><code>{_h(group['file'])}</code> — {group['count']} open, "
            f"worst {_h(group['max_severity'])}{tag}</summary>"
        )
        for rule_group in group["rules"]:
            hint = hints[rule_group["rule"]]
            out.append(
                f'<div class="rule"><strong><code>{_h(rule_group["rule"])}</code></strong> — '
                f"{len(rule_group['issues'])} open<div>{_h(hint['summary'] or NO_SUMMARY)}</div>"
                f'<div class="hint">Fix hint: {_h(hint["hint"])}</div><ul>'
            )
            out += [_html_issue(issue) for issue in rule_group["issues"]]
            out.append("</ul></div>")
        out.append("</details>")
    return out


def render_html(report: Mapping[str, Any]) -> str:
    rows = "".join(
        f'<tr><td><span class="badge {_h(level)}">{_h(level)}</span></td>'
        f'<td class="n">{count}</td></tr>'
        for level, count in report["counts"].items()
    )
    gate = _gate_text(report)
    gate_html = ""
    if gate:
        verdict_class = "fail" if report["failing"] else "pass"
        gate_html = f'<p class="{verdict_class}">{_h(gate)}</p>'
    body = [
        f"<h1>EasyScan report: <code>{_h(report['project_key'])}</code></h1>",
        f"<ul>{_html_meta(report)}</ul>",
        "<h2>Severity counts</h2>",
        (
            '<table><thead><tr><th scope="col">Severity</th>'
            '<th scope="col" class="n">Open</th></tr></thead>'
            f"<tbody>{rows}</tbody></table>"
        ),
        gate_html,
    ]
    if report["changed_from"]:
        body += [
            f"<h2>New in changed files (since <code>{_h(report['changed_from'])}</code>)</h2>",
            f"<p>{report['changed_open']} open finding(s) in {report['changed_files']} changed file(s).</p>",
            *_html_groups(report["changed_groups"], report["hints"], show_changed=False),
        ]
    body.append("<h2>Findings by file</h2>")
    body += _html_groups(report["groups"], report["hints"], show_changed=bool(report["changed_from"]))
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>EasyScan report: {_h(report['project_key'])}</title>"
        f"<style>{_CSS}</style></head><body><main>\n" + "\n".join(body) + "\n</main></body></html>\n"
    )


def render_terminal(report: Mapping[str, Any]) -> str:
    lines = [
        f"EasyScan report: {report['project_key']} — {report['listed']} open finding(s)",
        "",
        "Severity   Open",
    ]
    lines += [f"{level:<10} {count}" for level, count in report["counts"].items()]
    lines += ["", "Top files:"]
    if not report["top_files"]:
        lines.append("  (none)")
    lines += [f"  {count:>3}  {path}  (worst {worst})" for path, count, worst in report["top_files"]]
    if report["changed_from"]:
        lines.append(
            f"Changed since {report['changed_from']}: {report['changed_open']} open "
            f"in {report['changed_files']} file(s)"
        )
    gate = _gate_text(report)
    if gate:
        lines.append(gate)
        lines += [
            f"  {i.get('severity')} {i['file']}:{i['line']} — {i['message'][:120]}"
            for i in report["failing"][:20]
        ]
    return "\n".join(lines) + "\n"


def write_reports(report: Mapping[str, Any], out_dir: Path, fmt: str) -> list[Path]:
    if fmt not in FORMATS:
        raise ReportError(f"unknown format {fmt!r} (choose from: {', '.join(FORMATS)})")
    out_dir = Path(out_dir)
    planned: list[tuple[Path, str]] = []
    if fmt in ("html", "both"):
        planned.append((out_dir / f"{REPORT_BASENAME}.html", render_html(report)))
    if fmt in ("markdown", "both"):
        planned.append((out_dir / f"{REPORT_BASENAME}.md", render_markdown(report)))
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        for path, text in planned:
            path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ReportError(f"cannot write report to {out_dir}: {exc}") from exc
    return [path for path, _ in planned]
