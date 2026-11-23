"""Tests for review response validation and proposed-fix preflight."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
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
        ready = review()
        ready["verdict"] = "READY"
        self.assertTrue(any("READY" in error for error in validate_review(ready)))
        malformed = review()
        malformed["findings"][0]["severity"] = []
        self.assertTrue(validate_review(malformed))

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

    def test_extract_last_fenced_json_or_whole_json(self) -> None:
        text = 'example ```json\n{"n":1}\n``` then ```json\n{"n":2}\n```'
        self.assertEqual(extract_review(text), {"n": 2})
        self.assertEqual(extract_review('{"n":3}'), {"n": 3})

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
            stale = good.replace("-old", "-stale")
            result = check_fixes(review(fix={"kind": "unified_diff", "diff": stale}), repo)
            self.assertEqual(result[0]["status"], "fails")
            replacement = {"kind": "replacement", "replacement": {"file": "src/a.txt", "start_line": 99,
                           "end_line": 99, "old_code": "old", "new_code": "new"}}
            self.assertEqual(check_fixes(review(fix=replacement), repo)[0]["status"], "ok")
            for content, count in (("different\n", 0), ("old old\n", 2)):
                (repo / "src/a.txt").write_text(content, encoding="utf-8")
                result = check_fixes(review(fix=replacement), repo)
                self.assertEqual(result[0]["status"], "fails", count)

    def test_cli_exit_codes_and_template(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "review.json"
            path.write_text(json.dumps(review()), encoding="utf-8")
            self.assertEqual(invoke("review", "validate", str(path))[0], 0)
            invalid = review()
            del invalid["findings"][0]["proposed_fix"]
            path.write_text(json.dumps(invalid), encoding="utf-8")
            self.assertEqual(invoke("review", "validate", str(path))[0], 1)
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(invoke("review", "validate", str(path))[0], 2)
            path.write_text(json.dumps(review(fix={"kind": "unified_diff", "diff": "--- a/src/a.txt\n+++ b/src/a.txt\n@@ -1 +1 @@\n-old\n+new\n"})), encoding="utf-8")
            repo = Path(folder) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "src").mkdir()
            (repo / "src/a.txt").write_text("old\n", encoding="utf-8")
            self.assertEqual(invoke("review", "check-fixes", str(path), "--repo", str(repo))[0], 0)
            (repo / "src/a.txt").write_text("stale\n", encoding="utf-8")
            self.assertEqual(invoke("review", "check-fixes", str(path), "--repo", str(repo))[0], 1)
            path.write_text("bad", encoding="utf-8")
            self.assertEqual(invoke("review", "check-fixes", str(path), "--repo", str(repo))[0], 2)
        code, output = invoke("review", "template", "--writer-family", "openai", "--reviewer-model", "Haiku", "--reviewer-family", "claude")
        self.assertEqual(code, 0)
        self.assertIn('"schema": "fgl-review@1"', output)
        self.assertIn('"kind": "unified_diff"', output)


if __name__ == "__main__":
    unittest.main()
