"""Detect SonarQube language analyzer capabilities (CFamily, etc.)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class LanguageSupport:
    url: str
    languages: list[str]
    has_c: bool
    has_cpp: bool
    has_objc: bool
    has_julia: bool
    has_assembly: bool
    build_wrapper_url: str | None
    cfamily_available: bool
    notes: list[str]


def probe_language_support(url: str, token: str | None = None) -> LanguageSupport:
    base = url.rstrip("/")
    languages: list[str] = []
    try:
        req = urllib.request.Request(f"{base}/api/languages/list")
        if token:
            import base64

            req.add_header(
                "Authorization",
                "Basic " + base64.b64encode(f"{token}:".encode()).decode("ascii"),
            )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            languages = [x.get("key", "") for x in data.get("languages", [])]
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        languages = []

    keys = set(languages)
    has_c = "c" in keys
    has_cpp = "cpp" in keys
    has_objc = "objc" in keys or "objectivec" in keys
    has_julia = "julia" in keys
    has_assembly = any(k in keys for k in ("asm", "assembly", "s", "nasm"))
    cfamily = has_c or has_cpp or has_objc

    bw = f"{base}/static/cpp/build-wrapper-linux-x86.zip"
    bw_ok = False
    try:
        with urllib.request.urlopen(bw, timeout=5) as resp:
            bw_ok = resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        bw_ok = False

    notes: list[str] = []
    if not cfamily:
        notes.append(
            "C/C++/Objective-C (CFamily) is not in this SonarQube edition/image. "
            "Community `sonarqube:community` often lacks it; use Developer+ Server, "
            "SonarCloud, or SonarQube for IDE C/C++ connected mode where licensed."
        )
    else:
        notes.append(
            "CFamily present. Prefer AutoConfig for quick scans, or Build Wrapper / "
            "compile_commands.json for highest accuracy."
        )
    if not has_julia:
        notes.append(
            "Julia is not supported by SonarQube (no official analyzer). "
            "Use Semgrep/JuliaHub guidance outside Sonar, or track language interest on Sonar Community."
        )
    if not has_assembly:
        notes.append(
            "Assembly/ASM has no first-party Sonar analyzer. Treat .s/.S/.asm as unscanned "
            "or review manually; do not expect quality-gate coverage."
        )

    return LanguageSupport(
        url=base,
        languages=sorted(keys),
        has_c=has_c,
        has_cpp=has_cpp,
        has_objc=has_objc,
        has_julia=has_julia,
        has_assembly=has_assembly,
        build_wrapper_url=bw if bw_ok else None,
        cfamily_available=cfamily,
        notes=notes,
    )


def install_build_wrapper(url: str, dest_dir: Path) -> Path | None:
    """Download build-wrapper from the server when CFamily static assets exist."""
    import zipfile
    import io

    support = probe_language_support(url)
    if not support.build_wrapper_url:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(support.build_wrapper_url, timeout=60) as resp:
        raw = resp.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        zf.extractall(dest_dir)
    # Common layout: build-wrapper-linux-x86/build-wrapper-linux-x86-64
    for candidate in dest_dir.rglob("build-wrapper-linux-x86-64"):
        candidate.chmod(candidate.stat().st_mode | 0o111)
        return candidate
    for candidate in dest_dir.rglob("build-wrapper*"):
        if candidate.is_file():
            candidate.chmod(candidate.stat().st_mode | 0o111)
            return candidate
    return dest_dir


def support_as_dict(support: LanguageSupport) -> dict:
    return asdict(support)
