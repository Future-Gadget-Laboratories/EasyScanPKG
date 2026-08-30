#!/usr/bin/env python3
"""Unit tests for named analysis contexts."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from policy_db import PolicyStore  # noqa: E402


class ContextStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PolicyStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_upsert_use_and_tags(self) -> None:
        self.store.upsert_context(
            "cipherbank",
            url="http://127.0.0.1:9000",
            token_ref="~/.config/sft/sonar-local.env",
            project_key="local-cipherbank",
            tags=["gh:acme/cipherbank"],
            activate=True,
        )
        active = self.store.get_active_context_name()
        self.assertEqual(active, "cipherbank")
        ctx = self.store.get_context("cipherbank")
        assert ctx is not None
        self.assertEqual(ctx.project_key, "local-cipherbank")
        self.assertIn("gh:acme/cipherbank", ctx.tags or [])

        self.store.upsert_context(
            "other",
            url="https://sonar.example",
            token_ref="env:SONARQUBE_TOKEN",
            activate=False,
        )
        self.store.use_context("other")
        self.assertEqual(self.store.get_active_context_name(), "other")
        self.assertFalse(self.store.get_context("cipherbank").active)  # type: ignore[union-attr]

    def test_context_profiles(self) -> None:
        self.store.upsert_context("local", url="http://127.0.0.1:9000")
        self.store.upsert_context_profile(
            "local",
            language="py",
            profile_name="EasyScan Way",
            profile_key="abc",
            is_default=True,
        )
        profiles = self.store.list_context_profiles("local")
        self.assertEqual(len(profiles), 1)
        self.assertTrue(profiles[0].is_default)
        snap = self.store.snapshot()
        self.assertEqual(snap["schema_version"], 2)
        self.assertTrue(any(c["name"] == "local" for c in snap["contexts"]))

    def test_workspace_context_bind(self) -> None:
        ws = Path(self.tmp.name) / "repo"
        ws.mkdir()
        self.store.upsert_context("local", url="http://127.0.0.1:9000")
        row = self.store.upsert_workspace(ws, project_key="local-repo", context_name="local")
        self.assertEqual(row["context_name"], "local")
        self.assertEqual(row["project_key"], "local-repo")


if __name__ == "__main__":
    unittest.main()
