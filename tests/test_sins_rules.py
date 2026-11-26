"""Rule schema and loader tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sins.rules import RuleError, load_rule, load_rules


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
                load_rule(path)
            path.write_text(RULE.format(id="rewrite-sin", extra='[fix]\nkind = "rewrite"\ntemplate = "replace"\n'), encoding="utf-8")
            with self.assertRaisesRegex(RuleError, "examples.fixed"):
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

    def test_repo_local_additions_and_shadowing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            local = repo / ".sins" / "rules"
            local.mkdir(parents=True)
            (local / "extra.toml").write_text(RULE.format(id="local-sin", extra=""), encoding="utf-8")
            self.assertIn("local-sin", {rule.id for rule in load_rules(repo, [], [])})
            (local / "shadow.toml").write_text(RULE.format(id="py-bare-except", extra=""), encoding="utf-8")
            with self.assertRaisesRegex(RuleError, "built-in id"):
                load_rules(repo, [], [])

    def test_non_regex_match_keys_are_engine_specific_and_rules_dir_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            pack = repo / "pack"
            pack.mkdir()
            ast_rule = pack / "ast.toml"
            ast_rule.write_text(
                RULE.format(id="ast-sin", extra="").replace('engine = "regex"', 'engine = "ast-grep"')
                .replace('[match]\npattern = "bad"', '[match]\nrule = "except_clause"'),
                encoding="utf-8",
            )
            (pack / "selected.toml").write_text(RULE.format(id="selected-sin", extra=""), encoding="utf-8")
            selected = load_rules(repo, [pack], ["selected-sin"])
            self.assertEqual([rule.id for rule in selected], ["selected-sin"])
            with self.assertRaisesRegex(RuleError, "not implemented in S1"):
                load_rules(repo, [pack], ["ast-sin"])
            with self.assertRaisesRegex(RuleError, "rules-dir"):
                load_rules(repo, [repo / "missing"], [])

    def test_unknown_engine_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.toml"
            path.write_text(RULE.format(id="bad-engine", extra="").replace('engine = "regex"', 'engine = "bogus"'),
                            encoding="utf-8")
            with self.assertRaisesRegex(RuleError, "unknown engine"):
                load_rule(path)

    def test_builtin_rules_directory_is_not_loaded_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            rules = load_rules(repo, [Path(__file__).resolve().parents[1] / "lib/sins/rules"], [])
            self.assertGreaterEqual(len(rules), 3)


if __name__ == "__main__":
    unittest.main()
