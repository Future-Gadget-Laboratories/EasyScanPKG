"""Install the host tools behind easyscan-scan's scanners.

Every tool is tried through an ordered list of methods until one works:

* ``apt`` / ``dnf`` — distro packages (needs root or sudo)
* ``pip``  — isolated virtualenv under ``~/.config/sft/scanners/venv``
* ``github`` — release binary, verified by SHA-256 before install

Installed binaries are linked into ``~/.config/sft/scanners/bin``, which the
scanner adapters search in addition to ``PATH``. No shell profile edits needed.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from scanners._util import find_binary, tools_bin_dir, tools_home

GITHUB_API = "https://api.github.com"
USER_AGENT = "EasyScanPKG-scanner-installer"


@dataclass(frozen=True)
class Apt:
    package: str
    dnf_package: str | None = None  # None → same name on Fedora/RHEL


@dataclass(frozen=True)
class Pip:
    package: str


@dataclass(frozen=True)
class GitHubRelease:
    repo: str
    # Regex matched case-insensitively against asset names; {arch} is replaced.
    asset: str
    arch: dict[str, str]
    member: str | None = None  # binary name inside a tar.gz; None → asset is the binary
    unpack_dir: str | None = None  # extract whole archive here (relative to tools home)
    bin_path: str | None = None  # glob inside unpack_dir for the binary to link


Method = Apt | Pip | GitHubRelease


@dataclass(frozen=True)
class Tool:
    name: str  # scanner id
    binary: str
    methods: tuple[Method, ...]
    role: str
    default: bool = True


@dataclass
class ToolResult:
    name: str
    binary: str
    status: str  # present | installed | failed | skipped | planned
    path: str | None = None
    via: str | None = None
    detail: str = ""
    attempts: list[str] = field(default_factory=list)


CATALOG: tuple[Tool, ...] = (
    Tool("ruff", "ruff", (Pip("ruff"),), "Python lint"),
    Tool("bandit", "bandit", (Pip("bandit"),), "Python security"),
    Tool("semgrep", "semgrep", (Pip("semgrep"),), "Polyglot SAST"),
    Tool("pip-audit", "pip-audit", (Pip("pip-audit"),), "Python dependency CVEs"),
    Tool("shellcheck", "shellcheck", (Apt("shellcheck", "ShellCheck"), Pip("shellcheck-py")), "Shell lint"),
    Tool("hadolint", "hadolint", (Pip("hadolint-bin"), GitHubRelease(
        "hadolint/hadolint", r"hadolint-linux-{arch}", {"x86_64": "x86_64", "arm64": "arm64"},
    )), "Dockerfile lint"),
    Tool("cppcheck", "cppcheck", (Apt("cppcheck"),), "C/C++ static analysis"),
    Tool("flawfinder", "flawfinder", (Apt("flawfinder"), Pip("flawfinder")), "C/C++ risky-API heuristics"),
    Tool("clang-tidy", "clang-tidy", (Apt("clang-tidy", "clang-tools-extra"),), "C/C++ clang-tidy"),
    Tool("clang-analyzer", "scan-build", (Apt("clang-tools", "clang-analyzer"),), "Clang Static Analyzer"),
    Tool("valgrind", "valgrind", (Apt("valgrind"),), "Memcheck (dynamic)"),
    Tool("gitleaks", "gitleaks", (Apt("gitleaks"), GitHubRelease(
        "gitleaks/gitleaks", r"gitleaks_[0-9.]+_linux_{arch}\.tar\.gz", {"x86_64": "x64", "arm64": "arm64"},
        member="gitleaks",
    )), "Secrets detection"),
    Tool("osv", "osv-scanner", (GitHubRelease(
        "google/osv-scanner", r"osv-scanner_linux_{arch}", {"x86_64": "amd64", "arm64": "arm64"},
    ),), "Lockfile CVEs (OSV)"),
    Tool("drmemory", "drmemory", (GitHubRelease(
        "DynamoRIO/drmemory", r"DrMemory-Linux-[0-9.-]+\.tar\.gz", {"x86_64": ""},
        unpack_dir="opt/drmemory", bin_path="*/bin64/drmemory",
    ),), "Dr. Memory (dynamic, overlaps valgrind)", default=False),
)

# Scanners with nothing to install; reported so the status table is complete.
BUILTIN_NOTES = {
    "asan": ("gcc", "clang"),
    "ubsan": ("gcc", "clang"),
    "sonar": ("docker",),
}


def catalog_by_name() -> dict[str, Tool]:
    return {tool.name: tool for tool in CATALOG}


def machine_arch() -> str | None:
    raw = platform.machine().lower()
    if raw in {"x86_64", "amd64"}:
        return "x86_64"
    if raw in {"aarch64", "arm64"}:
        return "arm64"
    return None


def _method_label(method: Method) -> str:
    if isinstance(method, Apt):
        return f"apt:{method.package}"
    if isinstance(method, Pip):
        return f"pip:{method.package}"
    return f"github:{method.repo}"


# --------------------------------------------------------------------------- runner


class Runner:
    """Executes (or, in dry-run, prints) install commands."""

    def __init__(self, *, dry_run: bool = False, interactive: bool = True, log: Callable[[str], None] = print):
        self.dry_run = dry_run
        self.interactive = interactive
        self.log = log

    def run(self, cmd: Sequence[str], *, env: dict[str, str] | None = None, timeout: int = 1800) -> bool:
        self.log("    $ " + " ".join(cmd))
        if self.dry_run:
            return True
        full_env = os.environ.copy()
        full_env.update(env or {})
        try:
            proc = subprocess.run(list(cmd), env=full_env, check=False, timeout=timeout,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except (OSError, subprocess.SubprocessError) as exc:
            self.log(f"      failed: {exc}")
            return False
        if proc.returncode != 0:
            tail = (proc.stdout or "").strip().splitlines()[-5:]
            for line in tail:
                self.log(f"      {line}")
        return proc.returncode == 0


# --------------------------------------------------------------------------- system packages


def _package_manager() -> str | None:
    if shutil.which("apt-get"):
        return "apt"
    if shutil.which("dnf"):
        return "dnf"
    return None


def _privilege_prefix(interactive: bool) -> list[str] | None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    if not shutil.which("sudo"):
        return None
    return ["sudo"] if interactive and sys.stdin.isatty() else ["sudo", "-n"]


class SystemPackages:
    def __init__(self, runner: Runner):
        self.runner = runner
        self.manager = _package_manager()
        self.prefix = _privilege_prefix(runner.interactive)
        self._updated = False

    @property
    def usable(self) -> tuple[bool, str]:
        if self.manager is None:
            return False, "no apt-get/dnf on this system"
        if self.prefix is None:
            return False, "not root and sudo is unavailable"
        return True, ""

    def _pkg_name(self, method: Apt) -> str:
        if self.manager == "dnf":
            return method.dnf_package or method.package
        return method.package

    def install(self, methods: Iterable[Apt]) -> set[str]:
        """Install packages (batch, then one by one on failure). Returns installed names."""
        wanted = sorted({self._pkg_name(m) for m in methods})
        if not wanted or not self.usable[0]:
            return set()
        assert self.prefix is not None
        env_prefix = ["env", "DEBIAN_FRONTEND=noninteractive"]
        if self.manager == "apt":
            if not self._updated:
                self.runner.run([*self.prefix, *env_prefix, "apt-get", "update", "-q"])
                self._updated = True
            base = [*self.prefix, *env_prefix, "apt-get", "install", "-y", "-q", "--no-install-recommends"]
        else:
            base = [*self.prefix, "dnf", "install", "-y", "-q"]
        if self.runner.run([*base, *wanted]):
            return set(wanted)
        return {pkg for pkg in wanted if self.runner.run([*base, pkg])}


# --------------------------------------------------------------------------- pip venv


class PipVenv:
    def __init__(self, runner: Runner, system: SystemPackages):
        self.runner = runner
        self.system = system
        self.venv = tools_home() / "venv"

    @property
    def python(self) -> Path:
        return self.venv / "bin" / "python"

    def ensure(self) -> tuple[bool, str]:
        if self.python.is_file():
            return True, ""
        self.venv.parent.mkdir(parents=True, exist_ok=True)
        if self.runner.run([sys.executable, "-m", "venv", str(self.venv)]):
            return True, ""
        # Debian/Ubuntu ship venv without ensurepip unless python3-venv is installed.
        shutil.rmtree(self.venv, ignore_errors=True)
        if self.system.manager == "apt" and self.system.install([Apt("python3-venv")]):
            if self.runner.run([sys.executable, "-m", "venv", str(self.venv)]):
                return True, ""
        return False, "could not create virtualenv (install python3-venv)"

    def install(self, packages: Sequence[str]) -> set[str]:
        ok, _ = self.ensure()
        if not ok or not packages:
            return set()
        base = [str(self.python), "-m", "pip", "install", "-q", "--upgrade", "--disable-pip-version-check"]
        if self.runner.run([*base, *packages]):
            return set(packages)
        return {pkg for pkg in packages if self.runner.run([*base, pkg])}

    def link(self, binary: str) -> str | None:
        src = self.venv / "bin" / binary
        if self.runner.dry_run:
            return str(tools_bin_dir() / binary)
        if not src.is_file():
            return None
        return _link_into_bin(src, binary)


def _link_into_bin(src: Path, binary: str) -> str:
    bin_dir = tools_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    dst = bin_dir / binary
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.symlink_to(src)
    return str(dst)


# --------------------------------------------------------------------------- GitHub releases


def _github_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def select_asset(release: dict[str, Any], method: GitHubRelease, arch: str) -> dict[str, Any] | None:
    """Pick the release asset for this platform, ignoring checksum/signature files."""
    if arch not in method.arch:
        return None
    pattern = re.compile("^" + method.asset.replace("{arch}", re.escape(method.arch[arch])) + "$", re.IGNORECASE)
    for asset in release.get("assets") or []:
        name = str(asset.get("name") or "")
        if pattern.match(name):
            return asset
    return None


def parse_checksums(text: str, asset_name: str) -> str | None:
    """Find ``asset_name`` in a sha256sum-style listing."""
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == asset_name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
        if len(parts) == 1 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()  # per-asset .sha256 file
    return None


def expected_sha256(release: dict[str, Any], asset: dict[str, Any], fetch_text: Callable[[str], str]) -> str | None:
    digest = str(asset.get("digest") or "")
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1].lower()
    name = str(asset.get("name"))
    for other in release.get("assets") or []:
        other_name = str(other.get("name") or "").lower()
        if other_name in {f"{name.lower()}.sha256", f"{name.lower()}.sha256sum"} or re.search(
            r"(checksums?\.txt|sha256sums?(\.txt)?)$", other_name
        ):
            found = parse_checksums(fetch_text(str(other["browser_download_url"])), name)
            if found:
                return found
    return None


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace")


def _download(url: str, dest: Path) -> str:
    sha = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp, dest.open("wb") as fh:
        while chunk := resp.read(1 << 20):
            sha.update(chunk)
            fh.write(chunk)
    return sha.hexdigest()


def _safe_extract(archive: tarfile.TarFile, dest: Path) -> None:
    root = dest.resolve()
    for member in archive.getmembers():
        target = (dest / member.name).resolve()
        if root not in target.parents and target != root:
            raise RuntimeError(f"unsafe path in archive: {member.name}")
        if member.issym() or member.islnk():
            link = (target.parent / member.linkname).resolve()
            if root not in link.parents and link != root:
                raise RuntimeError(f"unsafe link in archive: {member.name}")
    if hasattr(tarfile, "data_filter"):
        archive.extractall(dest, filter="data")
    else:  # pragma: no cover — Python < 3.11.4
        archive.extractall(dest)


def install_github(method: GitHubRelease, binary: str, *, runner: Runner, allow_unverified: bool) -> tuple[str | None, str]:
    if platform.system() != "Linux":
        return None, "GitHub binaries are wired for Linux only"
    arch = machine_arch()
    if not arch or arch not in method.arch:
        return None, f"no {method.repo} build for {platform.machine()}"
    url = f"{GITHUB_API}/repos/{method.repo}/releases/latest"
    if runner.dry_run:
        runner.log(f"    GET {url} → asset /{method.asset}/ → verify sha256 → {tools_bin_dir() / binary}")
        return str(tools_bin_dir() / binary), "dry-run"
    try:
        release = _github_json(url)
    except OSError as exc:
        return None, f"GitHub API unreachable: {exc}"
    asset = select_asset(release, method, arch)
    if not asset:
        return None, f"no matching asset in {method.repo} {release.get('tag_name')}"
    try:
        expected = expected_sha256(release, asset, _fetch_text)
    except OSError as exc:
        return None, f"checksum lookup failed: {exc}"
    if not expected and not allow_unverified:
        return None, "release publishes no SHA-256 (use --allow-unverified to install anyway)"

    home = tools_home()
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=home, prefix="dl-") as tmp:
        tmp_path = Path(tmp)
        archive_path = tmp_path / str(asset["name"])
        runner.log(f"    downloading {asset['browser_download_url']}")
        try:
            actual = _download(str(asset["browser_download_url"]), archive_path)
        except OSError as exc:
            return None, f"download failed: {exc}"
        if expected and actual != expected:
            return None, f"SHA-256 mismatch (expected {expected[:12]}…, got {actual[:12]}…)"

        if method.unpack_dir:
            dest = home / method.unpack_dir
            shutil.rmtree(dest, ignore_errors=True)
            dest.mkdir(parents=True)
            with tarfile.open(archive_path) as archive:
                _safe_extract(archive, dest)
            matches = sorted(dest.glob(method.bin_path or binary))
            if not matches:
                return None, f"{binary} not found in archive"
            return _link_into_bin(matches[0], binary), f"{release.get('tag_name')}"

        if method.member:
            with tarfile.open(archive_path) as archive:
                member = next((m for m in archive.getmembers() if m.isfile() and Path(m.name).name == method.member), None)
                if member is None:
                    return None, f"{method.member} not found in archive"
                extracted = archive.extractfile(member)
                assert extracted is not None
                payload = extracted.read()
        else:
            payload = archive_path.read_bytes()

    bin_dir = tools_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    dst = bin_dir / binary
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.write_bytes(payload)
    dst.chmod(0o755)
    return str(dst), f"{release.get('tag_name')}"


# --------------------------------------------------------------------------- orchestration


def select_tools(
    *,
    only: Sequence[str] | None = None,
    skip: Sequence[str] | None = None,
    with_optional: Sequence[str] | None = None,
) -> list[Tool]:
    catalog = catalog_by_name()
    unknown = [n for n in [*(only or []), *(skip or []), *(with_optional or [])] if n not in catalog]
    if unknown:
        raise ValueError(f"unknown tool(s): {', '.join(unknown)} (known: {', '.join(catalog)})")
    if only:
        chosen = [catalog[n] for n in only]
    else:
        extra = set(with_optional or [])
        chosen = [t for t in CATALOG if t.default or t.name in extra]
    skipped = set(skip or [])
    return [t for t in chosen if t.name not in skipped]


def status(tools: Sequence[Tool] | None = None) -> list[ToolResult]:
    out: list[ToolResult] = []
    for tool in tools or CATALOG:
        path = find_binary(tool.binary)
        out.append(ToolResult(tool.name, tool.binary, "present" if path else "missing", path))
    return out


def install_tools(
    tools: Sequence[Tool],
    *,
    runner: Runner,
    use_system: bool = True,
    use_pip: bool = True,
    use_github: bool = True,
    allow_unverified: bool = False,
) -> list[ToolResult]:
    results = {t.name: ToolResult(t.name, t.binary, "missing") for t in tools}
    pending: list[Tool] = []
    for tool in tools:
        path = find_binary(tool.binary)
        if path:
            results[tool.name].status, results[tool.name].path = "present", path
        else:
            pending.append(tool)

    system = SystemPackages(runner)
    venv = PipVenv(runner, system)

    def resolved(tool: Tool) -> bool:
        return results[tool.name].status in {"installed", "planned"}

    def mark(tool: Tool, via: str, path: str | None) -> None:
        res = results[tool.name]
        res.status = "planned" if runner.dry_run else "installed"
        res.via, res.path = via, path

    # Try each tool's methods in order, batching every tool that shares a method tier.
    for tier in range(max((len(t.methods) for t in pending), default=0)):
        tier_tools = [t for t in pending if not resolved(t) and tier < len(t.methods)]

        apt_tools = [t for t in tier_tools if isinstance(t.methods[tier], Apt)]
        if apt_tools:
            ok, why = system.usable
            if not use_system or not ok:
                for t in apt_tools:
                    results[t.name].attempts.append(f"{_method_label(t.methods[tier])}: {why or 'disabled'}")
            else:
                runner.log(f"==> {system.manager}: {', '.join(t.name for t in apt_tools)}")
                installed = system.install([t.methods[tier] for t in apt_tools])  # type: ignore[misc]
                for t in apt_tools:
                    pkg = system._pkg_name(t.methods[tier])  # type: ignore[arg-type]
                    path = find_binary(t.binary) if not runner.dry_run else f"/usr/bin/{t.binary}"
                    if pkg in installed and path:
                        mark(t, _method_label(t.methods[tier]), path)
                    else:
                        results[t.name].attempts.append(f"{_method_label(t.methods[tier])}: failed")

        pip_tools = [t for t in tier_tools if isinstance(t.methods[tier], Pip)]
        if pip_tools:
            if not use_pip:
                for t in pip_tools:
                    results[t.name].attempts.append(f"{_method_label(t.methods[tier])}: disabled")
            else:
                runner.log(f"==> pip (venv {venv.venv}): {', '.join(t.name for t in pip_tools)}")
                installed = venv.install([t.methods[tier].package for t in pip_tools])  # type: ignore[union-attr]
                for t in pip_tools:
                    link = venv.link(t.binary) if t.methods[tier].package in installed else None  # type: ignore[union-attr]
                    if link:
                        mark(t, _method_label(t.methods[tier]), link)
                    else:
                        results[t.name].attempts.append(f"{_method_label(t.methods[tier])}: failed")

        for t in [t for t in tier_tools if isinstance(t.methods[tier], GitHubRelease)]:
            method = t.methods[tier]
            if not use_github:
                results[t.name].attempts.append(f"{_method_label(method)}: disabled")
                continue
            runner.log(f"==> github release: {t.name}")
            path, detail = install_github(method, t.binary, runner=runner, allow_unverified=allow_unverified)  # type: ignore[arg-type]
            if path:
                mark(t, f"{_method_label(method)}@{detail}", path)
            else:
                results[t.name].attempts.append(f"{_method_label(method)}: {detail}")

    for tool in pending:
        res = results[tool.name]
        if res.status == "missing":
            res.status = "failed"
            res.detail = "; ".join(res.attempts) or "no install method"
    return [results[t.name] for t in tools]


def builtin_notes() -> dict[str, str]:
    notes = {}
    for name, binaries in BUILTIN_NOTES.items():
        found = next((b for b in binaries if shutil.which(b)), None)
        notes[name] = f"uses {found}" if found else f"needs one of: {', '.join(binaries)}"
    return notes


def format_results(results: Sequence[ToolResult]) -> str:
    width = max((len(r.name) for r in results), default=8)
    lines = []
    for r in results:
        where = r.path or r.detail or ""
        via = f" [{r.via}]" if r.via else ""
        lines.append(f"  {r.name.ljust(width)}  {r.status:9} {where}{via}")
    return "\n".join(lines)
