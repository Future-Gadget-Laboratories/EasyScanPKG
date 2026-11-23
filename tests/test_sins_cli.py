"""CLI contract tests for sins."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sins.cli import _example_path, _run_examples, main
from sins.exitcodes import ERROR, FINDINGS, LOOP_STOP, OK, select_exit_code
from sins.rules import RuleError, load_rule


RULE = '''id = "sample-sin"
version = 1
phrase = "leave a bad marker"
description = "sample"
language = "text"
severity = "major"
mortal = {mortal}
mortal_threshold = {threshold}
engine = "regex"
[paths]
include = ["**/*.txt"]
[match]
pattern = "bad"
[examples]
bad = ["bad"]
good = ["good"]
'''


def invoke(*args: str) -> tuple[int, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        return main(list(args)), output.getvalue()


class CliTests(unittest.TestCase):
    def test_scan_exit_codes_threshold_any_and_engine_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            rules = Path(tmp) / "rules"
            rules.mkdir()
            rule = rules / "rule.toml"
            (repo / "sample.txt").write_text("bad\n", encoding="utf-8")
            rule.write_text(RULE.format(mortal="false", threshold=1), encoding="utf-8")
            self.assertEqual(invoke("scan", "--repo", str(repo), "--rules-dir", str(rules))[0], OK)
            self.assertEqual(invoke("scan", "--repo", str(repo), "--rules-dir", str(rules), "--fail-on", "any")[0], FINDINGS)
            (repo / "sample.txt").write_text("bad\nbad\n", encoding="utf-8")
            self.assertEqual(invoke("scan", "--repo", str(repo), "--rules-dir", str(rules))[0], FINDINGS)
            self.assertEqual(invoke("scan", "--repo", str(repo), "--rules-dir", str(rules), "--fail-on", "any")[0], FINDINGS)
            rule.write_text(RULE.format(mortal="true", threshold=20), encoding="utf-8")
            self.assertEqual(invoke("scan", "--repo", str(repo), "--rules-dir", str(rules))[0], FINDINGS)
            self.assertEqual(invoke("scan", "--repo", str(repo), "--rules-dir", str(rules), "--fail-on", "new")[0], ERROR)
            rule.write_text(RULE.format(mortal="true", threshold=0).replace('engine = "regex"', 'engine = "opengrep"'), encoding="utf-8")
            self.assertEqual(invoke("scan", "--repo", str(repo), "--rules-dir", str(rules))[0], ERROR)
            self.assertEqual(invoke("scan", "--repo", str(repo / "missing"))[0], ERROR)

    def test_rule_examples_follow_include_path_and_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "rules"
            pack.mkdir()
            for rule_id, language, include in (
                ("header-sin", "cpp", '["**/*.hpp"]'),
                ("nested-sin", "python", '["src/**/*.py"]'),
            ):
                rule = RULE.format(mortal="false", threshold=0).replace("sample-sin", rule_id)
                rule = rule.replace('language = "text"', f'language = "{language}"')
                rule = rule.replace('include = ["**/*.txt"]', f"include = {include}")
                (pack / f"{rule_id}.toml").write_text(rule, encoding="utf-8")
                self.assertEqual(invoke("test", "--rules-dir", str(pack), "--rule", rule_id)[0], OK)

            directory_rule = RULE.format(mortal="false", threshold=0).replace("sample-sin", "directory-sin")
            directory_rule = directory_rule.replace('include = ["**/*.txt"]', 'include = ["src/"]')
            (pack / "directory.toml").write_text(directory_rule, encoding="utf-8")
            self.assertEqual(invoke("test", "--rules-dir", str(pack), "--rule", "directory-sin")[0], OK)

    def test_example_paths_reject_absolute_and_parent_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "rules"
            pack.mkdir()
            for rule_id, include in (("parent-sin", "../*.txt"), ("absolute-sin", "/tmp/*.txt")):
                rule = RULE.format(mortal="false", threshold=0).replace("sample-sin", rule_id)
                rule = rule.replace('include = ["**/*.txt"]', f'include = ["{include}"]')
                path = pack / f"{rule_id}.toml"
                path.write_text(rule, encoding="utf-8")
                self.assertEqual(invoke("test", "--rules-dir", str(pack), "--rule", rule_id)[0], ERROR)
                with self.assertRaisesRegex(RuleError, "unsafe example pattern"):
                    _example_path(load_rule(path))

            safe_rule = load_rule(pack / "parent-sin.toml")
            temp_dir = tempfile.TemporaryDirectory
            with patch("sins.cli.tempfile.TemporaryDirectory",
                       side_effect=lambda prefix: temp_dir(dir=tmp, prefix=prefix)):
                with patch("sins.cli._example_path", return_value=Path("../escaped.txt")):
                    with self.assertRaisesRegex(RuleError, "escapes temporary repo"):
                        _run_examples(safe_rule)
            self.assertFalse((Path(tmp) / "escaped.txt").exists())

    def test_count_list_version_and_json_outputs(self) -> None:
        rc, version_out = invoke("--version", "--json")
        self.assertEqual(rc, OK)
        self.assertIn("rules_version", json.loads(version_out))
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "a.txt").write_text("bad\n", encoding="utf-8")
            rules = Path(tmp) / "rules"
            rules.mkdir()
            (rules / "r.toml").write_text(RULE.format(mortal="false", threshold=0), encoding="utf-8")
            (rules / "ast.toml").write_text(RULE.format(mortal="false", threshold=0).replace("sample-sin", "ast-sin")
                .replace('engine = "regex"', 'engine = "ast-grep"').replace('[match]\npattern = "bad"',
                '[match]\nrule = "except_clause"'), encoding="utf-8")
            rc_count, count_out = invoke("count", "--repo", str(repo), "--rules-dir", str(rules),
                "--rule", "sample-sin", "--format", "json")
            self.assertEqual(rc_count, FINDINGS)
            self.assertEqual(json.loads(count_out)["schema"], "fgl-sins/report@1")
            rc_dash, dash_out = invoke("scan", "--repo", str(repo), "--rules-dir", str(rules),
                "--rule", "sample-sin", "--json", "-", "--format", "json")
            self.assertEqual(rc_dash, FINDINGS)
            self.assertEqual(json.loads(dash_out)["totals"]["findings"], 1)
            self.assertEqual(dash_out.count('"schema"'), 1)
            rc_list, list_out = invoke("list", "--repo", str(repo), "--rules-dir", str(rules))
            self.assertEqual(rc_list, OK)
            self.assertIn("ast-sin", list_out)
            self.assertEqual(invoke("scan", "--repo", str(repo), "--rules-dir", str(repo / "missing"))[0], ERROR)

    def test_omitted_threshold_is_unlimited_and_explicit_overrun_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "sample.txt").write_text("bad\nbad\nbad\n", encoding="utf-8")
            rules = Path(tmp) / "rules"
            rules.mkdir()
            rule = rules / "rule.toml"
            without_threshold = RULE.format(mortal="false", threshold=0).replace("mortal_threshold = 0\n", "")
            rule.write_text(without_threshold, encoding="utf-8")
            self.assertEqual(invoke("scan", "--repo", str(repo), "--rules-dir", str(rules))[0], OK)
            rule.write_text(RULE.format(mortal="false", threshold=1), encoding="utf-8")
            self.assertEqual(invoke("scan", "--repo", str(repo), "--rules-dir", str(rules))[0], FINDINGS)

    def test_fail_on_new_does_not_create_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            output = Path(tmp) / "report.json"
            self.assertEqual(invoke("scan", "--repo", str(repo), "--fail-on", "new", "--json", str(output))[0], ERROR)
            self.assertFalse(output.exists())

    def test_json_file_also_prints_table_and_new_refuses_dangling_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            output = Path(tmp) / "report.json"
            rc, stdout = invoke("scan", "--repo", str(repo), "--json", str(output))
            self.assertEqual(rc, OK)
            self.assertIn("SIN | COUNT", stdout)
            self.assertTrue(output.is_file())
            rule_dir = Path(tmp) / "pack" / "text"
            rule_dir.mkdir(parents=True)
            (rule_dir / "dangling.toml").symlink_to(rule_dir / "missing.toml")
            self.assertEqual(invoke("new", "dangling", "--language", "text", "--out", str(Path(tmp) / "pack"))[0], ERROR)

    def test_precedence_and_examples_and_scaffold(self) -> None:
        self.assertEqual(select_exit_code(error=True, loop_stop=True, findings=True), ERROR)
        self.assertEqual(select_exit_code(loop_stop=True, findings=True), LOOP_STOP)
        self.assertEqual(select_exit_code(findings=True), FINDINGS)
        self.assertEqual(select_exit_code(), OK)
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            os.chdir(tmp)
            try:
                self.assertEqual(invoke("test")[0], OK)
                output, text = invoke("new", "fresh-rule", "--language", "text", "--out", str(Path(tmp) / "pack"))
                self.assertEqual(output, OK)
                created = Path(text.strip())
                self.assertEqual(invoke("test", "--rules-dir", str(created.parent.parent))[0], ERROR)
                self.assertEqual(invoke("new", "fresh-rule", "--language", "text", "--out", str(Path(tmp) / "pack"))[0], ERROR)
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
