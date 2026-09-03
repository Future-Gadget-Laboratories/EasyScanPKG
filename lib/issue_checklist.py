"""Build agent-ingestible multi-scanner issue checklists (markdown + JSON)."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_V1 = "easyscan.issue-checklist/v1"
SCHEMA_V2 = "easyscan.issue-checklist/v2"
DEFAULT_REL_MD = Path(".sft") / "issue-checklist.md"
DEFAULT_REL_JSON = Path(".sft") / "issue-checklist.json"

TOOL_DOC_URLS = {
    "sonar": None,
    "clang-tidy": "https://clang.llvm.org/extra/clang-tidy/",
    "drmemory": "https://drmemory.org/",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def issue_file_line(issue: Mapping[str, Any]) -> tuple[str, Any]:
    if issue.get("file"):
        return str(issue["file"]), issue.get("line") or "-"
    component = (issue.get("component") or "").split(":")[-1]
    return component or "(unknown)", issue.get("line") or "-"


def issue_ui_url(server_url: str, project_key: str, issue_key: str) -> str:
    base = server_url.rstrip("/")
    return (
        f"{base}/project/issues?id={urllib.parse.quote(project_key)}"
        f"&open={urllib.parse.quote(issue_key)}&resolved=false"
    )


def stable_finding_key(
    *,
    source: str,
    rule: str,
    path: str,
    line: Any,
    message: str,
    native_key: str | None = None,
) -> str:
    if native_key:
        return str(native_key)
    digest = hashlib.sha1(
        f"{source}|{rule}|{path}|{line}|{message}".encode("utf-8")
    ).hexdigest()[:12]
    return f"{source}:{digest}"


def finding_url(
    *,
    source: str,
    server_url: str | None,
    project_key: str | None,
    key: str,
) -> str | None:
    if source == "sonar" and server_url and project_key and key:
        return issue_ui_url(server_url, project_key, key)
    return TOOL_DOC_URLS.get(source)


def normalize_issues(
    issues: Sequence[Mapping[str, Any]],
    *,
    default_source: str = "sonar",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for issue in issues:
        path, line = issue_file_line(issue)
        source = str(issue.get("source") or default_source)
        rule = str(issue.get("rule") or "unknown")
        message = issue.get("message") or ""
        native = issue.get("key")
        # Prefer explicit key; for non-sonar leave hash generation to build_payload
        # when key missing.
        key = native
        out.append(
            {
                "key": key,
                "source": source,
                "severity": issue.get("severity"),
                "type": issue.get("type"),
                "rule": rule,
                "message": message,
                "file": path,
                "line": line,
                "status": issue.get("status") or "OPEN",
                "resolution": issue.get("resolution"),
            }
        )
    return out


def build_payload(
    *,
    server_url: str,
    project_key: str,
    issues: Sequence[Mapping[str, Any]],
    context: str | None = None,
    filters: Mapping[str, Any] | None = None,
    total: int | None = None,
    sources_run: Sequence[str] | None = None,
    sources_skipped: Mapping[str, str] | None = None,
    default_source: str = "sonar",
) -> dict[str, Any]:
    normalized = normalize_issues(issues, default_source=default_source)
    open_count = total if total is not None else len(normalized)
    run = list(sources_run) if sources_run is not None else [default_source]
    skipped = dict(sources_skipped or {})
    items = []
    for item in normalized:
        source = str(item.get("source") or default_source)
        key = stable_finding_key(
            source=source,
            rule=str(item.get("rule") or "unknown"),
            path=str(item.get("file") or ""),
            line=item.get("line"),
            message=str(item.get("message") or ""),
            native_key=str(item["key"]) if item.get("key") else None,
        )
        items.append(
            {
                **item,
                "key": key,
                "source": source,
                "done": False,
                "url": finding_url(
                    source=source,
                    server_url=server_url,
                    project_key=project_key,
                    key=key,
                ),
            }
        )
    return {
        "schema": SCHEMA_V2,
        "generated_at": _utc_now(),
        "server_url": server_url.rstrip("/"),
        "project_key": project_key,
        "context": context,
        "open_count": open_count,
        "resolved": open_count == 0,
        "filters": dict(filters or {}),
        "sources_run": run,
        "sources_skipped": skipped,
        "issues": items,
    }


def render_markdown(payload: Mapping[str, Any]) -> str:
    lines: list[str] = [
        "# EasyScanPKG issue checklist",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        f"- server: `{payload.get('server_url')}`",
        f"- project_key: `{payload.get('project_key')}`",
        f"- context: `{payload.get('context') or '(none)'}`",
        f"- open_count: **{payload.get('open_count', 0)}**",
        f"- resolved: **{'yes' if payload.get('resolved') else 'no'}**",
    ]
    sources_run = payload.get("sources_run") or []
    if sources_run:
        lines.append(f"- sources_run: `{json.dumps(list(sources_run))}`")
    sources_skipped = payload.get("sources_skipped") or {}
    if sources_skipped:
        lines.append(f"- sources_skipped: `{json.dumps(sources_skipped, sort_keys=True)}`")
    filters = payload.get("filters") or {}
    if filters:
        lines.append(f"- filters: `{json.dumps(filters, sort_keys=True)}`")
    lines.extend(
        [
            "",
            "Agents: treat unchecked items as the work queue. After fixes, re-run",
            "`easyscan-scan` (or `sonar-issues export --refresh`). **Done when open_count is 0.**",
            "",
        ]
    )
    issues = list(payload.get("issues") or [])
    if not issues:
        lines.append("_No open issues matching filters. Checklist complete._")
        lines.append("")
        return "\n".join(lines)

    lines.extend(_format_issue_line(item) for item in issues)
    lines.append("")
    return "\n".join(lines)


def _format_issue_line(item: Mapping[str, Any]) -> str:
    key = item.get("key") or "?"
    sev = item.get("severity") or "?"
    typ = item.get("type") or "?"
    rule = item.get("rule") or "?"
    path = item.get("file") or "?"
    line = item.get("line") or "-"
    msg = (item.get("message") or "").replace("\n", " ").strip()
    url = item.get("url") or ""
    source = item.get("source") or "sonar"
    link = f"  [open]({url})" if url else ""
    return (
        f"- [ ] `{key}` **{sev}/{typ}** `{path}:{line}` — {msg} ({rule}) [{source}]{link}"
    )


def write_checklist(
    workspace: Path,
    payload: Mapping[str, Any],
    *,
    md_path: Path | None = None,
    json_path: Path | None = None,
    write_json: bool = True,
) -> tuple[Path, Path | None]:
    workspace = workspace.resolve()
    md_out = (workspace / DEFAULT_REL_MD) if md_path is None else Path(md_path)
    if not md_out.is_absolute():
        md_out = workspace / md_out
    md_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.write_text(render_markdown(payload), encoding="utf-8")

    json_out: Path | None = None
    if write_json:
        json_out = (workspace / DEFAULT_REL_JSON) if json_path is None else Path(json_path)
        if not json_out.is_absolute():
            json_out = workspace / json_out
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return md_out, json_out
