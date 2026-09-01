"""Unit tests for sonar-cxx plugin helpers (no Docker / network)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sonar_cxx_plugin import (
    DEFAULT_CXX_JAR,
    DEFAULT_CXX_TAG,
    is_plugin_jar_name,
    resolve_cxx_tag,
    resolve_plugin_url,
)


class SonarCxxPluginHelpersTests(unittest.TestCase):
    def test_is_plugin_jar_name(self) -> None:
        self.assertTrue(is_plugin_jar_name("sonar-cxx-plugin-2.3.0.1496.jar"))
        self.assertFalse(is_plugin_jar_name("cxx-sslr-toolkit-2.3.0.1496.jar"))
        self.assertFalse(is_plugin_jar_name("sonar-cxx-plugin-2.3.0.1496.zip"))
        self.assertFalse(is_plugin_jar_name("evil.jar"))

    def test_resolve_cxx_tag_defaults_and_compat(self) -> None:
        self.assertEqual(resolve_cxx_tag(None), DEFAULT_CXX_TAG)
        self.assertEqual(resolve_cxx_tag("26.8.0.126808"), "cxx-2.3.0")
        self.assertEqual(resolve_cxx_tag("25.8.0.111898"), "cxx-2.3.0")
        self.assertEqual(resolve_cxx_tag("9.9.0"), "cxx-2.0.7")

    def test_resolve_cxx_tag_env_override(self) -> None:
        with patch.dict("os.environ", {"SFT_SONAR_CXX_VERSION": "2.3.0"}, clear=False):
            self.assertEqual(resolve_cxx_tag("99.0"), "cxx-2.3.0")
        with patch.dict("os.environ", {"SFT_SONAR_CXX_VERSION": "cxx-2.2.1"}, clear=False):
            self.assertEqual(resolve_cxx_tag("99.0"), "cxx-2.2.1")

    def test_resolve_plugin_url_default(self) -> None:
        # Clear overrides
        with patch.dict("os.environ", {"SFT_SONAR_CXX_PLUGIN_URL": "", "SFT_SONAR_CXX_VERSION": ""}, clear=False):
            url, jar, tag = resolve_plugin_url("26.8.0.126808")
        self.assertEqual(tag, "cxx-2.3.0")
        self.assertEqual(jar, DEFAULT_CXX_JAR)
        self.assertIn(DEFAULT_CXX_JAR, url)
        self.assertTrue(is_plugin_jar_name(jar))

    def test_resolve_plugin_url_env_rejects_toolkit(self) -> None:
        bad = "https://example.com/cxx-sslr-toolkit-2.3.0.1496.jar"
        with patch.dict("os.environ", {"SFT_SONAR_CXX_PLUGIN_URL": bad}, clear=False):
            with self.assertRaises(ValueError):
                resolve_plugin_url("26.8.0")


if __name__ == "__main__":
    unittest.main()
