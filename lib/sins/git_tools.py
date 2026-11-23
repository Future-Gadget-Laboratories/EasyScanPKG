"""Git-isolated snapshots and baseline multiset comparisons."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections import Counter
from contextlib import AbstractContextManager
from pathlib import Path

from . import RULES_VERSION
from .rules import RuleError

REF_RE = re.compile(r"^[A-Za-z0-9._/~^-]+$")


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git with a fixed executable and captured diagnostics."""
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, check=check)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RuleError(f"git {' '.join(args)} failed: {detail.strip()}") from exc


def validate_ref(value: str) -> str:
    """Validate a user supplied ref before passing it to git."""
    if value.startswith("-") or not REF_RE.fullmatch(value):
        raise RuleError(f"invalid git ref {value!r}")
    return value


def resolve_ref(repo: Path, ref: str) -> str:
    """Resolve a validated revision expression to a commit hash."""
    return git(repo, "rev-parse", "--verify", f"{validate_ref(ref)}^{{commit}}").stdout.strip()


def merge_base(repo: Path, ref: str) -> str:
    """Resolve merge-base of a reference and current HEAD."""
    return git(repo, "merge-base", validate_ref(ref), "HEAD").stdout.strip()


def baseline_counts(report: dict) -> dict:
    """Build the persisted fingerprint multiset document."""
    counts: dict[str, Counter[str]] = {entry["id"]: Counter() for entry in report["sins"]}
    for item in report["findings"]:
        if item["forgiven"]:
            continue
        counts.setdefault(item["sin"], Counter())[item["fingerprint"]] += 1
    return {"schema": "fgl-sins/baseline@1", "rules_version": RULES_VERSION,
            "counts": {key: dict(sorted(value.items())) for key, value in sorted(counts.items())},
            "totals": {key: sum(value.values()) for key, value in sorted(counts.items())}}


def load_baseline(path: Path) -> dict[str, Counter[str]]:
    """Load and validate baseline count data."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuleError(f"{path}: invalid baseline: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != "fgl-sins/baseline@1" or not isinstance(data.get("counts"), dict):
        raise RuleError(f"{path}: unsupported baseline schema")
    result: dict[str, Counter[str]] = {}
    for sin_id, values in data["counts"].items():
        if not isinstance(sin_id, str) or not isinstance(values, dict) or any(
            not isinstance(key, str) or not isinstance(value, int) or isinstance(value, bool) or value < 0
            for key, value in values.items()
        ):
            raise RuleError(f"{path}: malformed baseline counts")
        result[sin_id] = Counter(values)
    return result


def mark_new(report: dict, base: dict[str, Counter[str]]) -> None:
    """Annotate findings and aggregate multiset new counts into the report."""
    head: dict[str, Counter[str]] = {}
    for item in report["findings"]:
        if not item["forgiven"]:
            head.setdefault(item["sin"], Counter())[item["fingerprint"]] += 1
    new_by_sin = {sin_id: sum(max(0, count - base.get(sin_id, Counter())[fp])
                              for fp, count in values.items()) for sin_id, values in head.items()}
    remaining: dict[str, Counter[str]] = {}
    for item in report["findings"]:
        item["new"] = False
        if item["forgiven"]:
            continue
        sin_id, fp = item["sin"], item["fingerprint"]
        seen = remaining.setdefault(sin_id, Counter())[fp]
        remaining[sin_id][fp] += 1
        if seen >= base.get(sin_id, Counter())[fp]:
            item["new"] = True
    for entry in report["sins"]:
        entry["count_new"] = new_by_sin.get(entry["id"], 0)
    report["totals"]["new"] = sum(new_by_sin.values())


def worktree(repo: Path, commit: str) -> AbstractContextManager[Path]:
    """Yield a detached temporary worktree, removing it even on failures."""
    class Snapshot:
        def __enter__(self) -> Path:
            self.temp = tempfile.TemporaryDirectory(prefix="sins-worktree-")
            self.path = Path(self.temp.name) / "repo"
            try:
                git(repo, "worktree", "add", "--detach", str(self.path), resolve_ref(repo, commit))
            except Exception:
                self.temp.cleanup()
                raise
            return self.path

        def __exit__(self, *_exc: object) -> bool:
            git(repo, "worktree", "remove", "--force", str(self.path), check=False)
            self.temp.cleanup()
            return False
    return Snapshot()
