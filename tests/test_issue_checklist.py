#!/usr/bin/env python3
"""Unit tests for issue checklist export helpers (v2 multi-source)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from issue_checklist import SCHEMA_V2, build_payload, render_markdown, write_checklist  # noqa: E402


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
        self.assertEqual(payload["schema"], SCHEMA_V2)
        self.assertEqual(payload["sources_run"], ["sonar"])
        md = render_markdown(payload)
        self.assertIn("open_count: **0**", md)
        self.assertIn("Checklist complete", md)

    def test_nonempty_checkboxes_include_source(self) -> None:
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
            sources_run=["sonar"],
        )
        self.assertFalse(payload["resolved"])
        self.assertEqual(payload["issues"][0]["source"], "sonar")
        md = render_markdown(payload)
        self.assertIn("- [ ] `ISSUE-1`", md)
        self.assertIn("lib/foo.py:12", md)
        self.assertIn("[sonar]", md)

    def test_multi_source_payload(self) -> None:
        issues = [
            {
                "key": "clang-tidy:abc",
                "source": "clang-tidy",
                "severity": "MAJOR",
                "type": "BUG",
                "rule": "clang-tidy:bugprone-use-after-move",
                "message": "use after move",
                "file": "src/a.cpp",
                "line": 3,
            },
            {
                "source": "drmemory",
                "severity": "CRITICAL",
                "type": "BUG",
                "rule": "drmemory:unaddressable-access",
                "message": "UNADDRESSABLE ACCESS",
                "file": "src/a.c",
                "line": 10,
            },
        ]
        payload = build_payload(
            server_url="http://127.0.0.1:9000",
            project_key="demo",
            issues=issues,
            sources_run=["clang-tidy", "drmemory"],
            sources_skipped={"sonar": "disabled"},
            default_source="clang-tidy",
        )
        self.assertEqual(payload["open_count"], 2)
        self.assertEqual(payload["sources_skipped"]["sonar"], "disabled")
        sources = {i["source"] for i in payload["issues"]}
        self.assertEqual(sources, {"clang-tidy", "drmemory"})
        md = render_markdown(payload)
        self.assertIn("[clang-tidy]", md)
        self.assertIn("[drmemory]", md)
        self.assertIn("sources_skipped", md)

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
            self.assertEqual(data["schema"], SCHEMA_V2)
            self.assertTrue(data["resolved"])
            self.assertIn("sources_run", data)


if __name__ == "__main__":
    unittest.main()
