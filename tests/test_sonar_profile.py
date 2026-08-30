#!/usr/bin/env python3
"""Unit tests for quality profile helpers."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from policy_db import PolicyStore  # noqa: E402
from quality_profiles import language_from_xml, profile_name_from_xml  # noqa: E402


SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8'?>
<profile>
  <name>EasyScan Way</name>
  <language>py</language>
  <rules/>
</profile>
"""


class ProfileHelpersTests(unittest.TestCase):
    def test_parse_xml_name_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.xml"
            path.write_text(SAMPLE_XML, encoding="utf-8")
            self.assertEqual(profile_name_from_xml(path), "EasyScan Way")
            self.assertEqual(language_from_xml(path), "py")

    def test_store_profile_metadata_after_import(self) -> None:
        """CLI import persists profile metadata onto the named context."""
        with tempfile.TemporaryDirectory() as tmp:
            store = PolicyStore(Path(tmp))
            store.upsert_context("local", url="http://127.0.0.1:9000")
            store.upsert_context_profile(
                "local",
                language="py",
                profile_name="EasyScan Way",
                profile_key="qp-1",
                is_default=True,
            )
            profiles = store.list_context_profiles("local")
            self.assertEqual(len(profiles), 1)
            self.assertEqual(profiles[0].profile_name, "EasyScan Way")
            self.assertTrue(profiles[0].is_default)


if __name__ == "__main__":
    unittest.main()
