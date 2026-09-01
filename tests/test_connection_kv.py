"""Tests for sonar-policy connection kv keys."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from policy_db import PolicyStore  # noqa: E402


class ConnectionKvTests(unittest.TestCase):
    def test_fallback_pref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PolicyStore(Path(tmp))
            store.set_pref("connection", "fallback_to_local_server", True)
            self.assertTrue(store.get_domain("connection")["fallback_to_local_server"])


if __name__ == "__main__":
    unittest.main()
