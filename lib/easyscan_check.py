"""EasyScanPKG readiness checks (install / commission / start gate)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path


REQUIRED_SKILLS = (
    "easyscan-bootstrap",
    "sonar-fix-queue",
    "sonar-local-ops",
    "sonar-mcp-lifecycle",
    "sonar-agent-analysis",
)

REQUIRED_IMAGES = (
    "sonarqube:community",
    "postgres:16-alpine",
    "sonarsource/sonar-scanner-cli",
    "sonarsource/sonarqube-mcp",
)

REQUIRED_BIN = (
    "sonar-local-up",
    "sonar-mcp-up",
    "sonar-scan",
    "sonar-issues",
    "sonar-project",
    "sonar-context",
    "sonar-profile",
    "sonar-desktop",
    "easyscan-check",
    "install-skills.sh",
)


@dataclass
class CheckItem:
    id: str
    ok: bool
    level: str  # hard | soft
    detail: str


@dataclass
class CheckReport:
    ok: bool
    items: list[CheckItem] = field(default_factory=list)
    root: str = ""

    def add(self, item: CheckItem) -> None:
        self.items.append(item)
        if item.level == "hard" and not item.ok:
            self.ok = False


def _bridge_root() -> Path:
    return Path(os.environ.get("BRIDGE") or Path(__file__).resolve().parents[1])


def _run(cmd: list[str], *, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)


def check_docker(report: CheckReport, *, offline: bool) -> None:
    level = "soft" if offline else "hard"
    docker = shutil.which("docker")
    if not docker:
        report.add(CheckItem("docker", False, level, "docker not found in PATH"))
        return
    try:
        info = _run([docker, "info"], timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.add(CheckItem("docker", False, level, f"docker info failed: {exc}"))
        return
    if info.returncode == 0:
        report.add(CheckItem("docker", True, "hard", "docker usable"))
        return
    if shutil.which("sg") and "permission denied" in (info.stderr or "").lower():
        try:
            sg = _run(["sg", "docker", "-c", "docker info"], timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            report.add(CheckItem("docker", False, level, f"sg docker failed: {exc}"))
            return
        if sg.returncode == 0:
            report.add(CheckItem("docker", True, "hard", "docker usable via sg docker"))
            return
    report.add(
        CheckItem(
            "docker",
            False,
            level,
            (info.stderr or info.stdout or "docker info failed")[-200:],
        )
    )


def check_images(report: CheckReport, *, offline: bool, pull: bool) -> None:
    docker = shutil.which("docker")
    if not docker:
        return
    missing: list[str] = []
    for image in REQUIRED_IMAGES:
        insp = _run([docker, "image", "inspect", image], timeout=30)
        if insp.returncode == 0:
            continue
        missing.append(image)
        if offline or not pull:
            continue
        pull_r = _run([docker, "pull", image], timeout=300)
        if pull_r.returncode == 0:
            missing.pop()
    if missing:
        level = "soft" if offline else "hard"
        report.add(
            CheckItem(
                "images",
                False,
                level,
                "missing images: " + ", ".join(missing) + (" (offline)" if offline else ""),
            )
        )
    else:
        report.add(CheckItem("images", True, "hard", "required images present"))


def check_executables(report: CheckReport, root: Path) -> None:
    bad: list[str] = []
    for name in REQUIRED_BIN:
        path = root / "bin" / name
        if name.endswith(".sh") and not path.is_file():
            # install-skills lives in bin/
            pass
        if not path.is_file():
            # also allow root scripts
            alt = root / name
            if alt.is_file():
                path = alt
            else:
                bad.append(name)
                continue
        if not os.access(path, os.X_OK):
            bad.append(f"{name}(not executable)")
    for name in ("commission.sh", "install.sh"):
        path = root / name
        if not path.is_file():
            bad.append(name)
        elif not os.access(path, os.X_OK):
            bad.append(f"{name}(not executable)")
    if bad:
        report.add(CheckItem("executables", False, "hard", "missing/not executable: " + ", ".join(bad)))
    else:
        report.add(CheckItem("executables", True, "hard", "bridge scripts present"))


def check_skills(report: CheckReport, *, offline: bool = False) -> None:
    skills_dir = Path.home() / ".cursor" / "skills"
    missing = [s for s in REQUIRED_SKILLS if not (skills_dir / s).is_dir()]
    level = "soft" if offline else "hard"
    if missing:
        report.add(
            CheckItem(
                "skills",
                False,
                level,
                "missing skills: " + ", ".join(missing) + " — run bin/install-skills.sh",
            )
        )
    else:
        report.add(CheckItem("skills", True, "hard", f"skills installed under {skills_dir}"))


def check_config_dir(report: CheckReport, *, offline: bool = False) -> None:
    cfg = Path.home() / ".config" / "sft"
    level = "soft" if offline else "hard"
    try:
        cfg.mkdir(parents=True, exist_ok=True)
        probe = cfg / ".easyscan-write-probe"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink(missing_ok=True)
        report.add(CheckItem("config_dir", True, "hard", f"writable {cfg}"))
    except OSError as exc:
        report.add(CheckItem("config_dir", False, level, str(exc)))

def check_local_env(report: CheckReport, *, require_local: bool) -> None:
    env_path = Path.home() / ".config" / "sft" / "sonar-local.env"
    if not env_path.is_file():
        report.add(
            CheckItem(
                "local_env",
                not require_local,
                "hard" if require_local else "soft",
                "sonar-local.env missing — run sonar-local-up",
            )
        )
        return
    text = env_path.read_text(encoding="utf-8")
    has_url = "SONARQUBE_URL=" in text and "SONARQUBE_URL=\n" not in text
    has_token = any(
        line.startswith("SONARQUBE_TOKEN=") and len(line.split("=", 1)[1].strip()) > 8
        for line in text.splitlines()
    )
    ok = has_url and has_token
    report.add(
        CheckItem(
            "local_env",
            ok,
            "hard" if require_local else "soft",
            "URL+token present" if ok else "sonar-local.env incomplete (token/url)",
        )
    )


def check_local_server(report: CheckReport, *, require_local: bool) -> None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:9000/api/system/status", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            up = data.get("status") == "UP"
            report.add(
                CheckItem(
                    "local_server",
                    up,
                    "hard" if require_local else "soft",
                    f"status={data.get('status')}" if up else "not UP",
                )
            )
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        report.add(
            CheckItem(
                "local_server",
                not require_local,
                "hard" if require_local else "soft",
                f"unreachable: {exc}",
            )
        )


def check_mcp_host_network(report: CheckReport, root: Path) -> None:
    snippet = root / "templates" / "cursor.mcp.json.snippet"
    mcp_home = Path.home() / ".cursor" / "mcp.json"
    found = False
    detail = "template/mcp missing --network=host"
    for path in (snippet, mcp_home):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "--network=host" in text or '"--network=host"' in text:
            found = True
            detail = f"host network in {path}"
            break
    report.add(CheckItem("mcp_host_network", found, "hard", detail))


def check_ide_extension(report: CheckReport) -> None:
    try:
        from ide_port import diagnose_ide_bridge

        diag = diagnose_ide_bridge()
        if diag.extension_installed:
            report.add(
                CheckItem(
                    "ide_extension",
                    True,
                    "soft",
                    diag.detail if diag.ok else "installed; embedded server not listening yet",
                )
            )
        else:
            report.add(
                CheckItem(
                    "ide_extension",
                    False,
                    "soft",
                    diag.hint or "SonarQube for IDE not installed",
                )
            )
    except Exception as exc:  # noqa: BLE001
        report.add(CheckItem("ide_extension", False, "soft", str(exc)))


def check_desktop(report: CheckReport) -> None:
    entry = Path.home() / ".local" / "share" / "applications" / "easyscan.desktop"
    legacy = Path.home() / ".local" / "share" / "applications" / "sft-sonar.desktop"
    path = entry if entry.is_file() else legacy
    if path.is_file():
        report.add(CheckItem("desktop_entry", True, "soft", str(path)))
    else:
        report.add(
            CheckItem(
                "desktop_entry",
                False,
                "soft",
                "no desktop entry — run sonar-desktop-install or commission.sh",
            )
        )


def check_active_context(report: CheckReport) -> None:
    try:
        from context_resolve import ensure_default_contexts

        store = ensure_default_contexts()
        active = store.get_active_context_name()
        contexts = store.list_contexts()
        if active:
            report.add(
                CheckItem(
                    "active_context",
                    True,
                    "soft",
                    f"active={active} ({len(contexts)} registered)",
                )
            )
        else:
            report.add(
                CheckItem(
                    "active_context",
                    True,
                    "soft",
                    f"no active context yet ({len(contexts)} registered) — run sonar-context use",
                )
            )
    except Exception as exc:  # noqa: BLE001
        report.add(CheckItem("active_context", False, "soft", str(exc)))


def check_issue_checklist(report: CheckReport, root: Path) -> None:
    checklist = root / ".sft" / "issue-checklist.md"
    twin = root / ".sft" / "issue-checklist.json"
    if checklist.is_file():
        detail = str(checklist)
        if twin.is_file():
            try:
                data = json.loads(twin.read_text(encoding="utf-8"))
                detail += f" open_count={data.get('open_count')} resolved={data.get('resolved')}"
            except json.JSONDecodeError:
                detail += " (json unreadable)"
        report.add(CheckItem("issue_checklist", True, "soft", detail))
    else:
        report.add(
            CheckItem(
                "issue_checklist",
                True,
                "soft",
                "no workspace checklist yet — run sonar-issues export --workspace …",
            )
        )


def check_unit_tests(report: CheckReport, root: Path, *, skip_tests: bool) -> None:
    if skip_tests:
        report.add(CheckItem("unit_tests", True, "soft", "skipped"))
        return
    try:
        result = subprocess.run(
            ["python3", "-m", "unittest", "discover", "-s", "tests", "-q"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.add(CheckItem("unit_tests", False, "hard", str(exc)))
        return
    ok = result.returncode == 0
    tail = (result.stderr or result.stdout or "")[-200:]
    report.add(CheckItem("unit_tests", ok, "hard", "passed" if ok else tail))


def check_compose_optional(report: CheckReport) -> None:
    docker = shutil.which("docker")
    if not docker:
        return
    probe = _run([docker, "compose", "version"], timeout=15)
    if probe.returncode == 0:
        report.add(CheckItem("compose", True, "soft", "docker compose available"))
    else:
        report.add(
            CheckItem(
                "compose",
                True,
                "soft",
                "docker compose missing — native Docker fallback is used (OK)",
            )
        )


def run_checks(
    *,
    offline: bool = False,
    require_local: bool = False,
    quick: bool = False,
    pull_images: bool = False,
    skip_tests: bool = False,
    root: Path | None = None,
) -> CheckReport:
    root = root or _bridge_root()
    report = CheckReport(ok=True, root=str(root))
    check_executables(report, root)
    check_config_dir(report, offline=offline)
    check_docker(report, offline=offline)
    if not quick:
        check_images(report, offline=offline, pull=pull_images and not offline)
        check_mcp_host_network(report, root)
        check_compose_optional(report)
    check_skills(report, offline=offline)
    check_local_env(report, require_local=require_local)
    if require_local or not quick:
        check_local_server(report, require_local=require_local)
    if not quick:
        check_ide_extension(report)
        check_desktop(report)
        check_active_context(report)
        check_issue_checklist(report, root)
        check_unit_tests(report, root, skip_tests=skip_tests or offline or quick)
    return report


def report_to_dict(report: CheckReport) -> dict:
    return {
        "ok": report.ok,
        "root": report.root,
        "items": [asdict(i) for i in report.items],
    }
