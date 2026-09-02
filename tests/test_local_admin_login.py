"""Tests for local SonarQube admin UI login helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import local_server as ls  # noqa: E402


class AdminLoginTests(unittest.TestCase):
    def test_admin_login_reads_stored_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "sonar-local-admin.json"
            state.write_text(
                json.dumps({"admin_password": "Secret!1Aa"}) + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(ls, "ADMIN_STATE", state), mock.patch.object(
                ls, "DEFAULT_LOCAL_URL", "http://127.0.0.1:9000"
            ):
                info = ls.admin_login()
            self.assertEqual(info["url"], "http://127.0.0.1:9000")
            self.assertEqual(info["username"], "admin")
            self.assertEqual(info["password"], "Secret!1Aa")
            self.assertEqual(info["state_file"], str(state))

    def test_admin_login_missing_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "missing.json"
            with mock.patch.object(ls, "ADMIN_STATE", state):
                info = ls.admin_login()
            self.assertEqual(info["username"], "admin")
            self.assertEqual(info["password"], "")
            self.assertEqual(info["state_file"], str(state))


if __name__ == "__main__":
    unittest.main()
