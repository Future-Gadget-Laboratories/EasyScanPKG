"""Regex engine boundary and coordinate tests."""

from __future__ import annotations

import tempfile
import unittest
import os
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

    def test_recursive_globs_are_anchored_and_recursive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rule_file = root / "rule.toml"
            rule_file.write_text(RULE_TEXT.replace('include = ["**/*.txt"]',
                'include = ["src/**/*.txt"]\nexclude = ["**/vendor/**"]'), encoding="utf-8")
            rule = load_rule(rule_file)
            paths = ["src/a.txt", "src/pkg/b.txt", "src/vendor/c.txt", "src/vendor/sub/d.txt",
                     "other/src/e.txt", "root.txt", "src/node_modules/f.txt"]
            for rel in paths:
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("bad\n", encoding="utf-8")
            (root / "src/link.txt").symlink_to(root / "src/a.txt")
            self.assertEqual([item.path for item in scan_rule(root, rule)], ["src/a.txt", "src/pkg/b.txt"])

    def test_special_files_and_default_rule_pack_paths_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rule_file = root / "rule.toml"
            rule_file.write_text(RULE_TEXT.replace('include = ["**/*.txt"]', 'include = ["**/*.txt"]'), encoding="utf-8")
            rule = load_rule(rule_file)
            for rel in ("src/good.txt", "lib/sins/rules/example.txt", ".sins/rules/local.txt"):
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("bad\n", encoding="utf-8")
            if hasattr(os, "mkfifo"):
                os.mkfifo(root / "src" / "pipe.txt")
            self.assertEqual([item.path for item in scan_rule(root, rule)], ["src/good.txt"])


if __name__ == "__main__":
    unittest.main()
