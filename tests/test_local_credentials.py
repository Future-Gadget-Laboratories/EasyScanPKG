"""Tests for the local-only credential series."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import env_write  # noqa: E402
import local_credentials as lc  # noqa: E402
import local_server as ls  # noqa: E402
from env_write import read_env_file  # noqa: E402


class LocalCredentialStoreTests(unittest.TestCase):
    def test_rejects_remote_url(self) -> None:
        with self.assertRaises(lc.LocalCredentialError):
            lc.assert_local_url("https://sonar.example.com")
        with self.assertRaises(lc.LocalCredentialError):
            lc.store_local_credential("agent", "http://10.0.0.5:9000", "squ_secret_token")

    def test_store_list_use_hides_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            creds = root / "credentials"
            active = root / "sonar-local.env"
            with mock.patch.object(lc, "CREDENTIALS_DIR", creds), mock.patch.object(
                lc, "INDEX_PATH", creds / "index.json"
            ), mock.patch.object(env_write, "LOCAL_ENV", active):
                lc.store_local_credential(
                    "agent",
                    "http://127.0.0.1:9000",
                    "squ_local_agent_token",
                    kind="USER_TOKEN",
                    purpose="api",
                    activate=True,
                )
                lc.store_local_credential(
                    "scanner",
                    "http://localhost:9000",
                    "squ_local_scanner_token",
                    kind="GLOBAL_ANALYSIS_TOKEN",
                    purpose="scan",
                )
                catalog = lc.public_catalog()
                blob = json.dumps(catalog)
                self.assertNotIn("squ_local_agent_token", blob)
                self.assertNotIn("squ_local_scanner_token", blob)
                names = {row["name"] for row in catalog["entries"]}
                self.assertEqual(names, {"agent", "scanner"})
                self.assertEqual(catalog["active"], "agent")
                mode = stat.S_IMODE((creds / "agent.env").stat().st_mode)
                self.assertEqual(mode, 0o600)
                index = json.loads((creds / "index.json").read_text(encoding="utf-8"))
                self.assertNotIn("squ_local_agent_token", json.dumps(index))
                self.assertNotIn("squ_local_scanner_token", json.dumps(index))
                used = lc.use_local_credential("scanner")
                self.assertTrue(used.active)
                env = read_env_file(active)
                self.assertEqual(env["SONARQUBE_TOKEN"], "squ_local_scanner_token")
                self.assertTrue(env["SONARQUBE_URL"].startswith("http://localhost"))

    def test_bad_name_and_short_token(self) -> None:
        with self.assertRaises(lc.LocalCredentialError):
            lc.assert_credential_name("../admin")
        with tempfile.TemporaryDirectory() as tmp:
            creds = Path(tmp) / "credentials"
            with mock.patch.object(lc, "CREDENTIALS_DIR", creds), mock.patch.object(
                lc, "INDEX_PATH", creds / "index.json"
            ):
                with self.assertRaises(lc.LocalCredentialError):
                    lc.store_local_credential("agent", "http://127.0.0.1:9000", "short")


class LocalSeriesBootstrapTests(unittest.TestCase):
    def test_series_stores_three_names_without_returning_secrets(self) -> None:
        issued = {
            "sft-local-scanner": "squ_scanner_secret_value",
            "sft-local-issues": "squ_issues_secret_value",
        }

        def fake_generate(name: str, token_type: str, _password: str) -> tuple[str, str]:
            return issued[name], token_type

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            creds = root / "credentials"
            local_env = root / "sonar-local.env"
            with mock.patch.object(lc, "CREDENTIALS_DIR", creds), mock.patch.object(
                lc, "INDEX_PATH", creds / "index.json"
            ), mock.patch.object(env_write, "LOCAL_ENV", local_env), mock.patch.object(
                ls, "is_running", return_value=True
            ), mock.patch.object(
                ls, "ensure_token", return_value="squ_agent_secret_value"
            ), mock.patch.object(
                ls, "_ensure_admin_password", return_value="Admin!1Aa"
            ), mock.patch.object(
                ls, "_generate_named_token", side_effect=fake_generate
            ), mock.patch.object(ls, "check_server") as health:
                health.return_value.status = ls.AuthStatus.UNAUTHORIZED
                summary = ls.ensure_local_credential_series()
            blob = json.dumps(summary)
            self.assertNotIn("squ_agent_secret_value", blob)
            self.assertNotIn("squ_scanner_secret_value", blob)
            self.assertNotIn("squ_issues_secret_value", blob)
            names = {row["name"] for row in summary["entries"]}
            self.assertEqual(names, {"agent", "scanner", "issues"})
            self.assertEqual(summary["active"], "agent")
            self.assertTrue(summary["local_only"])
            scanner = read_env_file(creds / "scanner.env")
            self.assertEqual(scanner["SONARQUBE_TOKEN"], issued["sft-local-scanner"])
            self.assertEqual(scanner["LOCAL_ONLY"], "1")
            self.assertEqual(os.stat(creds / "issues.env").st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
