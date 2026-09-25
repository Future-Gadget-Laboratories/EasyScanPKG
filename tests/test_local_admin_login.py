"""Tests for local SonarQube admin UI login helpers."""

from __future__ import annotations

import json
import stat
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

    def test_generated_password_is_shell_safe(self) -> None:
        for _ in range(20):
            password = ls._generate_admin_password()
            self.assertGreaterEqual(len(password), 12)
            self.assertNotRegex(password, r"[!$`\\]")
            self.assertRegex(password, r"[A-Z]")
            self.assertRegex(password, r"[a-z]")
            self.assertRegex(password, r"\d")
            self.assertIn("#", password)

    def test_local_ui_login_rejects_foreign_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "sonar-local-admin.json"
            state.write_text(json.dumps({"admin_password": "FromOtherHost#1"}) + "\n")
            with mock.patch.object(ls, "ADMIN_STATE", state), mock.patch.object(
                ls, "is_running", return_value=True
            ), mock.patch.object(ls, "_validate_credentials", return_value=False):
                with self.assertRaises(RuntimeError) as caught:
                    ls.local_ui_login()
            self.assertIn("this computer", str(caught.exception))

    def test_missing_file_is_created_from_default_admin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "sonar-local-admin.json"
            workspace = Path(tmp) / "proj" / ".sft" / "sonar-local-admin.json"
            calls = {"n": 0}

            def validate(login: str, password: str) -> bool:
                calls["n"] += 1
                return password == "admin"

            def change(method: str, path: str, **kwargs: object) -> tuple[int, str]:
                self.assertEqual(path, "/api/users/change_password")
                return 204, ""

            with mock.patch.object(ls, "ADMIN_STATE", home), mock.patch.object(
                ls, "workspace_admin_state", return_value=workspace
            ), mock.patch.object(ls, "_validate_credentials", side_effect=validate), mock.patch.object(
                ls, "_api", side_effect=change
            ):
                password = ls._ensure_admin_password()
            self.assertTrue(home.is_file())
            self.assertTrue(workspace.is_file())
            saved = json.loads(home.read_text(encoding="utf-8"))
            self.assertEqual(saved["admin_password"], password)
            self.assertNotEqual(password, "admin")
            self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o600)

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
