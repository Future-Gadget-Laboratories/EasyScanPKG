"""Tests for easyscan-report: grouping, renderers, git-ref filter, gate, and CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from easyscan_report import (
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
GIT_ARGS = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]


def _report(**kwargs) -> dict:
    return build_report(
        load_checklist(FIXTURE), remediation=load_remediation(), **kwargs
    )


def _checklist(issues: list[dict], **extra) -> dict:
    data = {"schema": "easyscan.issue-checklist/v2", "project_key": "demo-report", "issues": issues}
    data.update(extra)
    return data


def _issue(**overrides) -> dict:
    issue = {
        "severity": "MAJOR",
        "rule": "python:S1481",
        "message": "Remove the unused local variable.",
        "file": "src/app.py",
        "line": 4,
        "status": "OPEN",
        "source": "sonar",
        "url": None,
    }
    issue.update(overrides)
    return issue


def _git_repo(tmp: str) -> Path:
    """Repo with lib/util.py committed; returns the repo root."""
    repo = Path(tmp)
    (repo / "lib").mkdir()
    (repo / "lib" / "util.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "keep.py").write_text("y = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), *GIT_ARGS, "commit", "-q", "-m", "base"],
        check=True, capture_output=True,
    )
    return repo


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

    def test_empty_checklist(self) -> None:
        report = build_report(_checklist([]), remediation=load_remediation())
        self.assertEqual(report["listed"], 0)
        self.assertEqual(report["groups"], [])
        self.assertIn("_No open findings._", render_markdown(report))
        self.assertIn("<p><em>No open findings.</em></p>", render_html(report))
        self.assertIn("(none)", render_terminal(report))

    def test_resolved_and_closed_are_hidden_and_counted(self) -> None:
        checklist = _checklist([
            _issue(),
            _issue(status="RESOLVED", line=9),
            _issue(status="closed", line=10),
        ])
        report = build_report(checklist, remediation=load_remediation(), threshold="MAJOR")
        self.assertEqual(report["listed"], 1)
        self.assertEqual(report["resolved_hidden"], 2)
        self.assertEqual(report["counts"]["MAJOR"], 1)
        # The gate uses the same open set, so the verdict matches the listed counts.
        self.assertEqual(len(report["failing"]), 1)
        self.assertIn("resolved or closed findings hidden: 2", render_markdown(report))
        self.assertIn("resolved or closed findings hidden: 2", render_html(report))

    def test_open_count_fallback_and_truncation_note(self) -> None:
        issues = [_issue()]
        for raw in (None, True, "7"):
            report = build_report(_checklist(issues, open_count=raw), remediation={})
            self.assertEqual(report["open_count"], 1)
            self.assertNotIn("fewer findings", render_markdown(report))
        truncated = build_report(_checklist(issues, open_count=9), remediation={})
        self.assertEqual(truncated["open_count"], 9)
        self.assertIn("lists fewer findings than its open_count", render_markdown(truncated))
        self.assertIn("lists fewer findings than its open_count", render_html(truncated))


class LinkSafetyTests(unittest.TestCase):
    def test_userinfo_stripped_and_scheme_checked(self) -> None:
        self.assertEqual(
            safe_link("http://user:secret@sonar.example.test:9000/x?y=1"),
            "http://sonar.example.test:9000/x?y=1",
        )
        self.assertIsNone(safe_link("javascript:alert(1)"))
        self.assertIsNone(safe_link("file:///etc/passwd"))
        self.assertIsNone(safe_link(None))

    def test_whitespace_and_markup_rejected(self) -> None:
        self.assertIsNone(safe_link("https://sonar.example.test/a b"))
        self.assertIsNone(safe_link("https://sonar.example.test/a\nb"))
        self.assertIsNone(safe_link('https://sonar.example.test/"x'))
        self.assertIsNone(safe_link("   "))
        self.assertEqual(safe_link("  https://sonar.example.test/x  "), "https://sonar.example.test/x")

    def test_parentheses_cannot_close_markdown_link(self) -> None:
        link = safe_link("https://sonar.example.test/a)b(c")
        self.assertEqual(link, "https://sonar.example.test/a%29b%28c")
        checklist = _checklist([_issue(url="https://sonar.example.test/a)b")])
        md = render_markdown(build_report(checklist, remediation={}))
        self.assertIn("[[open](https://sonar.example.test/a%29b)]", md)

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

    def test_html_has_no_raw_markdown_syntax(self) -> None:
        doc = render_html(_report(changed_from="HEAD", changed={"lib/util.py"}))
        self.assertIn("<code>demo-report</code>", doc)
        self.assertIn("<strong>5</strong>", doc)
        for markup in ("`", "**", "- project_key"):
            self.assertNotIn(markup, doc, markup)

    def test_html_uses_thead_and_scoped_headers(self) -> None:
        doc = render_html(_report())
        self.assertIn("<thead><tr><th scope=\"col\">Severity</th>", doc)
        self.assertIn("<tbody>", doc)
        self.assertIn("<main>", doc)

    def test_css_sets_background_and_aa_colors(self) -> None:
        doc = render_html(_report())
        self.assertIn("background:#fff", doc)
        self.assertIn(".MAJOR{background:#a84300}", doc)
        self.assertIn(".UNKNOWN{background:#596566}", doc)
        self.assertIn(".src{color:#595959", doc)

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

    def test_write_reports_unwritable_dir_is_report_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "a-file"
            blocker.write_text("not a directory", encoding="utf-8")
            with self.assertRaises(ReportError):
                write_reports(_report(), blocker / "out", "html")


class ChangedFromTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not installed")

    def test_new_in_changed_files_section(self) -> None:
        report = _report(changed_from="HEAD~1", changed={"lib/util.py"})
        self.assertEqual(report["changed_open"], 1)
        self.assertEqual(report["changed_files"], 1)
        md = render_markdown(report)
        self.assertIn("## New in changed files (since `HEAD~1`)", md)
        self.assertIn("1 open finding(s) in 1 changed file(s).", md)
        self.assertIn("changed", render_html(report))

    def test_changed_files_from_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _git_repo(tmp)
            (repo / "lib" / "util.py").write_text("x = 2\n", encoding="utf-8")
            self.assertEqual(changed_files(repo, "HEAD"), {"lib/util.py"})
            with self.assertRaises(ReportError):
                changed_files(repo, "--output=/tmp/x")
            with self.assertRaises(ReportError):
                changed_files(repo, "no-such-ref-xyz")

    def test_untracked_and_non_ascii_paths_included(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _git_repo(tmp)
            (repo / "lib" / "café.py").write_text("z = 1\n", encoding="utf-8")
            (repo / "new_module.py").write_text("w = 1\n", encoding="utf-8")
            self.assertEqual(
                changed_files(repo, "HEAD"),
                {"lib/café.py", "new_module.py"},
            )

    def test_git_failure_is_report_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ReportError):
            changed_files(Path(tmp), "HEAD")

    def test_missing_git_binary_is_report_error(self) -> None:
        side_effect = FileNotFoundError("git")
        with mock.patch("easyscan_report.subprocess.run", side_effect=side_effect), \
                self.assertRaises(ReportError) as ctx:
            changed_files(ROOT, "HEAD")
        self.assertIn("cannot run git", str(ctx.exception))

    def test_git_timeout_is_report_error(self) -> None:
        timeout = subprocess.TimeoutExpired(cmd="git", timeout=60)
        with mock.patch("easyscan_report.subprocess.run", side_effect=timeout), \
                self.assertRaises(ReportError):
            changed_files(ROOT, "HEAD")


class CliTests(unittest.TestCase):
    def _run(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), *args],
            capture_output=True, text=True, check=False, timeout=120,
            env=env,
        )

    def test_writes_reports_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run("--input", str(FIXTURE), "--out-dir", tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("EasyScan report: demo-report — 5 open finding(s)", proc.stdout)
            self.assertTrue((Path(tmp) / "easyscan-report.html").is_file())
            self.assertTrue((Path(tmp) / "easyscan-report.md").is_file())
            self.assertNotIn(CREDENTIAL_MARKER, proc.stdout + proc.stderr)

    def test_single_format_flag(self) -> None:
        for fmt, expected in (("html", {"easyscan-report.html"}), ("markdown", {"easyscan-report.md"})):
            with self.subTest(fmt=fmt), tempfile.TemporaryDirectory() as tmp:
                proc = self._run("--input", str(FIXTURE), "--out-dir", tmp, "--format", fmt)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual({p.name for p in Path(tmp).iterdir()}, expected)

    def test_fail_on_severity_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            failing = self._run("--input", str(FIXTURE), "--out-dir", tmp, "--fail-on-severity", "medium")
            self.assertEqual(failing.returncode, 3, failing.stdout)
            self.assertIn("— FAIL", failing.stdout)
            passing = self._run("--input", str(FIXTURE), "--out-dir", tmp, "--fail-on-severity", "blocker")
            self.assertEqual(passing.returncode, 0, passing.stdout)
            self.assertIn("— PASS", passing.stdout)

    def test_env_fallback_for_fail_on_severity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "EASYSCAN_FAIL_ON_SEVERITY": "medium"}
            proc = self._run("--input", str(FIXTURE), "--out-dir", tmp, env=env)
            self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
            self.assertIn("Gate MAJOR+", proc.stdout)

    def test_changed_from_via_cli(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        with tempfile.TemporaryDirectory() as tmp:
            repo = _git_repo(tmp)
            (repo / "lib" / "util.py").write_text("x = 2\n", encoding="utf-8")
            out = Path(tmp) / "out"
            proc = self._run(
                "--workspace", str(repo), "--input", str(FIXTURE),
                "--out-dir", str(out), "--changed-from", "HEAD",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Changed since HEAD: 1 open in 1 file(s)", proc.stdout)
            self.assertIn("New in changed files", (out / "easyscan-report.md").read_text(encoding="utf-8"))

    def test_missing_checklist_is_one_line_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run("--workspace", tmp)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no checklist at", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_unwritable_out_dir_is_one_line_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "a-file"
            blocker.write_text("not a directory", encoding="utf-8")
            proc = self._run("--input", str(FIXTURE), "--out-dir", str(blocker / "out"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("cannot write report", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_bad_threshold_is_usage_error(self) -> None:
        proc = self._run("--input", str(FIXTURE), "--fail-on-severity", "loud")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown severity", proc.stderr)


if __name__ == "__main__":
    unittest.main()
