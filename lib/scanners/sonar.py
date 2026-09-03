"""SonarQube scanner adapter: optional sonar-scan + issues search."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Mapping

from scanners.base import Finding

_SEVERITY_OK = {"BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO"}


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
        if config.get("run_scan", True) and not ctx.get("export_only"):
            self._run_sonar_scan(workspace, ctx)

        from context_resolve import resolve_creds
        from sonar_api import api

        creds = resolve_creds(
            context=ctx.get("context_name"),
            prefer_local=bool(ctx.get("prefer_local", True)),
            project_key=ctx.get("project_key"),
        )
        project_key = ctx.get("project_key") or creds.project_key
        if not project_key:
            raise RuntimeError("sonar adapter requires project_key")

        limit = int(config.get("limit") or 500)
        query: dict[str, Any] = {
            "componentKeys": project_key,
            "ps": limit,
            "resolved": "false",
        }
        if ctx.get("severities"):
            query["severities"] = ctx["severities"]
        if ctx.get("types"):
            query["types"] = ctx["types"]

        code, data = api(creds.url, creds.token, "GET", "/api/issues/search", query=query)
        if code != 200 or not isinstance(data, dict):
            raise RuntimeError(f"sonar issues search failed: HTTP {code} {data}")

        findings: list[Finding] = []
        for issue in data.get("issues") or []:
            component = (issue.get("component") or "").split(":")[-1]
            sev = str(issue.get("severity") or "MAJOR")
            if sev not in _SEVERITY_OK:
                sev = "MAJOR"
            findings.append(
                Finding(
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
            )
        ctx["_sonar_url"] = creds.url
        ctx["_sonar_project_key"] = project_key
        ctx["_sonar_total"] = int(data.get("total") or len(findings))
        ctx["_sonar_context"] = creds.context_name
        # Persist side-channel for orchestrator when caller reuses same dict
        if context is not None and hasattr(context, "update"):
            context.update(ctx)  # type: ignore[arg-type]
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
