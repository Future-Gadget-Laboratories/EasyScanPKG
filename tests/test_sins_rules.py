"""Rule schema and loader tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sins.rules import RuleError, load_rules


RULE = '''id = "{id}"
version = 1
phrase = "avoid the sample pattern"
description = "A sample rule."
language = "text"
severity = "minor"
mortal = false
engine = "regex"

[match]
pattern = "bad"

[examples]
bad = ["bad"]
good = ["good"]
{extra}
'''


class RuleLoadingTests(unittest.TestCase):
    def test_duplicate_id_across_packs_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            pack = repo / "pack"
            pack.mkdir()
            (pack / "custom.toml").write_text(RULE.format(id="py-bare-except", extra=""), encoding="utf-8")
            with self.assertRaisesRegex(RuleError, "built-in id"):
                load_rules(repo, [pack], [])

    def test_duplicate_id_within_custom_packs_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            packs = [repo / "one", repo / "two"]
            for pack in packs:
                pack.mkdir()
                (pack / "rule.toml").write_text(RULE.format(id="same-sin", extra=""), encoding="utf-8")
            with self.assertRaisesRegex(RuleError, "duplicate id"):
                load_rules(repo, packs, [])

    def test_missing_examples_and_rewrite_fixed_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.toml"
            path.write_text(RULE.format(id="missing-example", extra="").replace('bad = ["bad"]\n', ""), encoding="utf-8")
            with self.assertRaisesRegex(RuleError, "examples.bad"):
                from sins.rules import load_rule
                load_rule(path)
            path.write_text(RULE.format(id="rewrite-sin", extra='[fix]\nkind = "rewrite"\ntemplate = "replace"\n'), encoding="utf-8")
            with self.assertRaisesRegex(RuleError, "examples.fixed"):
                from sins.rules import load_rule
                load_rule(path)

    def test_selected_unimplemented_engine_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            pack = repo / "pack"
            pack.mkdir()
            (pack / "custom.toml").write_text(
                RULE.format(id="custom-sin", extra="").replace('engine = "regex"', 'engine = "ast-grep"'),
                encoding="utf-8")
            with self.assertRaisesRegex(RuleError, "not implemented in S1"):
                load_rules(repo, [pack], ["custom-sin"])


if __name__ == "__main__":
    unittest.main()
