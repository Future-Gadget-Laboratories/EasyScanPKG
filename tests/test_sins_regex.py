"""Regex engine boundary and coordinate tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sins.regex_engine import scan_rule
from sins.rules import load_rule


RULE_TEXT = '''id = "sample-rule"
version = 1
phrase = "match the sample"
description = "A sample."
language = "text"
severity = "minor"
mortal = false
engine = "regex"
[paths]
include = ["**/*.txt"]
[match]
pattern = "bad"
[examples]
bad = ["bad"]
good = ["good"]
'''


class RegexTests(unittest.TestCase):
    def test_coordinates_are_one_based_and_binary_and_large_files_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rule_file = root / "rule.toml"
            rule_file.write_text(RULE_TEXT, encoding="utf-8")
            rule = load_rule(rule_file)
            (root / "sample.txt").write_text("ok\nbad here\n", encoding="utf-8")
            (root / "binary.txt").write_bytes(b"bad\0" + b"x" * 100)
            (root / "large.txt").write_bytes(b"bad" + b"x" * (2 * 1024 * 1024))
            found = scan_rule(root, rule)
            self.assertEqual(len(found), 1)
            self.assertEqual((found[0].line, found[0].col, found[0].end_line, found[0].end_col), (2, 1, 2, 4))


if __name__ == "__main__":
    unittest.main()
