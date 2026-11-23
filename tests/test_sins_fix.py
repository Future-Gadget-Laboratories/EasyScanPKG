"""Safe rewrite previews and application tests."""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sins.cli import main
from sins.engines.ast_grep import EngineError, resolve_binary
from sins.regex_engine import Finding


def invoke(*args: str) -> tuple[int, str]:
    """Capture CLI output for a focused assertion."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        return main(list(args)), output.getvalue()


def git(repo: Path, *args: str) -> None:
    """Run a local Git setup command."""
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class FixCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            resolve_binary()
        except EngineError as exc:
            raise unittest.SkipTest(f"pinned ast-grep is not installed: {exc}") from exc

    def _repo(self, folder: str) -> Path:
        repo = Path(folder) / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "sins-test@example.invalid")
        git(repo, "config", "user.name", "sins tests")
        source = repo / "src" / "a.py"
        source.parent.mkdir()
        source.write_text("with open('data.txt') as stream:\n    stream.read()\n", encoding="utf-8")
        git(repo, "add", "src/a.py")
        git(repo, "commit", "-qm", "fixture")
        return repo

    def test_dry_run_prints_diff_and_clean_apply_rewrites(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-fix-") as folder:
            repo = self._repo(folder)
            code, output = invoke("fix", "--repo", str(repo), "--sin", "py-open-without-encoding")
            self.assertEqual(code, 0)
            self.assertIn("+with open('data.txt', encoding=\"utf-8\")", output)
            self.assertIn("would change 1 file", output)
            self.assertNotIn("encoding=", (repo / "src/a.py").read_text())
            code, output = invoke("fix", "--repo", str(repo), "--sin", "py-open-without-encoding", "--apply")
            self.assertEqual(code, 0, output)
            self.assertIn("applied=1", output)
            self.assertIn('encoding="utf-8"', (repo / "src/a.py").read_text())

    def test_apply_refuses_dirty_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-fix-dirty-") as folder:
            repo = self._repo(folder)
            target = repo / "src/a.py"
            target.write_text(target.read_text() + "# local edit\n", encoding="utf-8")
            code, output = invoke("fix", "--repo", str(repo), "--sin", "py-open-without-encoding", "--apply")
            self.assertEqual(code, 2)
            self.assertIn("dirty files", output)
            self.assertNotIn("encoding=", target.read_text())

    def test_apply_refuses_untracked_ignored_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-fix-ignored-") as folder:
            repo = self._repo(folder)
            (repo / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
            git(repo, "add", ".gitignore")
            git(repo, "commit", "-qm", "ignore rule")
            target = repo / "ignored.py"
            target.write_text("open('ignored.txt')\n", encoding="utf-8")
            code, output = invoke("fix", "--repo", str(repo), "--sin", "py-open-without-encoding", "--apply")
            self.assertEqual(code, 2)
            self.assertIn("dirty files", output)
            self.assertNotIn("encoding=", target.read_text())

    def test_non_overlapping_open_rewrite_applies_beside_overlap(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-fix-mixed-overlap-") as folder:
            repo = self._repo(folder)
            target = repo / "src/a.py"
            target.write_text("open(open('nested'))\nopen('independent')\n", encoding="utf-8")
            git(repo, "add", "src/a.py")
            git(repo, "commit", "-qm", "overlap fixture")
            code, output = invoke("fix", "--repo", str(repo), "--sin", "py-open-without-encoding", "--apply")
            self.assertEqual(code, 0, output)
            self.assertIn("applied=1", output)
            self.assertIn("skipped=1", output)
            content = target.read_text(encoding="utf-8")
            self.assertIn("open(open('nested'))", content)
            self.assertIn('open(\'independent\', encoding="utf-8")', content)

    def test_overlapping_matches_are_reported_as_skipped(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-fix-overlap-") as folder:
            repo = Path(folder) / "repo"
            repo.mkdir()
            rule_dir = repo / ".sins/rules"
            rule_dir.mkdir(parents=True)
            (rule_dir / "nested.toml").write_text(
                '''id = "nested-call"\nversion = 1\nphrase = "use nested call"\n'''
                '''description = "test"\nlanguage = "python"\nseverity = "minor"\nmortal = false\n'''
                '''engine = "ast-grep"\n[paths]\ninclude = ["**/*.py"]\n[match.rule]\n'''
                '''pattern = "foo($$$ARGS)"\n[fix]\nkind = "rewrite"\ntemplate = "foo($$$ARGS)"\n'''
                '''[examples]\nbad = ["foo()"]\ngood = ["bar()"]\nfixed = ["foo()"]\n''', encoding="utf-8")
            source = repo / "nested.py"
            source.write_text("foo(foo())\n", encoding="utf-8")
            code, output = invoke("fix", "--repo", str(repo), "--sin", "nested-call", "--apply")
            self.assertEqual(code, 0, output)
            self.assertIn("skipped=1", output)

    def test_invalid_rewrite_is_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-fix-rollback-") as folder:
            repo = Path(folder) / "repo"
            repo.mkdir()
            rule_dir = repo / ".sins/rules"
            rule_dir.mkdir(parents=True)
            (rule_dir / "invalid-rewrite.toml").write_text(
                '''id = "invalid-rewrite"\nversion = 1\nphrase = "rewrite test call"\n'''
                '''description = "test"\nlanguage = "python"\nseverity = "minor"\nmortal = false\n'''
                '''engine = "ast-grep"\n[paths]\ninclude = ["**/*.py"]\n[match.rule]\n'''
                '''pattern = "bad_call()"\n[fix]\nkind = "rewrite"\ntemplate = "except:"\n'''
                '''[examples]\nbad = ["bad_call()"]\ngood = ["safe_call()"]\nfixed = ["except:"]\n''',
                encoding="utf-8")
            source = repo / "bad.py"
            original = "bad_call()\n"
            source.write_text(original, encoding="utf-8")
            code, output = invoke("fix", "--repo", str(repo), "--sin", "invalid-rewrite", "--apply")
            self.assertEqual(code, 0, output)
            self.assertIn("rolled_back=1", output)
            self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_rewrite_that_does_not_reduce_count_is_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-fix-count-") as folder:
            repo = Path(folder) / "repo"
            repo.mkdir()
            rule_dir = repo / ".sins/rules"
            rule_dir.mkdir(parents=True)
            (rule_dir / "same-match.toml").write_text(
                '''id = "same-match"\nversion = 1\nphrase = "rewrite matching call"\n'''
                '''description = "test"\nlanguage = "python"\nseverity = "minor"\nmortal = false\n'''
                '''engine = "ast-grep"\n[paths]\ninclude = ["**/*.py"]\n[match.rule]\n'''
                '''pattern = "bad($X)"\n[fix]\nkind = "rewrite"\ntemplate = "bad($X + 1)"\n'''
                '''[examples]\nbad = ["bad(1)"]\ngood = ["safe(1)"]\nfixed = ["bad(1 + 1)"]\n''',
                encoding="utf-8")
            source = repo / "same.py"
            original = "bad(1)\n"
            source.write_text(original, encoding="utf-8")
            code, output = invoke("fix", "--repo", str(repo), "--sin", "same-match", "--apply")
            self.assertEqual(code, 0, output)
            self.assertIn("rolled_back=1", output)
            self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_cpp_parse_error_rewrite_is_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-cpp-fix-rollback-") as folder:
            repo = Path(folder) / "repo"
            repo.mkdir()
            rules = repo / ".sins/rules"
            rules.mkdir(parents=True)
            (rules / "invalid-cpp.toml").write_text(
                '''id = "invalid-cpp"\nversion = 1\nphrase = "rewrite test call"\n'''
                '''description = "test"\nlanguage = "cpp"\nseverity = "minor"\nmortal = false\n'''
                '''engine = "ast-grep"\n[paths]\ninclude = ["**/*.cpp"]\n[match.rule]\n'''
                '''pattern = "bad_call()"\n[fix]\nkind = "rewrite"\ntemplate = ")"\n'''
                '''[examples]\nbad = ["bad_call();"]\ngood = ["safe_call();"]\nfixed = [");"]\n''',
                encoding="utf-8")
            source = repo / "bad.cpp"
            original = "void f() { bad_call(); }\n"
            source.write_text(original, encoding="utf-8")
            code, output = invoke("fix", "--repo", str(repo), "--sin", "invalid-cpp", "--apply")
            self.assertEqual(code, 0, output)
            self.assertIn("rolled_back=1", output)
            self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_generic_rewrite_fixes_independent_match_beside_overlap(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-generic-mixed-") as folder:
            repo = Path(folder) / "repo"
            repo.mkdir()
            rules = repo / ".sins/rules"
            rules.mkdir(parents=True)
            (rules / "nested.toml").write_text(
                '''id = "nested-call"\nversion = 1\nphrase = "use renamed call"\n'''
                '''description = "test"\nlanguage = "cpp"\nseverity = "minor"\nmortal = false\n'''
                '''engine = "ast-grep"\n[paths]\ninclude = ["**/*.cpp"]\n[match.rule]\n'''
                '''pattern = "foo($$$ARGS)"\n[fix]\nkind = "rewrite"\ntemplate = "bar($$$ARGS)"\n'''
                '''[examples]\nbad = ["foo();"]\ngood = ["baz();"]\nfixed = ["bar();"]\n''',
                encoding="utf-8")
            source = repo / "calls.cpp"
            original = "void f() { foo(foo()); foo(); }\n"
            source.write_text(original, encoding="utf-8")
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "sins-test@example.invalid")
            git(repo, "config", "user.name", "sins tests")
            git(repo, "add", "calls.cpp")
            git(repo, "commit", "-qm", "fixture")
            code, output = invoke("fix", "--repo", str(repo), "--sin", "nested-call", "--apply")
            self.assertEqual(code, 0, output)
            self.assertIn("skipped=1", output)
            self.assertIn("foo(foo())", source.read_text(encoding="utf-8"))
            self.assertIn("bar()", source.read_text(encoding="utf-8"))

    def test_generic_rewrite_sorts_out_of_order_finding_spans(self) -> None:
        from sins.engines.ast_grep import rewrite_match_spans
        from sins.rules import load_rule

        with tempfile.TemporaryDirectory(prefix="sins-spans-out-of-order-") as folder:
            repo = Path(folder)
            rule_path = repo / "rule.toml"
            rule_path.write_text(
                'id = "rename-call"\nversion = 1\nphrase = "rename call"\n'
                'description = "test"\nlanguage = "cpp"\nseverity = "minor"\nmortal = false\n'
                'engine = "ast-grep"\n[paths]\ninclude = ["**/*.cpp"]\n'
                '[match.rule]\npattern = "foo($$$ARGS)"\n'
                '[fix]\nkind = "rewrite"\ntemplate = "renamed_longer($$$ARGS)"\n'
                '[examples]\nbad = ["foo();"]\ngood = ["bar();"]\nfixed = ["renamed_longer();"]\n',
                encoding="utf-8")
            rule = load_rule(rule_path)
            content = "void f() { foo(1); foo(2); foo(foo(3)); }\n"

            def finding(snippet: str) -> Finding:
                start = content.index(snippet)
                return Finding("rename-call", "sample.cpp", 1, start + 1, 1,
                               start + len(snippet) + 1, "rename call", "minor")

            findings = [finding("foo(2)"), finding("foo(1)"), finding("foo(foo(3))"), finding("foo(3)")]
            rewritten, has_overlap = rewrite_match_spans(rule, content, findings, {2, 3})
            self.assertTrue(has_overlap)
            self.assertEqual(
                rewritten,
                "void f() { renamed_longer(1); renamed_longer(2); foo(foo(3)); }\n")

    def test_generic_rewrite_without_overlaps_keeps_ast_context(self) -> None:
        from sins.engines.ast_grep import rewrite_match_spans, scan_rule
        from sins.rules import load_rule

        with tempfile.TemporaryDirectory(prefix="sins-spans-context-") as folder:
            repo = Path(folder)
            rule_path = repo / "rule.toml"
            rule_path.write_text(
                'id = "context-call"\nversion = 1\nphrase = "rename call"\n'
                'description = "test"\nlanguage = "cpp"\nseverity = "minor"\nmortal = false\n'
                'engine = "ast-grep"\n[paths]\ninclude = ["**/*.cpp"]\n'
                '[match.rule]\npattern = "foo($$$ARGS)"\n'
                '[match.rule.inside]\nkind = "function_definition"\nstopBy = "end"\n'
                '[fix]\nkind = "rewrite"\ntemplate = "renamed_longer($$$ARGS)"\n'
                '[examples]\nbad = ["foo();"]\ngood = ["bar();"]\nfixed = ["renamed_longer();"]\n',
                encoding="utf-8")
            rule = load_rule(rule_path)
            content = "void f() { foo(1); }\n"
            (repo / "sample.cpp").write_text(content, encoding="utf-8")
            rewritten, has_overlap = rewrite_match_spans(rule, content, scan_rule(repo, rule), set())
            self.assertFalse(has_overlap)
            self.assertEqual(rewritten, "void f() { renamed_longer(1); }\n")


if __name__ == "__main__":
    unittest.main()
