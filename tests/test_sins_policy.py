"""Policy overlays and forgiveness comment behavior."""

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


class PolicyTests(unittest.TestCase):
    def test_overlay_validation_and_global_excludes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "src").mkdir()
            (repo / "src/a.py").write_text("except:\n", encoding="utf-8")
            (repo / ".sins").mkdir()
            policy = repo / ".sins/policy.toml"
            policy.write_text('[sins."py-bare-except"]\nmortal = true\nmortal_threshold = 8\n', encoding="utf-8")
            rc, out = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--format", "json")
            self.assertEqual(rc, 1)
            self.assertTrue(json.loads(out)["sins"][0]["mortal"])
            policy.write_text('[sins."unknown-sin"]\nenabled = false\n', encoding="utf-8")
            self.assertEqual(run("scan", "--repo", str(repo))[0], 2)
            policy.write_text('[paths]\nexclude = ["src/**"]\n', encoding="utf-8")
            rc, out = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--format", "json")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["totals"]["findings"], 0)

    def test_forgiveness_locations_strings_cpp_and_missing_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            code = repo / "a.py"
            code.write_text('except: # sins: forgive py-bare-except -- legacy\n'
                            '# sins: forgive py-bare-except -- documented\nexcept:\n'
                            'text = "# sins: forgive py-bare-except -- fake"\nexcept:\n', encoding="utf-8")
            rc, out = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--format", "json")
            self.assertEqual(rc, 0)
            report = json.loads(out)
            self.assertEqual([item["forgiven"] for item in report["findings"] if item["sin"] == "py-bare-except"],
                             [True, True, False])
            code.write_text("except: # sins: forgive py-bare-except\n", encoding="utf-8")
            rc, out = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--format", "json")
            self.assertEqual(rc, 0)
            report = json.loads(out)
            self.assertEqual(report["sins"][-1]["id"], "sins-forgive-without-reason")
            cpp = repo / "a.cpp"
            cpp.write_text("usleep(1); //forgive me\n", encoding="utf-8")
            rc, out = run("scan", "--repo", str(repo), "--rule", "cpp-usleep", "--format", "json")
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(out)["findings"][0]["forgiven"])


if __name__ == "__main__":
    unittest.main()
