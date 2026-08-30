"""Tests for credentials_set (local/remote token writers)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import credentials_set as cs  # noqa: E402
from env_write import read_env_file, write_env_file  # noqa: E402
from server_health import AuthStatus, HealthResult  # noqa: E402


class CredentialsSetTests(unittest.TestCase):
    def test_set_local_credentials_writes_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "sonar-local.env"

            def fake_update(url: str, token: str, project_key: str | None = None) -> Path:
                values = {"SONARQUBE_URL": url, "SONARQUBE_TOKEN": token}
                if project_key:
                    values["SONARQUBE_PROJECT_KEY"] = project_key
                write_env_file(local, values, header="# test local")
                return local

            with mock.patch("credentials_set.update_local_credentials", side_effect=fake_update), mock.patch(
                "credentials_set.merge_mcp_config"
            ), mock.patch("credentials_set.merge_codex_config"):
                result = cs.set_local_credentials(
                    url="http://127.0.0.1:9000",
                    token="squ_test_token_value",
                    project_key="local-demo",
                )
            self.assertTrue(result.ok)
            self.assertEqual(result.backend, "local")
            self.assertFalse(result.bootstrapped)
            data = read_env_file(local)
            self.assertEqual(data["SONARQUBE_TOKEN"], "squ_test_token_value")
            self.assertEqual(data["SONARQUBE_PROJECT_KEY"], "local-demo")

    def test_bootstrap_calls_ensure_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "sonar-local.env"
            with mock.patch("credentials_set.ensure_token", return_value="squ_boot") as ensure, mock.patch(
                "credentials_set.update_local_credentials", return_value=local
            ) as upd, mock.patch("credentials_set.merge_mcp_config"), mock.patch(
                "credentials_set.merge_codex_config"
            ):
                result = cs.set_local_credentials(bootstrap=True, project_key="local-x")
            ensure.assert_called_once_with(project_key="local-x")
            upd.assert_called_once()
            args, kwargs = upd.call_args
            self.assertEqual(args[1], "squ_boot")
            self.assertTrue(result.bootstrapped)
            self.assertTrue(result.ok)

    def test_validate_marks_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "sonar-local.env"
            bad = HealthResult(AuthStatus.UNAUTHORIZED, "http://127.0.0.1:9000", "nope", 401)
            with mock.patch("credentials_set.update_local_credentials", return_value=local), mock.patch(
                "credentials_set.merge_mcp_config"
            ), mock.patch("credentials_set.merge_codex_config"), mock.patch(
                "credentials_set.check_server", return_value=bad
            ):
                result = cs.set_local_credentials(
                    url="http://127.0.0.1:9000",
                    token="bad",
                    validate=True,
                )
            self.assertFalse(result.ok)
            self.assertEqual(result.auth_status, "unauthorized")

    def test_paths_and_mask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "sonar-local.env"
            remote = Path(tmp) / "sonar.env"
            local.write_text("SONARQUBE_TOKEN=longenoughsecret\n", encoding="utf-8")
            with mock.patch.object(cs, "LOCAL_ENV", local), mock.patch.object(cs, "REMOTE_ENV", remote):
                paths = cs.credential_paths()
                self.assertEqual(paths["local_env"], str(local))
                self.assertTrue(cs.mask_token_present(local))
                self.assertFalse(cs.mask_token_present(remote))


if __name__ == "__main__":
    unittest.main()
