"""Tests for hardened URL / XML helpers."""

from __future__ import annotations

import sys
import tempfile
import unittest
import urllib.request
import xml.etree.ElementTree as ET  # nosec B405 — test only
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import safe_io  # noqa: E402


class UrlTests(unittest.TestCase):
    def test_rejects_non_http_schemes(self) -> None:
        for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://x", "/local/path"):
            with self.assertRaisesRegex(ValueError, "non-HTTP"):
                safe_io.urlopen(url, timeout=1)
        req = urllib.request.Request("file:///etc/hosts")
        with self.assertRaises(ValueError):
            safe_io.urlopen(req, timeout=1)

    @patch("safe_io.urllib.request.urlopen")
    def test_allows_http_and_https(self, mock_open) -> None:
        safe_io.urlopen("http://127.0.0.1:9000/api", timeout=3)
        safe_io.urlopen(urllib.request.Request("HTTPS://sonar.example.com"), timeout=3)
        self.assertEqual(mock_open.call_count, 2)
        self.assertEqual(mock_open.call_args.kwargs["timeout"], 3)


class XmlTests(unittest.TestCase):
    def test_parses_plain_xml(self) -> None:
        root = safe_io.parse_xml('<?xml version="1.0"?><results><error id="x"/></results>')
        self.assertEqual(root.find("error").get("id"), "x")

    def test_rejects_entities_and_doctype(self) -> None:
        bomb = (
            '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;">]><lolz>&lol2;</lolz>'
        )
        xxe = '<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
        for text in (bomb, xxe):
            with self.assertRaises(ET.ParseError):
                safe_io.parse_xml(text)

    def test_parse_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.xml"
            path.write_text("<profile><name>Sonar way</name></profile>", encoding="utf-8")
            self.assertEqual(safe_io.parse_xml_file(path).find("name").text, "Sonar way")


if __name__ == "__main__":
    unittest.main()
