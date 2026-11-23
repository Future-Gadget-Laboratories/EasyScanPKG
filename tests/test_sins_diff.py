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
from sins.git_tools import worktree


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

    def test_diff_leaves_dirty_and_untracked_files_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            make_repo(repo)
            dirty = repo / "a.py"
            dirty.write_text("# changed locally\n", encoding="utf-8")
            untracked = repo / "new.py"
            untracked.write_text("local only\n", encoding="utf-8")
            before = {path.name: path.read_bytes() for path in (dirty, untracked)}
            rc, _ = run("diff", "--repo", str(repo), "--base", "origin/main", "--json")
            self.assertEqual(rc, 0)
            self.assertEqual({path.name: path.read_bytes() for path in (dirty, untracked)}, before)

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

    def test_added_suppression_kind_uses_comments_and_ignores_docs_and_strings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            make_repo(repo)
            (repo / "a.py").write_text('message = """\n# noqa: fake\n"""\n', encoding="utf-8")
            (repo / "docs.md").write_text("Example: nosem noqa NOLINT\n", encoding="utf-8")
            self.assertEqual(_policy_tampering(repo, "origin/main"), [])
            (repo / "a.py").write_text('message = """\n# noqa: fake\n"""\n# noqa: T123\n',
                                        encoding="utf-8")
            rows = _policy_tampering(repo, "origin/main")
            self.assertEqual([row["kind"] for row in rows], ["added-suppression"])

    def test_untracked_symlink_is_not_opened_for_suppressions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            make_repo(repo)
            outside = Path(temp) / "outside.py"
            outside.write_text("# noqa: external\n", encoding="utf-8")
            (repo / "linked.py").symlink_to(outside)
            self.assertEqual(_policy_tampering(repo, "origin/main"), [])

    def test_policy_artifact_tampering_kind_detects_untracked_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            make_repo(repo)
            (repo / ".sins").mkdir()
            (repo / ".sins/baseline.json").write_text("{}\n", encoding="utf-8")
            rows = _policy_tampering(repo, "origin/main")
            self.assertTrue(any(row["kind"] == "sins-policy-change" for row in rows))

    def test_new_rule_with_baseline_in_filename_is_not_baseline_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            make_repo(repo)
            (repo / ".sins/rules").mkdir(parents=True)
            (repo / ".sins/rules/baseline-helper.toml").write_text('id = "baseline-helper"\n',
                                                                    encoding="utf-8")
            self.assertEqual(_policy_tampering(repo, "origin/main"), [])

    def test_allow_policy_change_downgrades_tampering_to_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            make_repo(repo)
            (repo / "a.py").write_text("# noqa: T123\n", encoding="utf-8")
            rc, output = run("scan", "--repo", str(repo), "--fail-on", "new", "--allow-policy-change",
                             "--format", "json")
            report = json.loads(output)
            self.assertEqual(rc, 0)
            self.assertTrue(report["policy_tampering"])
            self.assertTrue(report["warnings"])

    def test_ref_validation_rejects_upload_pack_option_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            make_repo(repo)
            rc, _ = run("diff", "--repo", str(repo), "--base=--upload-pack=x")
            self.assertEqual(rc, 2)

    def test_worktree_cleanup_runs_when_snapshot_body_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            commit = make_repo(repo)
            before = git(repo, "worktree", "list", "--porcelain")
            with self.assertRaises(RuntimeError):
                with worktree(repo, commit):
                    raise RuntimeError("forced scan failure")
            self.assertEqual(git(repo, "worktree", "list", "--porcelain"), before)

    def test_diff_defaults_to_merge_base_after_origin_main_advances(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            branch_point = make_repo(repo)
            git(repo, "switch", "main")
            (repo / "upstream.py").write_text("except:\n    pass\n", encoding="utf-8")
            git(repo, "add", "upstream.py")
            git(repo, "commit", "-m", "upstream advances")
            git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))
            git(repo, "switch", "feature")
            rc, output = run("diff", "--repo", str(repo), "--base", "origin/main", "--json")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(output)["base"], branch_point)

    def test_explicit_head_ref_controls_merge_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            branch_point = make_repo(repo)
            git(repo, "switch", "main")
            (repo / "upstream.py").write_text("except:\n    pass\n", encoding="utf-8")
            git(repo, "add", "upstream.py")
            git(repo, "commit", "-m", "upstream advances")
            git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))
            rc, output = run("diff", "--repo", str(repo), "--base", "origin/main", "--head", "feature", "--json")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(output)["base"], branch_point)

    def test_configuration_error_takes_precedence_over_new_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            make_repo(repo)
            (repo / "a.py").write_text("except:\n    pass\nexcept:\n    pass\n", encoding="utf-8")
            (repo / ".sins").mkdir()
            (repo / ".sins/policy.toml").write_text("[sins.unknown]\nenabled = false\n", encoding="utf-8")
            rc, _ = run("scan", "--repo", str(repo), "--fail-on", "new")
            self.assertEqual(rc, 2)

    def test_fail_new_without_origin_main_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            git(repo, "init", "-b", "main")
            git(repo, "config", "user.email", "sins@example.invalid")
            git(repo, "config", "user.name", "sins tests")
            (repo / "a.py").write_text("except:\n", encoding="utf-8")
            git(repo, "add", ".")
            git(repo, "commit", "-m", "base")
            rc, output = run("scan", "--repo", str(repo), "--fail-on", "new", "--format", "json")
            self.assertEqual(rc, 2)
            self.assertIn("needs --base REF or an existing origin/main", output)

    def test_baseline_write_rejects_missing_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing"
            self.assertEqual(run("baseline", "write", "--repo", str(missing))[0], 2)


if __name__ == "__main__":
    unittest.main()
