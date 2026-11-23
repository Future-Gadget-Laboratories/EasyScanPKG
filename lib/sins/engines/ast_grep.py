"""Pinned ast-grep installation, resolution, and structural scanning."""

from __future__ import annotations

import hashlib
import io
import ast
import json
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import tokenize
import tomllib
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from ..policy import context_anchor, fingerprint
from ..regex_engine import Finding, _glob_match, _included, _is_skipped_path
from ..rules import Rule, RuleError

PINS = Path(__file__).with_name("pins.toml")
DEFAULT_ROOT = Path.home() / ".local" / "share" / "fgl-sins" / "bin"
TIMEOUT_SECONDS = 120
CHUNK_SIZE = 100


class EngineError(RuleError):
    """Pinned engine unavailable or failed to produce valid results."""


def _pins() -> dict[str, Any]:
    """Load the checked-in engine manifest."""
    return tomllib.loads(PINS.read_text(encoding="utf-8"))


def _platform_key() -> str:
    """Map the host platform to its manifest key."""
    os_name = {"Linux": "linux", "Darwin": "macos"}.get(platform.system())
    arch = {"x86_64": "x86_64", "AMD64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(
        platform.machine())
    key = f"{os_name}-{arch}"
    if os_name is None or arch is None or key not in _pins()["platforms"]:
        raise EngineError(f"ast-grep: unsupported platform {platform.system()} {platform.machine()}")
    return key


