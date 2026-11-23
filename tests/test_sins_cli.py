"""CLI contract tests for sins."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sins.cli import main
from sins.exitcodes import ERROR, FINDINGS, LOOP_STOP, OK, select_exit_code


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
