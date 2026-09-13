"""pip-audit / OSV-Scanner dependency CVE adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scanners._util import relativize, require_binary, run_command
from scanners.base import Finding


def parse_pip_audit_json(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse `pip-audit -f json` output."""
    if not text.strip():
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    deps = data if isinstance(data, list) else data.get("dependencies") or []
    findings: list[Finding] = []
    for dep in deps if isinstance(deps, list) else []:
        if not isinstance(dep, dict):
            continue
        name = str(dep.get("name") or dep.get("package") or "package")
        version = str(dep.get("version") or "")
        vulns = dep.get("vulns") or dep.get("vulnerabilities") or []
        for vuln in vulns if isinstance(vulns, list) else []:
            if not isinstance(vuln, dict):
                continue
            vid = str(vuln.get("id") or vuln.get("alias") or "CVE")
            msg = str(vuln.get("description") or vuln.get("fix") or vid)
            findings.append(
                Finding(
                    source="pip-audit",
                    severity="CRITICAL",
                    type="VULNERABILITY",
                    rule=f"pip-audit:{vid}",
                    message=f"{name}=={version}: {msg}" if version else f"{name}: {msg}",
                    file=relativize(
                        str(
                            dep.get("path")
                            or "requirements.txt"
                        ),
                        workspace,
                    ),
                    line="-",
                    status="OPEN",
                )
            )
    return findings


def parse_osv_json(text: str, *, workspace: Path | None = None) -> list[Finding]:
    """Parse `osv-scanner --format json` output."""
    if not text.strip():
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    results = data.get("results") if isinstance(data, dict) else []
    findings: list[Finding] = []
    for result in results if isinstance(results, list) else []:
        if not isinstance(result, dict):
            continue
        source = result.get("source") or {}
        source_path = str(source.get("path") or "lockfile")
        packages = result.get("packages") or []
        for pkg in packages if isinstance(packages, list) else []:
            if not isinstance(pkg, dict):
                continue
            package = pkg.get("package") or {}
            pkg_name = str(package.get("name") or "package")
            for vuln in pkg.get("vulnerabilities") or []:
                if not isinstance(vuln, dict):
                    continue
                vid = str(vuln.get("id") or "OSV")
                summary = str(vuln.get("summary") or vuln.get("details") or vid)
                findings.append(
                    Finding(
                        source="osv",
                        severity="CRITICAL",
                        type="VULNERABILITY",
                        rule=f"osv:{vid}",
                        message=f"{pkg_name}: {summary}",
                        file=relativize(source_path, workspace),
                        line="-",
                        status="OPEN",
                    )
                )
    return findings


class PipAuditScanner:
    name = "pip-audit"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        workspace = workspace.resolve()
        binary = require_binary("pip-audit", config=config)
        cmd = [binary, "-f", "json"]
        req = config.get("requirements")
        if req:
            path = Path(str(req))
            if not path.is_absolute():
                path = workspace / path
            cmd.extend(["-r", str(path)])
        timeout = int(config.get("timeout_sec") or 600)
        proc = run_command(cmd, cwd=workspace, timeout_sec=timeout)
        return parse_pip_audit_json(proc.stdout or "", workspace=workspace)


class OsvScanner:
    name = "osv"

    def run(
        self,
        workspace: Path,
        config: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Finding]:
        _ = context
        workspace = workspace.resolve()
        binary = require_binary("osv-scanner", config=config)
        cmd = [binary, "--format", "json", "-r", str(workspace)]
        timeout = int(config.get("timeout_sec") or 600)
        proc = run_command(cmd, cwd=workspace, timeout_sec=timeout)
        return parse_osv_json(proc.stdout or "", workspace=workspace)