def _sha256(path: Path) -> str:
    """Hash a file in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pinned_directory(dest: Path | None = None) -> Path:
    """Return the destination directory for the pinned version."""
    return dest if dest is not None else DEFAULT_ROOT / f"ast-grep-{_pins()['version']}"


def _version(binary: Path) -> str:
    """Read and validate the engine's one-line version response."""
    try:
        result = subprocess.run([str(binary), "--version"], check=False, capture_output=True,
                                text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineError(f"ast-grep: cannot run {binary}; run `sins engines install`: {exc}") from exc
    if result.returncode != 0:
        raise EngineError(f"ast-grep: version check failed for {binary}; run `sins engines install`")
    return result.stdout.strip().split()[-1] if result.stdout.strip() else ""


def resolve_binary() -> Path:
    """Resolve only the explicit override or the pinned default path."""
    binary = Path(os.environ.get("SINS_AST_GREP", "")) if os.environ.get("SINS_AST_GREP") else (
        pinned_directory() / "ast-grep")
    binary = binary.expanduser().resolve()
    if not binary.is_file():
        raise EngineError(f"ast-grep not installed at {binary}; run `sins engines install`")
    manifest = _pins()
    try:
        actual_hash = _sha256(binary)
    except OSError as exc:
        raise EngineError(f"ast-grep cannot be read at {binary}; run `sins engines install`: {exc}") from exc
    expected_hash = manifest["platforms"].get(_platform_key(), {}).get("binary_sha256")
    stamp = binary.parent / "ast-grep.sha256"
    if expected_hash is None and stamp.is_file():
        try:
            expected_hash = stamp.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise EngineError(f"ast-grep stamp is unreadable at {stamp}; run `sins engines install`: {exc}") from exc
    if expected_hash is None or actual_hash != expected_hash:
        raise EngineError(f"ast-grep sha256 mismatch or missing stamp for {binary}; run `sins engines install`")
    expected_version = manifest["version"]
    actual_version = _version(binary)
    if actual_version != expected_version:
        raise EngineError(f"ast-grep version {actual_version!r} does not match pinned {expected_version}; "
                          "run `sins engines install`")
    return binary


def _download(url: str) -> bytes:
    """Fetch an HTTPS release, allowing local fixtures only when explicitly enabled."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    allowed = parsed.scheme == "https" or (parsed.scheme == "file" and os.environ.get("SINS_ALLOW_FILE_URL") == "1")
    if not allowed:
        raise EngineError("ast-grep installer accepts HTTPS URLs only")
    try:
        if parsed.scheme == "file":
            from urllib.request import url2pathname
            local_path = Path(url2pathname(parsed.path))
            return local_path.read_bytes()[:100 * 1024 * 1024 + 1]
        else:
            class HttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
                """Reject redirects that downgrade a pinned HTTPS download."""

                def redirect_request(self, request: Any, file_pointer: Any, code: int, message: str,
                                     headers: Any, new_url: str) -> Any:
                    if urlparse(new_url).scheme != "https":
                        raise EngineError("ast-grep download redirect must remain HTTPS")
                    return super().redirect_request(request, file_pointer, code, message, headers, new_url)

            response_context = urllib.request.build_opener(HttpsRedirectHandler).open(url, timeout=60)
        with response_context as response:
            return response.read(100 * 1024 * 1024 + 1)
    except OSError as exc:
        raise EngineError(f"ast-grep download failed: {exc}") from exc


def install(dest: Path | None = None) -> Path:
    """Install the pinned binary atomically after verifying its release archive."""
    key = _platform_key()
    platform_data = _pins()["platforms"][key]
    payload = _download(platform_data["url"])
    if len(payload) > 100 * 1024 * 1024:
        raise EngineError("ast-grep archive exceeds 100 MiB limit")
    archive_hash = hashlib.sha256(payload).hexdigest()
    if archive_hash != platform_data["zip_sha256"]:
        raise EngineError(f"ast-grep archive sha256 mismatch; expected {platform_data['zip_sha256']}, got {archive_hash}")
    destination = pinned_directory(dest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".ast-grep-install-", dir=destination.parent))
    binary = stage / "ast-grep"
    try:
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
                members = [item for item in bundle.infolist() if item.filename == "ast-grep"]
                if len(members) != 1 or members[0].is_dir() or members[0].file_size > 100 * 1024 * 1024:
                    raise EngineError("ast-grep archive must contain exactly one ast-grep member")
                binary.write_bytes(bundle.read(members[0]))
        except (zipfile.BadZipFile, OSError, KeyError) as exc:
            raise EngineError(f"ast-grep archive is invalid: {exc}") from exc
        digest = _sha256(binary)
        expected_binary = platform_data.get("binary_sha256")
        if expected_binary and digest != expected_binary:
            raise EngineError("ast-grep extracted binary sha256 mismatch")
        binary.chmod(0o755)
        (stage / "ast-grep.sha256").write_text(digest + "\n", encoding="ascii")
        _replace_directory(stage, destination)
    except Exception:
        if stage.exists():
            for child in stage.iterdir():
                child.unlink()
            stage.rmdir()
        raise
    return destination / "ast-grep"


def status() -> dict[str, Any]:
    """Return resolved binary status without consulting PATH."""
    try:
        binary = resolve_binary()
    except EngineError as exc:
        return {"path": str(pinned_directory() / "ast-grep"), "version": None,
                "sha256": None, "hash_ok": False, "error": str(exc)}
    return {"path": str(binary), "version": _version(binary), "sha256": _sha256(binary), "hash_ok": True}


def _replace_directory(stage: Path, destination: Path) -> None:
    """Swap a staged engine into place and restore the previous install on failure."""
    backup_root: Path | None = None
    backup: Path | None = None
    if destination.exists() or destination.is_symlink():
        backup_root = Path(tempfile.mkdtemp(prefix=".ast-grep-backup-", dir=destination.parent))
        backup = backup_root / destination.name
        destination.rename(backup)
    try:
        stage.rename(destination)
    except OSError:
        if backup is not None and not destination.exists():
            backup.rename(destination)
            shutil.rmtree(backup_root)
        raise
    if backup_root is not None:
        shutil.rmtree(backup_root)


def _language(language: str) -> str:
    """Map sins language identifiers to ast-grep language names."""
    values = {"python": "Python", "cpp": "Cpp", "bash": "Bash", "yaml": "Yaml"}
    if language not in values:
        raise EngineError(f"ast-grep does not support language {language!r}")
    return values[language]


def _eligible_files(repo: Path, rule: Rule, excludes: tuple[str, ...]) -> list[Path]:
    """Enumerate files matching both rule scopes before invoking ast-grep."""
    selected: list[Path] = []
    for directory, dirs, files in os.walk(repo, topdown=True):
        relative_dir = Path(directory).relative_to(repo).parts
        dirs[:] = sorted(name for name in dirs if not _is_skipped_path(relative_dir + (name,)))
        for name in sorted(files):
            file = Path(directory) / name
            relative = file.relative_to(repo).as_posix()
            if (_included(relative, rule) and not any(_glob_match(relative, item) for item in excludes)
                    and _is_scannable_file(file)):
                selected.append(file)
    return selected


def _is_scannable_file(file: Path) -> bool:
    """Return whether a path is a bounded, non-binary regular file."""
    try:
        info = file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
            return False
        with file.open("rb") as stream:
            return b"\0" not in stream.read(8192)
    except OSError:
        return False


def _invoke(binary: Path, repo: Path, rule: Rule, paths: list[Path]) -> list[dict[str, Any]]:
    """Run ast-grep in bounded path chunks and parse stream JSON."""
    hits: list[dict[str, Any]] = []
    inline = json.dumps({"id": rule.id, "language": _language(rule.language), "rule": rule.ast_rule})
    for start in range(0, len(paths), CHUNK_SIZE):
        command = [str(binary), "scan", "--inline-rules", inline, "--json=stream", "--threads", "2", "--",
                   *[path.relative_to(repo).as_posix() for path in paths[start:start + CHUNK_SIZE]]]
        try:
            result = subprocess.run(command, cwd=repo, capture_output=True, text=True,
                                    timeout=TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EngineError(f"ast-grep failed for {rule.id}: {exc}") from exc
        if result.returncode != 0:
            raise EngineError(f"ast-grep failed for {rule.id} (exit {result.returncode}): {result.stderr.strip()}")
        for line in result.stdout.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EngineError(f"ast-grep returned invalid JSON for {rule.id}") from exc
            if not isinstance(row, dict):
                raise EngineError(f"ast-grep returned invalid result for {rule.id}")
            hits.append(row)
    return hits


def scan_rule(repo: Path, rule: Rule, global_excludes: tuple[str, ...] = ()) -> list[Finding]:
    """Scan files with the pinned structural engine and normalize results."""
    binary = resolve_binary()
    root = repo.resolve()
    paths = _eligible_files(root, rule, global_excludes)
    rows = _invoke(binary, root, rule, paths) if paths else []
    return [finding for row in rows if (finding := _parse_finding(root, rule, row)) is not None]


def scan_file(repo: Path, rule: Rule, path: Path) -> list[Finding]:
    """Scan only one file after a rewrite has been applied."""
    root = repo.resolve()
    target = path.resolve()
    if not target.is_relative_to(root):
        raise EngineError(f"cannot scan out-of-repository file for {rule.id}")
    rows = _invoke(resolve_binary(), root, rule, [target])
    return [finding for row in rows if (finding := _parse_finding(root, rule, row)) is not None]


def _parse_finding(repo: Path, rule: Rule, row: dict[str, Any]) -> Finding | None:
    """Validate and normalize one ast-grep JSON match row."""
    if not isinstance(row.get("file"), str) or not isinstance(row.get("range"), dict):
        raise EngineError(f"ast-grep returned malformed location data for {rule.id}")
    start, end = row["range"].get("start"), row["range"].get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        raise EngineError(f"ast-grep returned malformed coordinates for {rule.id}")
    coords = (start.get("line"), start.get("column"), end.get("line"), end.get("column"))
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in coords):
        raise EngineError(f"ast-grep returned malformed coordinates for {rule.id}")
    matched = row.get("text")
    if not isinstance(matched, str):
        raise EngineError(f"ast-grep returned malformed match text for {rule.id}")
    file = (repo / row["file"]).resolve()
    if not file.is_relative_to(repo):
        raise EngineError(f"ast-grep returned an out-of-repository path for {rule.id}")
    relative = file.relative_to(repo).as_posix()
    if rule.id == "py-open-without-encoding" and not _open_needs_encoding(matched):
        return None
    line, col, end_line, end_col = coords[0] + 1, coords[1] + 1, coords[2] + 1, coords[3] + 1
    anchor = context_anchor(file.read_text(encoding="utf-8", errors="replace"), line)
    return Finding(rule.id, relative, line, col, end_line, end_col, rule.phrase, rule.severity,
                   fingerprint=fingerprint(rule.id, relative, matched, anchor))


def validate_parse(rule: Rule, path: Path) -> None:
    """Reject invalid rewritten source before accepting a fix."""
    if rule.language == "python":
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise EngineError(f"ast-grep rewrite produced invalid Python in {path}: {exc}") from exc
        return
    binary = resolve_binary()
    for kind in ("ERROR", "MISSING"):
        inline = json.dumps({"id": "sins-parse-check", "language": _language(rule.language),
                             "rule": {"kind": kind}})
        try:
            result = subprocess.run([str(binary), "scan", "--inline-rules", inline, "--json=stream",
                                     "--threads", "2", "--", str(path)], cwd=path.parent,
                                    capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EngineError(f"ast-grep parse check failed for {path}: {exc}") from exc
        if result.returncode != 0:
            if kind == "MISSING" and "Invalid Kind" in result.stderr:
                continue
            raise EngineError(f"ast-grep parse check failed for {path}: {result.stderr.strip()}")
        if result.stdout.strip():
            raise EngineError(f"ast-grep rewrite produced {kind} parse node in {path}")


def rewrite_text(rule: Rule, content: str) -> str:
    """Rewrite one in-memory snippet through ast-grep's native rewrite engine."""
    if rule.engine != "ast-grep" or rule.fix_kind != "rewrite":
        raise EngineError(f"rule {rule.id} has no ast-grep rewrite template")
    if rule.id == "py-open-without-encoding":
        return _rewrite_open_calls(content)[0]
    binary = resolve_binary()
    with tempfile.TemporaryDirectory(prefix="sins-rewrite-") as folder:
        source = Path(folder) / ("example" + {"python": ".py", "cpp": ".cpp", "bash": ".sh",
                                                    "yaml": ".yaml"}.get(rule.language, ".txt"))
        source.write_text(content, encoding="utf-8")
        inline = json.dumps({"id": rule.id, "language": _language(rule.language),
                             "rule": rule.ast_rule, "fix": rule.template})
        command = [str(binary), "scan", "--inline-rules", inline, "--update-all", "--threads", "2", "--",
                   str(source)]
        try:
            result = subprocess.run(command, cwd=folder, capture_output=True, text=True,
                                    timeout=TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EngineError(f"ast-grep rewrite failed for {rule.id}: {exc}") from exc
        if result.returncode != 0:
            raise EngineError(f"ast-grep rewrite failed for {rule.id}: {result.stderr.strip()}")
        return source.read_text(encoding="utf-8")


def rewrite_text_non_overlapping(rule: Rule, content: str) -> tuple[str, bool]:
    """Rewrite independent matches and report overlapping source spans."""
    if rule.id != "py-open-without-encoding":
        return rewrite_text(rule, content), False
    return _rewrite_open_calls(content, skip_overlapping=True)


def rewrite_match_spans(rule: Rule, content: str, findings: list[Finding],
                        skipped: set[int]) -> tuple[str, bool]:
    """Rewrite non-overlapping matched snippets while preserving skipped spans."""
    lines = content.splitlines(keepends=True)
    starts: list[int] = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    spans = [_finding_offsets(item, lines, starts) for item in findings]
    output = content
    for index in reversed(range(len(findings))):
        if index in skipped:
            continue
        start, end = spans[index]
        replacement = rewrite_text(rule, content[start:end])
        output = output[:start] + replacement + output[end:]
    return output, bool(skipped)


def _finding_offsets(finding: Finding, lines: list[str], starts: list[int]) -> tuple[int, int]:
    """Convert one-based UTF-8 byte coordinates to source string offsets."""
    start_line, end_line = finding.line - 1, finding.end_line - 1
    start_bytes = lines[start_line].encode("utf-8")[:finding.col - 1]
    end_bytes = lines[end_line].encode("utf-8")[:finding.end_col - 1]
    return starts[start_line] + len(start_bytes.decode("utf-8")), starts[end_line] + len(end_bytes.decode("utf-8"))


def _open_needs_encoding(source: str) -> bool:
    """Check a matched Python open call for text-mode, missing encoding use."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Expr):
        return False
    call = tree.body[0].value
    return isinstance(call, ast.Call) and _call_needs_encoding(call)


def _call_needs_encoding(call: ast.Call) -> bool:
    """Return whether a single open call has a definite text mode without encoding."""
    if not isinstance(call.func, ast.Name) or call.func.id != "open":
        return False
    if (any(isinstance(arg, ast.Starred) for arg in call.args) or len(call.args) >= 4
            or any(keyword.arg in {None, "encoding"} for keyword in call.keywords)):
        return False
    mode = call.args[1] if len(call.args) > 1 else next(
        (keyword.value for keyword in call.keywords if keyword.arg == "mode"), None)
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value:
        return False
    return bool(call.args) or any(keyword.arg == "file" for keyword in call.keywords)


def _rewrite_open_calls(source: str, *, skip_overlapping: bool = False) -> tuple[str, bool]:
    """Insert UTF-8 encoding on eligible Python open calls."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise EngineError(f"cannot rewrite invalid Python: {exc}") from exc
    lines = source.splitlines(keepends=True)
    starts: list[int] = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    calls: list[tuple[int, int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _call_needs_encoding(node):
            continue
        start = _source_offset(source, lines, starts, node.lineno, node.col_offset)
        end = _source_offset(source, lines, starts, node.end_lineno, node.end_col_offset) - 1
        segment = source[start:end + 1]
        close = segment.rfind(")")
        if close >= 0:
            calls.append((start, end + 1, start + close))
    overlapping = _overlapping_calls(calls)
    insertions = [position for index, (_start, _end, position) in enumerate(calls)
                  if not skip_overlapping or index not in overlapping]
    for position in sorted(set(insertions), reverse=True):
        prefix = source[:position]
        separator = _open_separator(prefix)
        source = source[:position] + separator + 'encoding="utf-8"' + source[position:]
    return source, bool(overlapping)


def _open_separator(prefix: str) -> str:
    """Choose the argument separator before a closing parenthesis, ignoring comments."""
    ignored = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
               tokenize.DEDENT, tokenize.ENCODING}
    last = ""
    try:
        for token in tokenize.generate_tokens(io.StringIO(prefix).readline):
            if token.type not in ignored:
                last = token.string
    except tokenize.TokenError:
        pass
    if last == "(":
        return ""
    if last == ",":
        if prefix.endswith(("\n", "\r")):
            lines = prefix.splitlines()
            indentation = next((len(line) - len(line.lstrip()) for line in reversed(lines) if line.strip()), 0)
            return " " * indentation
        return "" if prefix.endswith((" ", "\t")) else " "
    return ", "


def _overlapping_calls(calls: list[tuple[int, int, int]]) -> set[int]:
    """Mark both calls in any overlapping source span."""
    ordered = sorted(enumerate(calls), key=lambda item: (item[1][0], item[1][1]))
    overlaps: set[int] = set()
    furthest_end = -1
    furthest_index = -1
    for index, (start, end, _insert) in ordered:
        if start < furthest_end:
            overlaps.add(index)
            overlaps.add(furthest_index)
        if end > furthest_end:
            furthest_end = end
            furthest_index = index
    return overlaps


def _source_offset(source: str, lines: list[str], starts: list[int], line: int, byte_column: int) -> int:
    """Convert Python AST's UTF-8 byte column into a source string index."""
    line_text = lines[line - 1]
    byte_prefix = line_text.encode("utf-8")[:byte_column]
    try:
        character_column = len(byte_prefix.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise EngineError("Python AST returned a non-character byte offset") from exc
    return starts[line - 1] + character_column

