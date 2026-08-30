"""Tests for ide_port detection helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from ide_port import diagnose_ide_bridge, extension_installed  # noqa: E402


class IdePortTests(unittest.TestCase):
    @patch("ide_port.find_ide_port", return_value=None)
    @patch("ide_port.extension_installed", return_value=False)
    def test_diagnose_missing_extension(self, *_mocks) -> None:
        diag = diagnose_ide_bridge()
        self.assertFalse(diag.ok)
        self.assertFalse(diag.extension_installed)
        self.assertIn("not installed", diag.detail)

    @patch("ide_port.find_ide_port", return_value=None)
    @patch("ide_port.extension_installed", return_value=True)
    def test_diagnose_extension_no_port(self, *_mocks) -> None:
        diag = diagnose_ide_bridge()
        self.assertFalse(diag.ok)
        self.assertTrue(diag.extension_installed)
        self.assertIn("64120", diag.detail)

    def test_extension_installed_detects_cursor_list(self) -> None:
        with (
            patch("ide_port.IDE_EXTENSION_DIRS", []),
            patch("ide_port.shutil.which", return_value="/usr/bin/cursor"),
            patch(
                "ide_port.subprocess.run",
                return_value=type(
                    "R", (), {"returncode": 0, "stdout": "SonarSource.sonarlint-vscode\n"}
                )(),
            ),
        ):
            self.assertTrue(extension_installed())


if __name__ == "__main__":
    unittest.main()
