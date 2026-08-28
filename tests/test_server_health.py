"""Tests for server_health."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from server_health import AuthStatus, check_server  # noqa: E402


class ServerHealthTests(unittest.TestCase):
    def test_missing_token(self) -> None:
        r = check_server("https://example.com", None)
        self.assertEqual(r.status, AuthStatus.MISSING_CREDENTIALS)

    def test_missing_url(self) -> None:
        r = check_server("", "tok")
        self.assertEqual(r.status, AuthStatus.MISSING_CREDENTIALS)

    @patch("server_health.urllib.request.urlopen")
    def test_ok(self, mock_urlopen) -> None:
        mock_resp = mock_urlopen.return_value.__enter__.return_value
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"valid":true}'
        r = check_server("https://sonar.example", "secret")
        self.assertEqual(r.status, AuthStatus.OK)

    @patch("server_health.urllib.request.urlopen")
    def test_invalid_body(self, mock_urlopen) -> None:
        mock_resp = mock_urlopen.return_value.__enter__.return_value
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"valid":false}'
        r = check_server("https://sonar.example", "secret")
        self.assertEqual(r.status, AuthStatus.UNAUTHORIZED)


if __name__ == "__main__":
    unittest.main()
