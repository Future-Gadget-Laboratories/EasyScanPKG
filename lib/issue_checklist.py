"""Build agent-ingestible Sonar issue checklists (markdown + JSON)."""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_REL_MD = Path(".sft") / "issue-checklist.md"
DEFAULT_REL_JSON = Path(".sft") / "issue-checklist.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def issue_file_line(issue: Mapping[str, Any]) -> tuple[str, Any]:
    component = (issue.get("component") or "").split(":")[-1]
    return component or "(unknown)", issue.get("line") or "-"


def issue_ui_url(server_url: str, project_key: str, issue_key: str) -> str:
    base = server_url.rstrip("/")
    return (
        f"{base}/project/issues?id={urllib.parse.quote(project_key)}"
        f"&open={urllib.parse.quote(issue_key)}&resolved=false"
    )


def normalize_issues(issues: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for issue in issues:
        path, line = issue_file_line(issue)
        out.append(
            {
                "key": issue.get("key"),
                "severity": issue.get("severity"),
                "type": issue.get("type"),
                "rule": issue.get("rule"),
                "message": issue.get("message") or "",
                "file": path,
                "line": line,
                "status": issue.get("status"),
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
) -> dict[str, Any]:
    normalized = normalize_issues(issues)
    open_count = total if total is not None else len(normalized)
    items = []
    for item in normalized:
        key = str(item.get("key") or "")
        items.append(
            {
                **item,
                "done": False,
                "url": issue_ui_url(server_url, project_key, key) if key else None,
            }
        )
    return {
        "schema": "easyscan.issue-checklist/v1",
        "generated_at": _utc_now(),
        "server_url": server_url.rstrip("/"),
        "project_key": project_key,
        "context": context,
        "open_count": open_count,
        "resolved": open_count == 0,
        "filters": dict(filters or {}),
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
    filters = payload.get("filters") or {}
    if filters:
        lines.append(f"- filters: `{json.dumps(filters, sort_keys=True)}`")
    lines.extend(
        [
            "",
            "Agents: treat unchecked items as the work queue. After fixes, re-run",
            "`sonar-issues export --refresh` (or `--workspace …`). **Done when open_count is 0.**",
            "",
        ]
    )
    issues = list(payload.get("issues") or [])
    if not issues:
        lines.append("_No open issues matching filters. Checklist complete._")
        lines.append("")
        return "\n".join(lines)

    for item in issues:
        key = item.get("key") or "?"
        sev = item.get("severity") or "?"
        typ = item.get("type") or "?"
        rule = item.get("rule") or "?"
        path = item.get("file") or "?"
        line = item.get("line") or "-"
        msg = (item.get("message") or "").replace("\n", " ").strip()
        url = item.get("url") or ""
        lines.append(
            f"- [ ] `{key}` **{sev}/{typ}** `{path}:{line}` — {msg} ({rule})"
            + (f"  [open]({url})" if url else "")
        )
    lines.append("")
    return "\n".join(lines)


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
