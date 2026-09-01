"""Tests for easyscan_check."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from easyscan_check import CheckReport, check_executables, check_mcp_host_network, run_checks  # noqa: E402


class EasyScanCheckTests(unittest.TestCase):
    def test_executables_detect_missing(self) -> None:
        report = CheckReport(ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            check_executables(report, root)
        self.assertFalse(report.ok)
        self.assertTrue(any(i.id == "executables" and not i.ok for i in report.items))

    def test_mcp_host_network_from_template(self) -> None:
        report = CheckReport(ok=True)
        bridge = Path(__file__).resolve().parents[1]
        check_mcp_host_network(report, bridge)
        item = next(i for i in report.items if i.id == "mcp_host_network")
        self.assertTrue(item.ok)

    @patch("easyscan_check.check_unit_tests")
    @patch("easyscan_check.check_ide_extension")
    @patch("easyscan_check.check_desktop")
    @patch("easyscan_check.check_local_server")
    @patch("easyscan_check.check_images")
    @patch("easyscan_check.check_docker")
    def test_offline_quick_runs(self, *_mocks) -> None:
        bridge = Path(__file__).resolve().parents[1]
        with patch("easyscan_check.check_skills") as skills:
            skills.side_effect = lambda r, offline=False: r.add(
                __import__("easyscan_check", fromlist=["CheckItem"]).CheckItem(
                    "skills", True, "hard", "ok"
                )
            )
            report = run_checks(offline=True, quick=True, skip_tests=True, root=bridge)
        self.assertTrue(any(i.id == "executables" for i in report.items))


if __name__ == "__main__":
    unittest.main()
