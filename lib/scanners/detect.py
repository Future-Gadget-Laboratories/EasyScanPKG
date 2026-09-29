"""Detect what a workspace contains so the harness can route files to scanners."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Directories never worth scanning (VCS, caches, virtualenvs, build output, EasyScan state).
DEFAULT_EXCLUDE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".sft",
        ".venv",
        "venv",
        "CMakeFiles",
        ".tox",
        ".nox",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "node_modules",
        "bower_components",
        ".gradle",
        ".idea",
        ".vscode",
        "dist",
        "site-packages",
    }
)

MAX_FILES = 200_000

PYTHON_EXT = {".py", ".pyi"}
SHELL_EXT = {".sh", ".bash", ".ksh", ".bats"}
C_EXT = {".c", ".h"}
CPP_EXT = {".cc", ".cpp", ".cxx", ".c++", ".hh", ".hpp", ".hxx", ".h++", ".ipp", ".tpp"}
OTHER_CODE_EXT = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".rs": "rust",
    ".cs": "csharp",
    ".scala": "scala",
    ".swift": "swift",
    ".tf": "terraform",
}
SHELL_INTERPRETERS = {"sh", "bash", "dash", "ksh", "zsh"}
PYTHON_REQUIREMENTS_PREFIX = "requirements"
# Manifests / lockfiles osv-scanner understands.
DEPENDENCY_MANIFESTS = frozenset(
    {
        "requirements.txt",
        "Pipfile.lock",
        "poetry.lock",
        "pdm.lock",
        "uv.lock",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "go.mod",
        "Cargo.lock",
        "Gemfile.lock",
        "composer.lock",
        "pom.xml",
        "gradle.lockfile",
        "buildscript-gradle.lockfile",
        "packages.lock.json",
        "mix.lock",
        "pubspec.lock",
        "renv.lock",
        "conan.lock",
    }
)
COMPILE_DB_DIRS = ("", "build", "out", "cmake-build-debug", "cmake-build-release", "builddir")


@dataclass
class ProjectProfile:
    """Files the harness found, grouped by the scanners that care about them."""

    workspace: Path
    python_files: list[str] = field(default_factory=list)
    python_scripts: list[str] = field(default_factory=list)  # extensionless, python shebang
    shell_files: list[str] = field(default_factory=list)
    c_files: list[str] = field(default_factory=list)
    cpp_files: list[str] = field(default_factory=list)
    dockerfiles: list[str] = field(default_factory=list)
    requirements_files: list[str] = field(default_factory=list)
    dependency_manifests: list[str] = field(default_factory=list)
    compile_commands: str | None = None
    other_languages: set[str] = field(default_factory=set)
    file_count: int = 0
    truncated: bool = False

    @property
    def has_python(self) -> bool:
        return bool(self.python_files or self.python_scripts)

    @property
    def has_c_family(self) -> bool:
        return bool(self.c_files or self.cpp_files)

    @property
    def has_code(self) -> bool:
        return bool(
            self.has_python
            or self.shell_files
            or self.has_c_family
            or self.dockerfiles
            or self.other_languages
        )

    def languages(self) -> list[str]:
        langs = set(self.other_languages)
        if self.has_python:
            langs.add("python")
        if self.shell_files:
            langs.add("shell")
        if self.c_files:
            langs.add("c")
        if self.cpp_files:
            langs.add("cpp")
        if self.dockerfiles:
            langs.add("dockerfile")
        return sorted(langs)

    def python_targets(self, *, include_tests: bool = True) -> list[str]:
        """Top-level roots holding .py files plus extensionless python scripts."""
        files, scripts = self.python_files, self.python_scripts
        if not include_tests:
            files = [f for f in files if not is_test_path(f)]
            scripts = [f for f in scripts if not is_test_path(f)]
        # Ruff/bandit skip extensionless files when walking a directory, so scripts
        # are always passed explicitly alongside the directory roots.
        return _roots(files) + sorted(scripts)

    def c_family_targets(self) -> list[str]:
        return _roots(self.c_files + self.cpp_files)

    def summary(self) -> dict:
        return {
            "languages": self.languages(),
            "files_scanned": self.file_count,
            "truncated": self.truncated,
            "python_files": len(self.python_files) + len(self.python_scripts),
            "shell_files": len(self.shell_files),
            "c_cpp_files": len(self.c_files) + len(self.cpp_files),
            "dockerfiles": self.dockerfiles,
            "requirements_files": self.requirements_files,
            "dependency_manifests": self.dependency_manifests,
            "compile_commands": self.compile_commands,
        }


TEST_DIRS = frozenset({"test", "tests", "testing"})


def is_test_path(rel: str) -> bool:
    """True for files under a top-level test dir or named like a pytest module."""
    name = rel.rsplit("/", 1)[-1]
    in_test_dir = "/" in rel and _top(rel) in TEST_DIRS
    return in_test_dir or name.startswith("test_") or name.endswith("_test.py")


def _top(rel: str) -> str:
    return rel.split("/", 1)[0]


def _roots(files: Iterable[str]) -> list[str]:
    """Collapse files to their top-level directory (or the file itself at the root)."""
    roots: set[str] = set()
    for rel in files:
        roots.add(_top(rel) if "/" in rel else rel)
    return sorted(roots)


def _shebang_interpreter(path: Path) -> str | None:
    try:
        with path.open("rb") as fh:
            head = fh.read(128)
    except OSError:
        return None
    if not head.startswith(b"#!"):
        return None
    line = head[2:].split(b"\n", 1)[0].decode("utf-8", "replace").strip()
    parts = line.split()
    if not parts:
        return None
    prog = os.path.basename(parts[0])
    if prog == "env":
        args = [p for p in parts[1:] if not p.startswith("-")]
        if not args:
            return None
        prog = os.path.basename(args[0])
    return prog


def _is_dockerfile(name: str) -> bool:
    lower = name.lower()
    return (
        lower == "dockerfile"
        or lower.startswith("dockerfile.")
        or lower.endswith(".dockerfile")
        or lower == "containerfile"
    )


def excluded_dirs(extra: Iterable[str] | None = None) -> set[str]:
    out = set(DEFAULT_EXCLUDE_DIRS)
    env = os.environ.get("EASYSCAN_EXCLUDE_DIRS")
    if env:
        out.update(part.strip() for part in env.split(",") if part.strip())
    if extra:
        out.update(str(x) for x in extra)
    return out


def _find_compile_commands(workspace: Path) -> str | None:
    for sub in COMPILE_DB_DIRS:
        candidate = workspace / sub / "compile_commands.json" if sub else workspace / "compile_commands.json"
        if candidate.is_file():
            return str(candidate.relative_to(workspace))
    for candidate in sorted(workspace.glob("build*/compile_commands.json")):
        return str(candidate.relative_to(workspace))
    return None


def _classify(profile: ProjectProfile, path: Path, rel: str, name: str) -> None:
    suffix = path.suffix.lower()
    if suffix in PYTHON_EXT:
        profile.python_files.append(rel)
    elif suffix in SHELL_EXT:
        profile.shell_files.append(rel)
    elif suffix in C_EXT:
        profile.c_files.append(rel)
    elif suffix in CPP_EXT:
        profile.cpp_files.append(rel)
    elif suffix in OTHER_CODE_EXT:
        profile.other_languages.add(OTHER_CODE_EXT[suffix])
    elif not suffix and os.access(path, os.X_OK):
        interp = _shebang_interpreter(path)
        if interp and interp.startswith("python"):
            profile.python_scripts.append(rel)
        elif interp in SHELL_INTERPRETERS:
            profile.shell_files.append(rel)

    if _is_dockerfile(name):
        profile.dockerfiles.append(rel)
    if name.startswith(PYTHON_REQUIREMENTS_PREFIX) and name.endswith(".txt"):
        profile.requirements_files.append(rel)
    if name in DEPENDENCY_MANIFESTS or (
        name.startswith(PYTHON_REQUIREMENTS_PREFIX) and name.endswith(".txt")
    ):
        profile.dependency_manifests.append(rel)


def detect_project(
    workspace: Path,
    *,
    exclude_dirs: Iterable[str] | None = None,
    max_files: int = MAX_FILES,
) -> ProjectProfile:
    """Walk the workspace once and classify files by the scanners that apply."""
    workspace = workspace.resolve()
    profile = ProjectProfile(workspace=workspace)
    skip = excluded_dirs(exclude_dirs)
    for root, dirs, files in os.walk(workspace):
        dirs[:] = sorted(d for d in dirs if d not in skip and not d.endswith(".egg-info"))
        root_path = Path(root)
        for name in sorted(files):
            if profile.file_count >= max_files:
                profile.truncated = True
                break
            path = root_path / name
            if path.is_symlink() or not path.is_file():
                continue
            profile.file_count += 1
            rel = path.relative_to(workspace).as_posix()
            _classify(profile, path, rel, name)
        if profile.truncated:
            break
    profile.compile_commands = _find_compile_commands(workspace)
    return profile
