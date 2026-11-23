"""Integration tests for the pinned ast-grep engine."""

from __future__ import annotations

import os
import json
import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sins.engines import ast_grep
from sins.rules import load_rule


class AstGrepIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.binary = ast_grep.resolve_binary()
        except ast_grep.EngineError as exc:
            raise unittest.SkipTest(f"pinned ast-grep is not installed: {exc}") from exc

    def _scan(self, language: str, suffix: str, source: str, match: str) -> list:
        with tempfile.TemporaryDirectory(prefix="sins-ast-test-") as folder:
            repo = Path(folder)
            (repo / f"fixture{suffix}").write_text(source, encoding="utf-8")
            rule_path = repo / "rule.toml"
            rule_path.write_text(
                f'id = "fixture-rule"\nversion = 1\nphrase = "match fixture"\n'
                f'description = "fixture"\nlanguage = "{language}"\nseverity = "minor"\n'
                f'mortal = false\nengine = "ast-grep"\n[paths]\ninclude = ["**/*"]\n'
                f'[match.rule]\n{match}\n[examples]\nbad = ["x"]\ngood = ["y"]\n',
                encoding="utf-8")
            rule = load_rule(rule_path)
            return ast_grep.scan_rule(repo, rule)

    def test_python_bash_yaml_and_cpp_fixtures(self) -> None:
        fixtures = (
            ("python", ".py", "try:\n    pass\nexcept:\n    pass\n", 'kind = "except_clause"'),
            ("bash", ".sh", "echo hello\n", 'pattern = "echo hello"'),
            ("yaml", ".yaml", "enabled: true\n", 'pattern = "enabled: true"'),
            ("cpp", ".cpp", "void f() { usleep(10); }\n", 'pattern = "usleep($$$ARGS)"'),
        )
        for language, suffix, source, match in fixtures:
            with self.subTest(language=language):
                self.assertEqual(len(self._scan(language, suffix, source, match)), 1)

    def test_usleep_matches_calls_but_not_comments_or_strings(self) -> None:
        rule = load_rule(Path(__file__).resolve().parents[1] / "lib/sins/rules/cpp/cpp-usleep.toml")
        with tempfile.TemporaryDirectory(prefix="sins-usleep-context-") as folder:
            repo = Path(folder)
            (repo / "fixture.cpp").write_text(
                '// usleep(1);\nconst char* text = "usleep(2)";\nvoid f() { usleep(3); }\n',
                encoding="utf-8")
            findings = ast_grep.scan_rule(repo, rule)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].line, 3)

    def test_usleep_matches_global_namespace_call(self) -> None:
        rule = load_rule(Path(__file__).resolve().parents[1] / "lib/sins/rules/cpp/cpp-usleep.toml")
        with tempfile.TemporaryDirectory(prefix="sins-usleep-global-") as folder:
            repo = Path(folder)
            (repo / "fixture.cpp").write_text("void f() { ::usleep(3); }\n", encoding="utf-8")
            self.assertEqual(len(ast_grep.scan_rule(repo, rule)), 1)

    def test_ast_grep_fingerprint_survives_line_shift(self) -> None:
        rule = load_rule(Path(__file__).resolve().parents[1] / "lib/sins/rules/cpp/cpp-usleep.toml")
        with tempfile.TemporaryDirectory(prefix="sins-anchor-shift-") as folder:
            repo = Path(folder)
            path = repo / "fixture.cpp"
            path.write_text("void f() {\n  usleep(3);\n}\n", encoding="utf-8")
            first = ast_grep.scan_rule(repo, rule)[0]
            path.write_text("\n\nvoid f() {\n  usleep(3);\n}\n", encoding="utf-8")
            shifted = ast_grep.scan_rule(repo, rule)[0]
            self.assertEqual(first.fingerprint, shifted.fingerprint)

    def test_open_without_encoding_only_checks_outer_call_and_supported_args(self) -> None:
        self.assertFalse(ast_grep._open_needs_encoding("open('a', encoding=open('b').read())"))
        self.assertFalse(ast_grep._open_needs_encoding("open('a', 'rb', opener=helper(open('b')))"))
        self.assertTrue(ast_grep._open_needs_encoding("open('a', 'r', 1)"))
        self.assertFalse(ast_grep._open_needs_encoding("open(*args)"))

    def test_open_with_trailing_comma_comment_rewrites_validly(self) -> None:
        source = "open(\n    'file',  # keep this comma\n)\n"
        rewritten = ast_grep.rewrite_text_non_overlapping(
            load_rule(Path(__file__).resolve().parents[1] / "lib/sins/rules/python/py-open-without-encoding.toml"),
            source)[0]
        import ast
        ast.parse(rewritten)
        self.assertIn("'file',  # keep this comma\n    encoding=", rewritten)

    def test_relative_repo_path_scans(self) -> None:
        rule = load_rule(Path(__file__).resolve().parents[1] / "lib/sins/rules/python/py-bare-except.toml")
        with tempfile.TemporaryDirectory(prefix="sins-relative-repo-") as folder:
            previous = Path.cwd()
            try:
                os.chdir(folder)
                repo = Path("nested/repo")
                repo.mkdir(parents=True)
                (repo / "fixture.py").write_text("try:\n pass\nexcept:\n pass\n", encoding="utf-8")
                self.assertEqual(len(ast_grep.scan_rule(repo, rule)), 1)
            finally:
                os.chdir(previous)

    def test_parse_validation_rejects_cpp_error_nodes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-cpp-parse-") as folder:
            path = Path(folder) / "bad.cpp"
            path.write_text("void f( {\n", encoding="utf-8")
            rule = load_rule(Path(__file__).resolve().parents[1] / "lib/sins/rules/cpp/cpp-usleep.toml")
            with self.assertRaisesRegex(ast_grep.EngineError, "ERROR|MISSING"):
                ast_grep.validate_parse(rule, path)

    def test_open_without_encoding_skips_binary_and_encoded_calls(self) -> None:
        rule = load_rule(Path(__file__).resolve().parents[1] / "lib/sins/rules/python/py-open-without-encoding.toml")
        with tempfile.TemporaryDirectory(prefix="sins-open-context-") as folder:
            repo = Path(folder)
            (repo / "fixture.py").write_text(
                "open('a.bin', 'rb')\nopen('b.bin', mode='wb')\n"
                "open('c.txt', encoding='utf-8')\nopen('d.txt')\n",
                encoding="utf-8")
            findings = ast_grep.scan_rule(repo, rule)
            self.assertEqual([item.line for item in findings], [4])

    def test_bare_except_does_not_match_exception_class(self) -> None:
        rule = load_rule(Path(__file__).resolve().parents[1] / "lib/sins/rules/python/py-bare-except.toml")
        with tempfile.TemporaryDirectory(prefix="sins-except-context-") as folder:
            repo = Path(folder)
            (repo / "fixture.py").write_text(
                "try:\n    work()\nexcept Exception:\n    pass\n"
                "try:\n    work()\nexcept:\n    pass\n", encoding="utf-8")
            findings = ast_grep.scan_rule(repo, rule)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].line, 7)

    def test_never_uses_shadow_utils_sg(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-fake-path-") as folder:
            marker = Path(folder) / "sg-called"
            fake = Path(folder) / "sg"
            fake.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 97\n", encoding="utf-8")
            fake.chmod(0o755)
            with patch.dict(os.environ, {"PATH": folder}):
                self.assertEqual(Path(ast_grep.resolve_binary()).name, "ast-grep")
                self.assertEqual(len(self._scan("python", ".py", "try:\n pass\nexcept:\n pass\n",
                                                'kind = "except_clause"')), 1)
            self.assertFalse(marker.exists())

    def test_hash_mismatch_and_wrong_version_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-bad-engine-") as folder:
            binary = Path(folder) / "ast-grep"
            shutil.copyfile(self.binary, binary)
            binary.write_bytes(binary.read_bytes() + b"tamper")
            binary.chmod(0o755)
            with patch.dict(os.environ, {"SINS_AST_GREP": str(binary)}):
                with self.assertRaisesRegex(ast_grep.EngineError, "sha256 mismatch"):
                    ast_grep.resolve_binary()
            binary.write_text("#!/bin/sh\necho ast-grep 9.9.9\n", encoding="utf-8")
            binary.chmod(0o755)
            fake_pins = {"version": "0.51.3", "platforms": {ast_grep._platform_key(): {
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}}}
            with patch.dict(os.environ, {"SINS_AST_GREP": str(binary)}), patch.object(
                    ast_grep, "_pins", return_value=fake_pins):
                with self.assertRaisesRegex(ast_grep.EngineError, "version"):
                    ast_grep.resolve_binary()

    def test_wrong_version_is_cli_engine_error(self) -> None:
        from sins.cli import main
        import contextlib
        import io

        with tempfile.TemporaryDirectory(prefix="sins-version-cli-") as folder:
            fake = Path(folder) / "ast-grep"
            fake.write_text("#!/bin/sh\necho ast-grep 9.9.9\n", encoding="utf-8")
            fake.chmod(0o755)
            fake_pins = {"version": "0.51.3", "platforms": {ast_grep._platform_key(): {
                "binary_sha256": hashlib.sha256(fake.read_bytes()).hexdigest()}}}
            output = io.StringIO()
            with patch.dict(os.environ, {"SINS_AST_GREP": str(fake)}), \
                    patch.object(ast_grep, "_pins", return_value=fake_pins), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main(["scan", "--repo", folder, "--rule", "py-bare-except",
                             "--format", "json"])
            self.assertEqual(code, 2)
            self.assertIn("ast-grep", output.getvalue())
            report, _ = json.JSONDecoder().raw_decode(output.getvalue())
            self.assertEqual(report["engine_errors"][0]["sin"], "py-bare-except")
            self.assertEqual(report["engine_versions"]["ast-grep"], "unavailable")


if __name__ == "__main__":
    unittest.main()
