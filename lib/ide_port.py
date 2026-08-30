"""Detect SonarQube for IDE embedded HTTP port (64120–64130)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

START_PORT = 64120
END_PORT = 64130
TIMEOUT_S = 0.35
IDE_EXTENSION_ID = "sonarsource.sonarlint-vscode"
IDE_EXTENSION_DIRS = (
    Path.home() / ".cursor" / "extensions",
    Path.home() / ".vscode" / "extensions",
)
CURSOR_LOGS = Path.home() / ".config" / "Cursor" / "logs"
EMBEDDED_PORT_RE = re.compile(
    r"(?:embedded server|security hotspot handler).{0,40}port\s+(\d{5})",
    re.IGNORECASE,
)


@dataclass
class IdeStatus:
    port: int
    ide_name: str | None
    raw: dict


@dataclass
class IdeDiagnosis:
    ok: bool
    port: int | None
    ide_name: str | None
    extension_installed: bool
    detail: str
    hint: str | None = None


def extension_installed() -> bool:
    if _extension_on_disk():
        return True
    return _extension_via_cursor_cli()


def _extension_on_disk() -> bool:
    for root in IDE_EXTENSION_DIRS:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            name = entry.name.lower()
            if IDE_EXTENSION_ID in name or name.startswith("sonarsource.sonarlint-vscode-"):
                return True
    return False


def _extension_via_cursor_cli() -> bool:
    cursor = shutil.which("cursor")
    if not cursor:
        return False
    try:
        result = subprocess.run(
            [cursor, "--list-extensions"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return any(line.strip().lower() == IDE_EXTENSION_ID for line in result.stdout.splitlines())


def _port_from_logs() -> int | None:
    if not CURSOR_LOGS.is_dir():
        return None
    log_files = sorted(CURSOR_LOGS.glob("**/SonarLint*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    log_files += sorted(CURSOR_LOGS.glob("**/SonarQube*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    log_files += sorted(CURSOR_LOGS.glob("**/exthost/**/*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    seen: set[Path] = set()
    for path in log_files:
        if path in seen:
            continue
        seen.add(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matches = EMBEDDED_PORT_RE.findall(text)
        for raw in reversed(matches):
            port = int(raw)
            if START_PORT <= port <= END_PORT:
                return port
    return None


def check_port(port: int, expected_ide: str | None = None) -> IdeStatus | None:
    for host in ("127.0.0.1", "localhost"):
        # SonarLint IDE bridge listens on loopback HTTP only (no TLS endpoint).
        url = f"http://{host}:{port}/sonarlint/api/status"  # NOSONAR python:S5332
        req = urllib.request.Request(url, headers={"Origin": "ai-agent://sft-bridge"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as response:
                if response.status != 200:
                    continue
                data = json.loads(response.read().decode("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ide_name = data.get("ideName") or data.get("ide_name")
        if expected_ide and ide_name and ide_name != expected_ide:
            continue
        return IdeStatus(port=port, ide_name=ide_name, raw=data)
    return None


def find_ide_port(expected_ide: str | None = None) -> IdeStatus | None:
    hinted = os.environ.get("SONARQUBE_IDE_PORT")
    if hinted:
        try:
            status = check_port(int(hinted), expected_ide=expected_ide)
            if status is not None:
                return status
        except ValueError:
            pass

    log_port = _port_from_logs()
    if log_port is not None:
        status = check_port(log_port, expected_ide=expected_ide)
        if status is not None:
            return status

    for port in range(START_PORT, END_PORT + 1):
        status = check_port(port, expected_ide=expected_ide)
        if status is not None:
            return status
    return None


def diagnose_ide_bridge() -> IdeDiagnosis:
    installed = extension_installed()
    status = find_ide_port()
    if status is not None:
        return IdeDiagnosis(
            ok=True,
            port=status.port,
            ide_name=status.ide_name,
            extension_installed=installed,
            detail=f"IDE bridge active on port {status.port}",
        )

    if not installed:
        return IdeDiagnosis(
            ok=False,
            port=None,
            ide_name=None,
            extension_installed=False,
            detail="SonarQube for IDE extension is not installed in Cursor",
            hint=(
                "Install: cursor --install-extension SonarSource.sonarlint-vscode "
                "then reload Cursor (Developer: Reload Window) with your workspace open"
            ),
        )

    return IdeDiagnosis(
        ok=False,
        port=None,
        ide_name=None,
        extension_installed=True,
        detail="Extension installed but embedded server not listening on 64120–64130",
        hint=(
            "Reload Cursor, open the target workspace folder, wait for SonarQube for IDE "
            "to finish starting (Output panel → SonarQube for IDE), then rerun sonar-mcp-up"
        ),
    )


def install_ide_extension() -> tuple[bool, str]:
    cursor = shutil.which("cursor")
    if not cursor:
        return False, "cursor CLI not found in PATH"
    if extension_installed():
        return True, f"{IDE_EXTENSION_ID} already installed"
    result = subprocess.run(
        [cursor, "--install-extension", IDE_EXTENSION_ID],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "install failed")[-500:]
        return False, err
    return True, f"installed {IDE_EXTENSION_ID} — reload Cursor window before detecting IDE port"
