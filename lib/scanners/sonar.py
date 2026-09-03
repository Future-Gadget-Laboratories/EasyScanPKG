"""SonarQube scanner adapter: optional sonar-scan + issues search."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from scanners.base import Finding

_SEVERITY_OK = {"BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO"}
_INDEX_POLL_SEC = 2
_INDEX_POLL_ATTEMPTS = 15


def _issue_to_finding(issue: Mapping[str, Any]) -> Finding:
    component = (issue.get("component") or "").split(":")[-1]
    sev = str(issue.get("severity") or "MAJOR")
    if sev not in _SEVERITY_OK:
        sev = "MAJOR"
    return Finding(
        source="sonar",
        key=issue.get("key"),
        severity=sev,
        type=str(issue.get("type") or "CODE_SMELL"),
        rule=str(issue.get("rule") or "unknown"),
        message=str(issue.get("message") or ""),
        file=component or "(unknown)",
        line=issue.get("line") or "-",
        status=str(issue.get("status") or "OPEN"),
        resolution=issue.get("resolution"),
    )


def _search_issues(
    url: str,
    token: str,
    project_key: str,
    ctx: Mapping[str, Any],
    limit: int,
) -> dict[str, Any]:
    from sonar_api import api

    query: dict[str, Any] = {
        "componentKeys": project_key,
        "ps": limit,
        "resolved": "false",
    }
    if ctx.get("severities"):
        query["severities"] = ctx["severities"]
    if ctx.get("types"):
        query["types"] = ctx["types"]
    code, data = api(url, token, "GET", "/api/issues/search", query=query)
    if code != 200 or not isinstance(data, dict):
        raise RuntimeError(f"sonar issues search failed: HTTP {code} {data}")
    return data


def _wait_for_issue_index(
    url: str,
    token: str,
    project_key: str,
    ctx: Mapping[str, Any],
    limit: int,
) -> dict[str, Any]:
    """Brief poll after a fresh scan — Sonar can return total=0 before indexing."""
    data = _search_issues(url, token, project_key, ctx, limit)
    if int(data.get("total") or 0) > 0:
        return data
    for _ in range(_INDEX_POLL_ATTEMPTS):
        time.sleep(_INDEX_POLL_SEC)
        data = _search_issues(url, token, project_key, ctx, limit)
        if int(data.get("total") or 0) > 0:
            break
    return data


def _write_sonar_context(
    ctx: dict[str, Any],
    *,
    context: Mapping[str, Any] | None,
    url: str,
    project_key: str,
    context_name: str | None,
    total: int,
) -> None:
    ctx["_sonar_url"] = url
    ctx["_sonar_project_key"] = project_key
    ctx["_sonar_total"] = total
    ctx["_sonar_context"] = context_name
    if context is not None and hasattr(context, "update"):
        context.update(ctx)  # type: ignore[arg-type]


class SonarScanner:
    name = "sonar"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        ctx: dict[str, Any] = dict(context or {})
        workspace = workspace.resolve()
        ran_scan = bool(config.get("run_scan", True)) and not ctx.get("export_only")
        if ran_scan:
            self._run_sonar_scan(workspace, ctx)

        from context_resolve import resolve_creds

        creds = resolve_creds(
            context=ctx.get("context_name"),
            prefer_local=bool(ctx.get("prefer_local", True)),
            project_key=ctx.get("project_key"),
        )
        project_key = ctx.get("project_key") or creds.project_key
        if not project_key:
            raise RuntimeError("sonar adapter requires project_key")

        limit = int(config.get("limit") or 500)
        if ran_scan:
            data = _wait_for_issue_index(
                creds.url, creds.token, project_key, ctx, limit
            )
        else:
            data = _search_issues(creds.url, creds.token, project_key, ctx, limit)

        findings = [_issue_to_finding(issue) for issue in data.get("issues") or []]
        total = int(data.get("total") or len(findings))
        _write_sonar_context(
            ctx,
            context=context,
            url=creds.url,
            project_key=project_key,
            context_name=creds.context_name,
            total=total,
        )
        return findings

    def _run_sonar_scan(self, workspace: Path, ctx: Mapping[str, Any]) -> None:
        root = Path(__file__).resolve().parents[2]
        script = root / "bin" / "sonar-scan"
        if not script.is_file():
            raise RuntimeError(f"sonar-scan not found at {script}")
        cmd = [str(script), "--workspace", str(workspace)]
        if ctx.get("sources"):
            cmd.extend(["--sources", str(ctx["sources"])])
        if ctx.get("project_key"):
            cmd.extend(["--project-key", str(ctx["project_key"])])
        if ctx.get("context_name"):
            cmd.extend(["--context", str(ctx["context_name"])])
        if ctx.get("exclusions"):
            cmd.extend(["--exclusions", str(ctx["exclusions"])])
        if ctx.get("compile_commands"):
            cmd.extend(["--compile-commands", str(ctx["compile_commands"])])
        proc = subprocess.run(cmd, check=False, env=os.environ.copy())
        if proc.returncode != 0:
            raise RuntimeError(f"sonar-scan exited {proc.returncode}")
