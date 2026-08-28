"""Tests for env_write."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from env_write import read_env_file, write_env_file  # noqa: E402


class EnvWriteTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "test.env"
            write_env_file(p, {"SONARQUBE_URL": "https://x", "SONARQUBE_TOKEN": "t"})
            data = read_env_file(p)
            self.assertEqual(data["SONARQUBE_URL"], "https://x")
            self.assertEqual(data["SONARQUBE_TOKEN"], "t")


if __name__ == "__main__":
    unittest.main()
