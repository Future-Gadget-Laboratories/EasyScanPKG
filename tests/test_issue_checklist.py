#!/usr/bin/env python3
"""Unit tests for issue checklist export helpers."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from issue_checklist import build_payload, render_markdown, write_checklist  # noqa: E402


class IssueChecklistTests(unittest.TestCase):
    def test_empty_is_resolved(self) -> None:
        payload = build_payload(
            server_url="http://127.0.0.1:9000",
            project_key="demo",
            issues=[],
            context="local",
            total=0,
        )
        self.assertTrue(payload["resolved"])
        self.assertEqual(payload["open_count"], 0)
        md = render_markdown(payload)
        self.assertIn("open_count: **0**", md)
        self.assertIn("Checklist complete", md)

    def test_nonempty_checkboxes(self) -> None:
        issues = [
            {
                "key": "ISSUE-1",
                "severity": "CRITICAL",
                "type": "CODE_SMELL",
                "rule": "python:S3776",
                "message": "Too complex",
                "component": "demo:lib/foo.py",
                "line": 12,
            }
        ]
        payload = build_payload(
            server_url="http://127.0.0.1:9000",
            project_key="demo",
            issues=issues,
            total=1,
        )
        self.assertFalse(payload["resolved"])
        md = render_markdown(payload)
        self.assertIn("- [ ] `ISSUE-1`", md)
        self.assertIn("lib/foo.py:12", md)

    def test_write_checklist_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            payload = build_payload(
                server_url="http://127.0.0.1:9000",
                project_key="demo",
                issues=[],
                total=0,
            )
            md_path, json_path = write_checklist(ws, payload)
            self.assertTrue(md_path.is_file())
            self.assertTrue(json_path and json_path.is_file())
            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], "easyscan.issue-checklist/v1")
            self.assertTrue(data["resolved"])


if __name__ == "__main__":
    unittest.main()
