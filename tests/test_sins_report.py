"""Report schema and count reconciliation tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sins.regex_engine import Finding
from sins.report import make_report, text_report
from sins.rules import load_rule


class ReportTests(unittest.TestCase):
    def test_path_counts_and_totals_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rule.toml"
            path.write_text('''id="sample-rule"\nversion=1\nphrase="avoid sample"\ndescription="sample"\nlanguage="text"\nseverity="minor"\nmortal=false\nmortal_threshold=1\nengine="regex"\n[match]\npattern="bad"\n[examples]\nbad=["bad"]\ngood=["good"]\n''', encoding="utf-8")
            rule = load_rule(path)
            findings = [Finding(rule.id, "a.txt", 1, 1, 1, 4, rule.phrase, rule.severity),
                        Finding(rule.id, "b.txt", 3, 2, 3, 5, rule.phrase, rule.severity)]
            report = make_report(Path(tmp), [rule], findings)
            entry = report["sins"][0]
            self.assertEqual(sum(entry["by_path"].values()), entry["count_unforgiven"])
            self.assertEqual(report["totals"]["findings"], 2)
            self.assertEqual(report["totals"]["unforgiven"], 2)
            self.assertEqual(report["totals"]["mortal_over"], 1)
            self.assertIn("SIN | COUNT | THRESH", text_report(report))


if __name__ == "__main__":
    unittest.main()
