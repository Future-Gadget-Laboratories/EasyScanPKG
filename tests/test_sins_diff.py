"""Detached diff, ref validation, enforcement, and tampering tests."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from sins.cli import _policy_tampering, main


def run(*args: str) -> tuple[int, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        return main(list(args)), output.getvalue()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, text=True,
                          capture_output=True).stdout.strip()


def make_repo(repo: Path) -> str:
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "sins@example.invalid")
    git(repo, "config", "user.name", "sins tests")
    (repo / "a.py").write_text("try:\n    work()\nexcept:\n    pass\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", sha)
    git(repo, "switch", "-c", "feature")
    return sha


class DiffTests(unittest.TestCase):
    def test_diff_default_merge_base_preserves_dirty_tree_and_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            base = make_repo(repo)
            (repo / "a.py").write_text("# local dirty\ntry:\n    work()\nexcept:\n    pass\n", encoding="utf-8")
            (repo / "untracked.py").write_text("local\n", encoding="utf-8")
            before = git(repo, "status", "--porcelain", "-uall")
            worktrees_before = git(repo, "worktree", "list", "--porcelain")
            rc, output = run("diff", "--repo", str(repo), "--base", "origin/main", "--json")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(output)["base"], base)
            self.assertEqual(git(repo, "status", "--porcelain", "-uall"), before)
            self.assertEqual(git(repo, "worktree", "list", "--porcelain"), worktrees_before)

    def test_ref_validation_fail_new_and_tampering_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            make_repo(repo)
            self.assertEqual(run("scan", "--repo", str(repo), "--rule", "py-bare-except",
                                 "--fail-on", "new")[0], 0)
            target = repo / "a.py"
            target.write_text(target.read_text(encoding="utf-8") + "except:\n    pass\n", encoding="utf-8")
            self.assertEqual(run("diff", "--repo", str(repo), "--base=--upload-pack=x")[0], 2)
            self.assertEqual(run("scan", "--repo", str(repo), "--rule", "py-bare-except",
                                 "--fail-on", "new")[0], 1)
            (repo / ".sins").mkdir(exist_ok=True)
            (repo / ".sins/policy.toml").write_text('[sins.unknown-sin]\nenabled = false\n', encoding="utf-8")
            self.assertEqual(run("scan", "--repo", str(repo), "--rule", "py-bare-except",
                                 "--fail-on", "new")[0], 2)
            (repo / ".sins/policy.toml").unlink()
            (repo / ".sins").mkdir(exist_ok=True)
            (repo / ".sins/policy.toml").write_text('[sins."py-bare-except"]\nenabled = false\n', encoding="utf-8")
            rc, output = run("scan", "--repo", str(repo), "--fail-on", "new", "--allow-policy-change",
                             "--format", "json")
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(output)["policy_tampering"])
            self.assertTrue(json.loads(output)["warnings"])

    def test_added_suppression_and_policy_changes_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            make_repo(repo)
            file = repo / "a.py"
            file.write_text(file.read_text(encoding="utf-8") + "# noqa: T123\n", encoding="utf-8")
            rc, output = run("scan", "--repo", str(repo), "--fail-on", "new", "--format", "json")
            self.assertEqual(rc, 1)
            self.assertEqual(json.loads(output)["policy_tampering"][0]["kind"], "added-suppression")
            (repo / ".sins").mkdir()
            (repo / ".sins/baseline.json").write_text("{}\n", encoding="utf-8")
            rc, output = run("scan", "--repo", str(repo), "--fail-on", "new", "--allow-policy-change",
                             "--format", "json")
            self.assertEqual(rc, 0)
            kinds = {item["kind"] for item in json.loads(output)["policy_tampering"]}
            self.assertIn("sins-policy-change", kinds)

    def test_each_policy_weakening_category_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            make_repo(repo)
            sins = repo / ".sins"
            (sins / "rules").mkdir(parents=True)
            policy = sins / "policy.toml"
            policy.write_text('[sins.local-rule]\nmortal_threshold = 2\nenabled = true\nmortal = true\n'
                              '[paths]\nexclude = []\n', encoding="utf-8")
            rule = sins / "rules/local-rule.toml"
            rule.write_text('''id = "local-rule"
version = 1
phrase = "avoid bad markers"
description = "test rule"
language = "text"
severity = "minor"
mortal = true
mortal_threshold = 2
engine = "regex"
[paths]
include = ["**/*.txt"]
exclude = []
[match]
pattern = "bad"
[examples]
bad = ["bad"]
good = ["good"]
''', encoding="utf-8")
            (sins / "baseline.json").write_text("{}\n", encoding="utf-8")
            git(repo, "add", ".sins")
            git(repo, "commit", "-m", "policy baseline")
            git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))

            weakenings = (
                ('[sins.local-rule]\nmortal_threshold = 9\nenabled = true\nmortal = true\n[paths]\nexclude = []\n', "policy.toml"),
                ('[sins.local-rule]\nmortal_threshold = 2\nenabled = false\nmortal = true\n[paths]\nexclude = []\n', "policy.toml"),
                ('[sins.local-rule]\nmortal_threshold = 2\nenabled = true\nmortal = false\n[paths]\nexclude = []\n', "policy.toml"),
                ('[sins.local-rule]\nmortal_threshold = 2\nenabled = true\nmortal = true\n[paths]\nexclude = ["vendor/**"]\n', "policy.toml"),
            )
            for text, filename in weakenings:
                git(repo, "restore", "--source=origin/main", "--", ".sins")
                (sins / filename).write_text(text, encoding="utf-8")
                self.assertTrue(_policy_tampering(repo, "origin/main"))
            git(repo, "restore", "--source=origin/main", "--", ".sins")
            (sins / "baseline.json").write_text('{"changed": true}\n', encoding="utf-8")
            self.assertTrue(_policy_tampering(repo, "origin/main"))
            git(repo, "restore", "--source=origin/main", "--", ".sins")
            (sins / "rules/local-rule.toml").unlink()
            self.assertTrue(_policy_tampering(repo, "origin/main"))
            git(repo, "restore", "--source=origin/main", "--", ".sins")
            rule.write_text(rule.read_text(encoding="utf-8").replace('pattern = "bad"', 'pattern = "bad|worse"'),
                            encoding="utf-8")
            self.assertTrue(_policy_tampering(repo, "origin/main"))


if __name__ == "__main__":
    unittest.main()
