"""Tests for desktop launcher install helpers."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from desktop_install import DESKTOP_ID, add_to_favorites, install_desktop_entry  # noqa: E402


class DesktopInstallTests(unittest.TestCase):
    def test_install_desktop_entry_writes_file(self) -> None:
        bridge = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            apps = Path(tmp) / "applications"
            icons = Path(tmp) / "icons" / "hicolor" / "scalable" / "apps"
            with (
                patch("desktop_install._applications_dir", return_value=apps),
                patch("desktop_install._icons_dir", return_value=icons),
                patch("desktop_install.shutil.which", return_value=None),
            ):
                dest = install_desktop_entry(
                    bridge=bridge,
                    workspace="/tmp/ws",
                    also_desktop_shortcut=False,
                )
            self.assertTrue(dest.is_file())
            text = dest.read_text(encoding="utf-8")
            self.assertIn("EasyScan", text)
            self.assertIn("sonar-desktop", text)
            self.assertIn("/tmp/ws", text)
            self.assertTrue((icons / "sft-sonar.svg").is_file())

    @patch("desktop_install._set_favorites", return_value=True)
    @patch("desktop_install._get_favorites", return_value=["firefox.desktop"])
    def test_add_to_favorites_appends(self, _get, set_fav) -> None:
        self.assertTrue(add_to_favorites())
        set_fav.assert_called_once_with(["firefox.desktop", DESKTOP_ID])

    @patch("desktop_install._set_favorites")
    @patch("desktop_install._get_favorites", return_value=[DESKTOP_ID])
    def test_add_to_favorites_idempotent(self, _get, set_fav) -> None:
        self.assertTrue(add_to_favorites())
        set_fav.assert_not_called()


if __name__ == "__main__":
    unittest.main()
