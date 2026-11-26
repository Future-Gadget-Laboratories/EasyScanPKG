#!/usr/bin/env python3
"""Tests for easyscan-report: grouping, renderers, git-ref filter, gate, and CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from easyscan_report import (  # noqa: E402
    ReportError,
    build_report,
    changed_files,
    load_checklist,
    load_remediation,
    render_html,
    render_markdown,
    render_terminal,
    safe_link,
    write_reports,
)

FIXTURE = ROOT / "tests" / "fixtures" / "sft" / "issue-checklist.json"
SCRIPT = ROOT / "bin" / "easyscan-report"
CREDENTIAL_MARKER = "secret"


def _report(**kwargs) -> dict:
    return build_report(
        load_checklist(FIXTURE), remediation=load_remediation(), **kwargs
    )


class GroupingTests(unittest.TestCase):
    def test_counts_and_file_order(self) -> None:
        report = _report()
        self.assertEqual(report["listed"], 5)
        self.assertEqual(report["counts"]["MAJOR"], 2)
        self.assertEqual(report["counts"]["CRITICAL"], 1)
        self.assertEqual(report["counts"]["UNKNOWN"], 1)
        # Worst file first: src/app.py holds CRITICAL, so it leads.
        files = [g["file"] for g in report["groups"]]
        self.assertEqual(files, ["src/app.py", "lib/util.py", "docs/notes.md"])

    def test_rules_grouped_inside_file(self) -> None:
        app = _report()["groups"][0]
        self.assertEqual(app["count"], 3)
        self.assertEqual(app["max_severity"], "CRITICAL")
        rules = {r["rule"]: len(r["issues"]) for r in app["rules"]}
        self.assertEqual(rules, {"python:S3776": 1, "python:S1481": 2})

    def test_path_normalized(self) -> None:
        paths = {g["file"] for g in _report()["groups"]}
        self.assertIn("lib/util.py", paths)
        self.assertNotIn("./lib/util.py", paths)

    def test_hints_from_bundled_template_and_fallback(self) -> None:
        hints = _report()["hints"]
        self.assertIn("Unused local variable", hints["python:S1481"]["summary"])
        self.assertTrue(hints["python:S1481"]["hint"])
        self.assertEqual(hints["custom:unmapped-rule"]["summary"], "")
        self.assertIn("No bundled fix hint", hints["custom:unmapped-rule"]["hint"])

    def test_top_files_limit(self) -> None:
        report = _report(top=1)
        self.assertEqual([t[0] for t in report["top_files"]], ["src/app.py"])


class LinkSafetyTests(unittest.TestCase):
    def test_userinfo_stripped_and_scheme_checked(self) -> None:
        self.assertEqual(
            safe_link("http://user:secret@sonar.example.test:9000/x?y=1"),
            "http://sonar.example.test:9000/x?y=1",
        )
        self.assertIsNone(safe_link("javascript:alert(1)"))
        self.assertIsNone(safe_link("file:///etc/passwd"))
        self.assertIsNone(safe_link(None))

    def test_checklist_rejects_other_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "other.json"
            path.write_text(json.dumps({"schema": "something/else", "issues": []}), encoding="utf-8")
            with self.assertRaises(ReportError):
                load_checklist(path)
            with self.assertRaises(ReportError):
                load_checklist(Path(tmp) / "missing.json")


class RendererTests(unittest.TestCase):
    def test_html_is_self_contained_and_escaped(self) -> None:
        doc = render_html(_report())
        self.assertTrue(doc.startswith("<!doctype html>"))
        for forbidden in ("<script", "src=", "@import", "<link", "https://cdn", "//cdn"):
            self.assertNotIn(forbidden, doc, forbidden)
        self.assertIn("&lt;b&gt;tmp&lt;/b&gt;", doc)
        self.assertNotIn("<b>tmp</b>", doc)
        self.assertNotIn(CREDENTIAL_MARKER, doc)
        self.assertNotIn("javascript:", doc)

    def test_markdown_has_checklist_and_hints(self) -> None:
        md = render_markdown(_report())
        self.assertIn("## Findings by file", md)
        self.assertIn("### `src/app.py` — 3 open, worst CRITICAL", md)
        self.assertIn("- [ ] **CRITICAL** `src/app.py:3`", md)
        self.assertIn("- Fix hint:", md)
        self.assertIn("No bundled fix hint", md)
        self.assertNotIn(CREDENTIAL_MARKER, md)

    def test_terminal_summary_gate_line(self) -> None:
        text = render_terminal(_report(threshold="MAJOR"))
        self.assertIn("Gate MAJOR+: 3 open finding(s) — FAIL", text)
        self.assertIn("src/app.py  (worst CRITICAL)", text)
        self.assertNotIn(CREDENTIAL_MARKER, text)

    def test_gate_passes_above_all_findings(self) -> None:
        report = _report(threshold="BLOCKER")
        self.assertEqual(report["failing"], [])
        self.assertIn("— PASS", render_terminal(report))

    def test_write_reports_both_formats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            written = write_reports(_report(), Path(tmp) / "out", "both")
            self.assertEqual({p.suffix for p in written}, {".html", ".md"})
            for path in written:
                self.assertTrue(path.read_text(encoding="utf-8"))
            with self.assertRaises(ReportError):
                write_reports(_report(), Path(tmp), "pdf")


class ChangedFromTests(unittest.TestCase):
    def test_new_in_changed_files_section(self) -> None:
        report = _report(changed_from="HEAD~1", changed={"lib/util.py"})
        self.assertEqual(report["changed_open"], 1)
        self.assertEqual(report["changed_files"], 1)
        md = render_markdown(report)
        self.assertIn("## New in changed files (since `HEAD~1`)", md)
        self.assertIn("1 open finding(s) in 1 changed file(s).", md)
        self.assertIn("changed", render_html(report))

    def test_changed_files_from_git(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        env_args = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "lib").mkdir()
            (repo / "lib" / "util.py").write_text("x = 1\n", encoding="utf-8")
            (repo / "keep.py").write_text("y = 1\n", encoding="utf-8")
            run = lambda *a: subprocess.run(  # noqa: E731
                ["git", "-C", str(repo), *env_args, *a],
                capture_output=True, text=True, check=True,
            )
            run("init", "-q")
            run("add", ".")
            run("commit", "-q", "-m", "base")
            (repo / "lib" / "util.py").write_text("x = 2\n", encoding="utf-8")
            self.assertEqual(changed_files(repo, "HEAD"), {"lib/util.py"})
            with self.assertRaises(ReportError):
                changed_files(repo, "--output=/tmp/x")
            with self.assertRaises(ReportError):
                changed_files(repo, "no-such-ref-xyz")


class CliTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), *args],
            capture_output=True, text=True, check=False, timeout=120,
        )

    def test_writes_reports_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run("--input", str(FIXTURE), "--out-dir", tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("EasyScan report: demo-report — 5 open finding(s)", proc.stdout)
            self.assertTrue((Path(tmp) / "easyscan-report.html").is_file())
            self.assertTrue((Path(tmp) / "easyscan-report.md").is_file())
            self.assertNotIn(CREDENTIAL_MARKER, proc.stdout + proc.stderr)

    def test_fail_on_severity_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            failing = self._run("--input", str(FIXTURE), "--out-dir", tmp, "--fail-on-severity", "medium")
            self.assertEqual(failing.returncode, 3, failing.stdout)
            self.assertIn("— FAIL", failing.stdout)
            passing = self._run("--input", str(FIXTURE), "--out-dir", tmp, "--fail-on-severity", "blocker")
            self.assertEqual(passing.returncode, 0, passing.stdout)
            self.assertIn("— PASS", passing.stdout)

    def test_missing_checklist_is_one_line_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run("--workspace", tmp)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no checklist at", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_bad_threshold_is_usage_error(self) -> None:
        proc = self._run("--input", str(FIXTURE), "--fail-on-severity", "loud")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown severity", proc.stderr)


if __name__ == "__main__":
    unittest.main()
