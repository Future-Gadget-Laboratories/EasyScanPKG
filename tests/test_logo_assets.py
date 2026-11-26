"""Tests for the EasyScan / Paladin logo and badge SVG assets."""

from __future__ import annotations

import re
import unittest
import xml.etree.ElementTree as ET  # nosec B405 — test only, repo-owned files
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "assets"
SVG_NS = "http://www.w3.org/2000/svg"
LOGOS = ("easyscan.svg", "easyscan-dark.svg", "paladin.svg", "paladin-dark.svg")
BADGES = ("badge-easyscan.svg", "badge-paladin.svg")
NEW_ASSETS = LOGOS + BADGES


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class SvgAssetTests(unittest.TestCase):
    def _root(self, name: str) -> ET.Element:
        return ET.parse(ASSETS / name).getroot()  # nosec B314 — repo-owned fixture

    def test_files_exist(self) -> None:
        for name in NEW_ASSETS:
            self.assertTrue((ASSETS / name).is_file(), name)

    def test_parse_viewbox_and_title(self) -> None:
        for name in NEW_ASSETS:
            with self.subTest(name=name):
                root = self._root(name)
                self.assertEqual(root.tag, f"{{{SVG_NS}}}svg")
                parts = root.get("viewBox", "").split()
                self.assertEqual(len(parts), 4, "viewBox missing or malformed")
                self.assertTrue(all(float(p) >= 0 for p in parts))
                titles = [e for e in root.iter() if _local(e.tag) == "title"]
                self.assertTrue(titles and (titles[0].text or "").strip(), "missing <title>")

    def test_no_script_external_refs_or_raster(self) -> None:
        for name in NEW_ASSETS:
            with self.subTest(name=name):
                text = (ASSETS / name).read_text(encoding="utf-8")
                root = self._root(name)
                tags = {_local(e.tag) for e in root.iter()}
                for banned in ("script", "image", "foreignObject", "use", "style"):
                    self.assertNotIn(banned, tags)
                for el in root.iter():
                    for attr, value in el.attrib.items():
                        if _local(attr) == "href":
                            self.fail(f"href attribute not allowed: {value}")
                self.assertNotRegex(text, r"(?i)https?://(?!www\.w3\.org/2000/svg)")
                self.assertNotRegex(text, r"(?i)data:image|\.(png|jpe?g|gif|webp)\b|@import")
                self.assertNotRegex(text, r"(?i)url\(\s*['\"]?(?!#)")

    def test_logos_are_square(self) -> None:
        for name in LOGOS:
            with self.subTest(name=name):
                parts = self._root(name).get("viewBox", "").split()
                self.assertEqual(parts[2], parts[3])

    def test_badge_text_contrast_meets_wcag_aa(self) -> None:
        for name in BADGES:
            with self.subTest(name=name):
                root = self._root(name)
                fills = [
                    e.get("fill", "")
                    for e in root.iter()
                    if _local(e.tag) == "rect" and re.fullmatch(r"#[0-9a-fA-F]{6}", e.get("fill", ""))
                ]
                self.assertEqual(len(fills), 2, "expected label and message backgrounds")
                text_groups = [e for e in root.iter() if _local(e.tag) == "g" and e.get("fill")]
                self.assertEqual(len(text_groups), 1)
                text_color = text_groups[0].get("fill", "")
                for bg in fills:
                    self.assertGreaterEqual(_contrast(text_color, bg), 4.5, f"{text_color} on {bg}")

    def test_badge_text(self) -> None:
        expected = {
            "badge-easyscan.svg": ("scanned with", "EasyScan"),
            "badge-paladin.svg": ("guarded by", "Paladin"),
        }
        for name, (left, right) in expected.items():
            with self.subTest(name=name):
                words = [e.text for e in self._root(name).iter() if _local(e.tag) == "text"]
                self.assertEqual(words, [left, right])

    def test_readme_references_assets_relatively(self) -> None:
        readme = (ASSETS.parent / "README.md").read_text(encoding="utf-8")
        for name in NEW_ASSETS:
            with self.subTest(name=name):
                self.assertIn(f"assets/{name}", readme)
        self.assertNotRegex(readme, r"\]\(https?://[^)]*assets/")


if __name__ == "__main__":
    unittest.main()
