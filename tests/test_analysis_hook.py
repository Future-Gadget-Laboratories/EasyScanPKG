"""Tests for the fail-open editor analysis hook."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import sonarqube_analysis_hook as hook  # noqa: E402


class AnalysisHookTests(unittest.TestCase):
    def _run(self, raw: str) -> str:
        out = io.StringIO()
        with patch("sys.stdin", io.StringIO(raw)), patch("sys.stdout", out):
            hook.main()
        return out.getvalue()

    def test_empty_stdin_fails_open(self) -> None:
        self.assertIn("failing open", self._run(""))

    def test_launch_error_fails_open(self) -> None:
        event = json.dumps({"file_path": "/tmp/example.py"})
        with patch.object(hook.subprocess, "run", side_effect=OSError("exec format error")):
            output = self._run(event)
        self.assertIn("fail-open", output)
        self.assertIn("exec format error", output)

    def test_nested_tool_info_path(self) -> None:
        event = {"tool_info": {"filePath": "/tmp/nested.py"}}
        self.assertEqual(hook.extract_file_path(event), "/tmp/nested.py")


if __name__ == "__main__":
    unittest.main()
