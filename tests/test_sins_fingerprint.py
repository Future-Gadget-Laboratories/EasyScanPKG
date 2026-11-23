"""Fingerprint stability and baseline multiplicity tests."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from sins.cli import main


def run(*args: str) -> tuple[int, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        return main(list(args)), output.getvalue()


class FingerprintTests(unittest.TestCase):
    def test_line_shift_stable_and_second_duplicate_is_new(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            file = repo / "a.py"
            file.write_text("try:\n    work()\nexcept:\n    pass\n", encoding="utf-8")
            _, output = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--format", "json")
            first = json.loads(output)["findings"][0]["fingerprint"]
            file.write_text("\n\ntry:\n    work()\nexcept:\n    pass\n", encoding="utf-8")
            _, output = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--format", "json")
            self.assertEqual(first, json.loads(output)["findings"][0]["fingerprint"])

    def test_baseline_round_trip_multiset_and_count_new(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            file = repo / "a.py"
            file.write_text("try:\n    work()\nexcept:\n    pass\n", encoding="utf-8")
            baseline = repo / ".sins/baseline.json"
            self.assertEqual(run("baseline", "write", "--repo", str(repo), "--out", str(baseline))[0], 0)
            data = json.loads(baseline.read_text(encoding="utf-8"))
            self.assertEqual(data["totals"]["py-bare-except"], 1)
            file.write_text("try:\n    work()\nexcept:\n    pass\nexcept:\n    pass\n", encoding="utf-8")
            rc, output = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--baseline",
                             str(baseline), "--format", "json")
            report = json.loads(output)
            self.assertEqual(report["totals"]["new"], 1)
            self.assertEqual(report["sins"][0]["count_new"], 1)
            self.assertTrue(any(finding["new"] for finding in report["findings"]))
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
