#!/usr/bin/env python3
"""Unit tests for PolicyStore."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from policy_db import PolicyStore  # noqa: E402


class PolicyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PolicyStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_defaults_seeded(self) -> None:
        scan = self.store.get_domain("scan")
        self.assertIn("max_files_per_analyze", scan)
        self.assertTrue(self.store.get_connection_prefs().prefer_connected)

    def test_prefs_survive_new_store_instance(self) -> None:
        self.store.set_pref("scan", "max_files_per_analyze", 12)
        self.store.set_connection_prefs(last_url="https://sonar.example")
        again = PolicyStore(Path(self.tmp.name))
        self.assertEqual(again.get_domain("scan")["max_files_per_analyze"], 12)
        self.assertEqual(again.get_connection_prefs().last_url, "https://sonar.example")

    def test_export_json(self) -> None:
        path = self.store.export_preferences()
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 2)
        self.assertIn("scan", data)

    def test_workspace_bind_and_overlay(self) -> None:
        ws = Path(self.tmp.name) / "proj"
        ws.mkdir()
        sft = ws / ".sft"
        sft.mkdir()
        (sft / "sonar-policy.json").write_text(
            json.dumps(
                {
                    "project_key": "acme.app",
                    "scan": {"max_files_per_analyze": 7},
                    "connection": {"token": "should-be-ignored", "last_url": "https://from-overlay"},
                }
            ),
            encoding="utf-8",
        )
        merged = self.store.merge_project_overlay(ws)
        self.assertEqual(merged["scan"]["max_files_per_analyze"], 7)
        self.assertNotIn("token", merged["connection"])
        self.assertEqual(self.store.get_workspace(ws)["project_key"], "acme.app")

    def test_events_strip_token_keys(self) -> None:
        self.store.record_event("test", {"SONARQUBE_TOKEN": "secret", "port": 64120})
        snap = self.store.snapshot()
        detail = snap["recent_events"][0]["detail"]
        self.assertNotIn("SONARQUBE_TOKEN", detail)
        self.assertEqual(detail["port"], 64120)


if __name__ == "__main__":
    unittest.main()
