"""Tests for review response validation and proposed-fix preflight."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sins.cli import main
from sins.review import check_fixes, extract_review, validate_review


def review(severity: str = "major", fix: dict | None = None) -> dict:
    finding = {"id": "R1", "severity": severity, "sin": None, "file": "src/a.txt", "line": 1,
               "problem": "Bad code", "why": "It breaks", "new_sin_proposal": None}
    if fix is not None:
        finding["proposed_fix"] = fix
    elif severity != "nit":
        finding["proposed_fix"] = {"kind": "instructions", "steps": ["change it"]}
    return {"schema": "fgl-review@1", "pr": "o/r#1", "head": "abc", "reviewer": {"model": "Haiku", "family": "claude"},
            "writer_family": "openai", "verdict": "CHANGES", "findings": [finding], "summary": "Needs work"}


def invoke(*args: str) -> tuple[int, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = main(list(args))
    return code, output.getvalue()


class ReviewValidationTests(unittest.TestCase):
    def test_valid_missing_fix_nit_and_cross_checks(self) -> None:
        self.assertEqual(validate_review(review()), [])
        missing = review("major")
        del missing["findings"][0]["proposed_fix"]
        self.assertTrue(any("proposed_fix" in error for error in validate_review(missing)))
        self.assertEqual(validate_review(review("nit")), [])
        same_family = review()
        same_family["reviewer"]["family"] = "OPENAI"
        self.assertIn("reviewer.family: same model family", validate_review(same_family))
        extra_field = review()
        extra_field["findings"][0]["unexpected"] = True
        self.assertTrue(any("unexpected field" in error for error in validate_review(extra_field)))
        ready = review()
        ready["verdict"] = "READY"
        self.assertTrue(any("READY" in error for error in validate_review(ready)))
        malformed = review()
        malformed["findings"][0]["severity"] = []
        self.assertTrue(validate_review(malformed))
        for sev in ("blocker", "minor"):
            missing_sev = review(sev)
            del missing_sev["findings"][0]["proposed_fix"]
            self.assertTrue(any("proposed_fix" in error for error in validate_review(missing_sev)))
        dup = review()
        dup["findings"].append(dict(dup["findings"][0]))
        self.assertTrue(any("duplicate id" in error for error in validate_review(dup)))
        for bad_conf in (-0.1, 1.1, True):
            bad = review(fix={"kind": "instructions", "steps": ["do it"], "confidence": bad_conf})
            self.assertTrue(any("confidence" in error for error in validate_review(bad)))
        bad_deg = review(fix={"kind": "instructions", "steps": ["do it"], "degraded_fix": "yes"})
        self.assertTrue(any("degraded_fix" in error for error in validate_review(bad_deg)))

    def test_bad_diffs_replacement_and_instructions_fail(self) -> None:
        diffs = (
            "--- a/src/a.txt\n+++ b/src/a.txt\n-no\n+yes\n",
            "--- /tmp/a.txt\n+++ b/src/a.txt\n@@ -1 +1 @@\n-no\n+yes\n",
            "--- a/../a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-no\n+yes\n",
        )
        for diff in diffs:
            errors = validate_review(review(fix={"kind": "unified_diff", "diff": diff}))
            self.assertTrue(any("proposed_fix.diff" in error for error in errors), errors)
        replacement = review(fix={"kind": "replacement", "replacement": {"file": "src/a.txt", "start_line": 4,
                           "end_line": 3, "old_code": "x", "new_code": "y"}})
        self.assertTrue(any("start_line" in error for error in validate_review(replacement)))
        instructions = review(fix={"kind": "instructions", "steps": []})
        self.assertTrue(any("steps" in error for error in validate_review(instructions)))
        for path in ("C:src/a.txt", ".", " src/a.txt", "src/a.txt ", "src/a\n.txt", "src/a\x00.txt"):
            malformed_path = review()
            malformed_path["findings"][0]["file"] = path
            self.assertTrue(any("file" in error for error in validate_review(malformed_path)), path)

    def test_diff_headers_ignore_hunk_lines_and_allow_dev_null(self) -> None:
        valid_diffs = (
            "--- /dev/null\n+++ b/src/new.txt\n@@ -0,0 +1 @@\n+new\n",
            "--- a/src/old.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n",
            "--- a/src/a.txt\n+++ b/src/a.txt\n@@ -1 +1 @@\n--- ../path\n+replacement\n",
        )
        for diff in valid_diffs:
            self.assertEqual(validate_review(review(fix={"kind": "unified_diff", "diff": diff})), [])
        missing_headers = "@@ -1 +1 @@\n--- ../path\n+replacement\n"
        self.assertTrue(validate_review(review(fix={"kind": "unified_diff", "diff": missing_headers})))

    def test_extract_last_fenced_json_or_whole_json(self) -> None:
        text = 'example ```json\n{"n":1}\n``` then ```json\n{"n":2}\n```'
        self.assertEqual(extract_review(text), {"n": 2})
        self.assertEqual(extract_review('{"n":3}'), {"n": 3})
        payload = {"diff": "--- a/doc.md\n+++ b/doc.md\n@@ -1 +1 @@\n-```\n+```"}
        fenced = "```json\n" + json.dumps(payload) + "\n```"
        self.assertEqual(extract_review(fenced), payload)

    def test_schema_is_json_and_has_required_fields(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "lib/sins/schemas/fgl-review-1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema"]["const"], "fgl-review@1")
        self.assertEqual(schema["required"], ["schema", "pr", "head", "reviewer", "writer_family", "verdict", "findings", "summary"])
        self.assertEqual(schema["$defs"]["finding"]["required"], ["id", "severity", "sin", "file", "line", "problem", "why"])

    def test_check_fixes_in_temp_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "src").mkdir()
            (repo / "src/a.txt").write_text("old\n", encoding="utf-8")
            good = "--- a/src/a.txt\n+++ b/src/a.txt\n@@ -1 +1 @@\n-old\n+new\n"
            result = check_fixes(review(fix={"kind": "unified_diff", "diff": good}), repo)
            self.assertEqual(result[0]["status"], "ok")
            self.assertEqual((repo / "src/a.txt").read_text(encoding="utf-8"), "old\n")
            stale = good.replace("-old", "-stale")
            result = check_fixes(review(fix={"kind": "unified_diff", "diff": stale}), repo)
            self.assertEqual(result[0]["status"], "fails")
            replacement = {"kind": "replacement", "replacement": {"file": "src/a.txt", "start_line": 99,
                           "end_line": 99, "old_code": "old", "new_code": "new"}}
            self.assertEqual(check_fixes(review(fix=replacement), repo)[0]["status"], "ok")
            empty_anchor = review(fix={"kind": "replacement", "replacement": {"file": "src/a.txt", "start_line": 1,
                                 "end_line": 1, "old_code": "", "new_code": "new"}})
            self.assertTrue(any("old_code" in error for error in validate_review(empty_anchor)))
            for content, count in (("different\n", 0), ("old old\n", 2)):
                (repo / "src/a.txt").write_text(content, encoding="utf-8")
                result = check_fixes(review(fix=replacement), repo)
                self.assertEqual(result[0]["status"], "fails", count)
            self.assertEqual(check_fixes(review(), repo)[0]["status"], "skipped")
            self.assertEqual(check_fixes(review("nit"), repo)[0]["status"], "skipped")

    def test_cli_exit_codes_and_template(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "review.json"
            path.write_text(json.dumps(review()), encoding="utf-8")
            self.assertEqual(invoke("review", "validate", str(path))[0], 0)
            path.write_text("```json\n" + json.dumps(review()) + "\n```", encoding="utf-8")
            self.assertEqual(invoke("review", "validate", str(path))[0], 0)
            path.write_text(json.dumps(review()), encoding="utf-8")
            code, out = invoke("review", "validate", str(path), "--json")
            self.assertEqual((code, json.loads(out)["valid"]), (0, True))
            invalid = review()
            del invalid["findings"][0]["proposed_fix"]
            path.write_text(json.dumps(invalid), encoding="utf-8")
            self.assertEqual(invoke("review", "validate", str(path))[0], 1)
            code, out = invoke("review", "validate", str(path), "--json")
            self.assertEqual((code, json.loads(out)["valid"]), (1, False))
            self.assertEqual(invoke("review", "check-fixes", str(path), "--repo", folder)[0], 2)
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(invoke("review", "validate", str(path))[0], 2)
            path.write_text(json.dumps(review(fix={"kind": "unified_diff", "diff": "--- a/src/a.txt\n+++ b/src/a.txt\n@@ -1 +1 @@\n-old\n+new\n"})), encoding="utf-8")
            repo = Path(folder) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "src").mkdir()
            (repo / "src/a.txt").write_text("old\n", encoding="utf-8")
            self.assertEqual(invoke("review", "check-fixes", str(path), "--repo", str(repo))[0], 0)
            code, out = invoke("review", "check-fixes", str(path), "--repo", str(repo), "--json")
            self.assertEqual((code, json.loads(out)["all_ok"]), (0, True))
            self.assertEqual(invoke("review", "check-fixes", str(path), "--repo", str(Path(folder) / "missing"))[0], 2)
            (repo / "src/a.txt").write_text("stale\n", encoding="utf-8")
            self.assertEqual(invoke("review", "check-fixes", str(path), "--repo", str(repo))[0], 1)
            code, out = invoke("review", "check-fixes", str(path), "--repo", str(repo), "--json")
            self.assertEqual((code, json.loads(out)["all_ok"]), (1, False))
            path.write_text("bad", encoding="utf-8")
            self.assertEqual(invoke("review", "check-fixes", str(path), "--repo", str(repo))[0], 2)
            diff = "--- a/src/a.txt\n+++ b/src/a.txt\n@@ -1 +1 @@\n-old\n+new\n"
            tool_failure = json.dumps(review(fix={"kind": "unified_diff", "diff": diff}))
            path.write_text(tool_failure, encoding="utf-8")
            non_repo = Path(folder) / "not-a-repo"
            non_repo.mkdir()
            self.assertEqual(invoke("review", "check-fixes", str(path), "--repo", str(non_repo))[0], 2)
            with patch("sins.review.subprocess.run", side_effect=FileNotFoundError("git missing")):
                self.assertEqual(invoke("review", "check-fixes", str(path), "--repo", str(repo))[0], 2)
        code, output = invoke("review", "template", "--writer-family", "openai", "--reviewer-model", "Haiku", "--reviewer-family", "claude")
        self.assertEqual(code, 0)
        self.assertIn('"schema": "fgl-review@1"', output)
        self.assertIn('"kind": "unified_diff"', output)


if __name__ == "__main__":
    unittest.main()
