"""Tests for language_support probing."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from language_support import probe_language_support  # noqa: E402


class LanguageSupportTests(unittest.TestCase):
    @patch("language_support.urllib.request.urlopen")
    def test_community_without_cfamily(self, mock_urlopen) -> None:
        langs = {"languages": [{"key": "py", "name": "Python"}, {"key": "js", "name": "JavaScript"}]}

        def side_effect(req, timeout=10):
            url = req.full_url if isinstance(req, Request) else str(req)
            if "languages/list" in url:
                cm = MagicMock()
                cm.__enter__.return_value.read.return_value = json.dumps(langs).encode()
                cm.__enter__.return_value.status = 200
                return cm
            raise URLError("404")

        mock_urlopen.side_effect = side_effect
        support = probe_language_support("http://127.0.0.1:9000", "tok")
        self.assertFalse(support.cfamily_available)
        self.assertFalse(support.has_julia)
        self.assertFalse(support.has_assembly)
        self.assertIsNone(support.build_wrapper_url)
        self.assertTrue(any("C/C++" in n for n in support.notes))
        self.assertTrue(any("Julia" in n for n in support.notes))
        self.assertTrue(any("Assembly" in n for n in support.notes))


if __name__ == "__main__":
    unittest.main()
