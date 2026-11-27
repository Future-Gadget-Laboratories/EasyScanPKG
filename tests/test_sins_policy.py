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
            (repo / "src/a.py").write_text("try:\n    pass\nexcept:\n    pass\n", encoding="utf-8")
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
            code.write_text('try:\n    pass\nexcept: # sins: forgive py-bare-except -- legacy\n    pass\n'
                            'try:\n    pass\n# sins: forgive py-bare-except -- documented\nexcept:\n    pass\n'
                            'text = "# sins: forgive py-bare-except -- fake"\ntry:\n    pass\nexcept:\n    pass\n',
                            encoding="utf-8")
            rc, out = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--format", "json")
            self.assertEqual(rc, 0)
            report = json.loads(out)
            self.assertEqual([item["forgiven"] for item in report["findings"] if item["sin"] == "py-bare-except"],
                             [True, True, False])
            code.write_text("try:\n    pass\nexcept: # sins: forgive py-bare-except\n    pass\n", encoding="utf-8")
            rc, out = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--format", "json")
            self.assertEqual(rc, 0)
            report = json.loads(out)
            self.assertEqual(report["sins"][-1]["id"], "sins-forgive-without-reason")
            cpp = repo / "a.cpp"
            cpp.write_text("usleep(1); //forgive me\n", encoding="utf-8")
            rc, out = run("scan", "--repo", str(repo), "--rule", "cpp-usleep", "--format", "json")
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(out)["findings"][0]["forgiven"])

    def test_forgiveness_marker_inside_python_string_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "a.py").write_text(
                'text = "# sins: forgive py-bare-except -- fake"\ntry:\n    pass\nexcept:\n    pass\n',
                encoding="utf-8")
            rc, output = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--fail-on", "any",
                             "--format", "json")
            self.assertEqual(rc, 1)
            self.assertFalse(json.loads(output)["findings"][0]["forgiven"])

    def test_forgiveness_without_reason_emits_meta_sin(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "a.py").write_text("try:\n    pass\nexcept: # sins: forgive py-bare-except\n    pass\n",
                                        encoding="utf-8")
            rc, output = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--format", "json")
            report = json.loads(output)
            self.assertEqual(rc, 0)
            self.assertTrue(any(item["sin"] == "sins-forgive-without-reason" for item in report["findings"]))

    def test_cpp_forgive_me_comment_forgives_same_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "a.cpp").write_text("usleep(1'000); //forgive me\n", encoding="utf-8")
            rc, output = run("scan", "--repo", str(repo), "--rule", "cpp-usleep", "--format", "json")
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(output)["findings"][0]["forgiven"])
            (repo / "a.py").write_text("try:\n    pass\nexcept:  # //forgive me\n    pass\n", encoding="utf-8")
            rc, output = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--format", "json")
            self.assertEqual(rc, 0)
            self.assertFalse(json.loads(output)["findings"][0]["forgiven"])

    def test_trailing_comment_does_not_forgive_following_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "a.py").write_text(
                "x = 1 # sins: forgive py-bare-except -- inline\ntry:\n    pass\nexcept:\n    pass\n",
                encoding="utf-8")
            rc, output = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--fail-on", "any",
                             "--format", "json")
            self.assertEqual(rc, 1)
            self.assertFalse(json.loads(output)["findings"][0]["forgiven"])

    def test_comma_spaced_forgiveness_ids_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "a.py").write_text(
                "try:\n    pass\n"
                "# sins: forgive py-bare-except, py-assert-validation -- two rules\n"
                "except:\n    pass\n",
                encoding="utf-8")
            rc, output = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--format", "json")
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(output)["findings"][0]["forgiven"])

    def test_policy_for_unselected_rule_does_not_break_filtered_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".sins").mkdir()
            (repo / ".sins/policy.toml").write_text(
                '[sins."cpp-usleep"]\nmortal = true\n', encoding="utf-8")
            (repo / "a.py").write_text("try:\n    pass\nexcept:\n    pass\n", encoding="utf-8")
            rc, output = run("scan", "--repo", str(repo), "--rule", "py-bare-except", "--format", "json")
            self.assertEqual(rc, 0)
            self.assertEqual(len(json.loads(output)["findings"]), 1)
            self.assertEqual(json.loads(output)["sins"][0]["id"], "py-bare-except")

    def test_excluded_paths_do_not_emit_forgiveness_meta_sins(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "vendor").mkdir()
            (repo / "vendor/a.py").write_text("# sins: forgive py-bare-except\n", encoding="utf-8")
            (repo / ".sins").mkdir()
            (repo / ".sins/policy.toml").write_text('[paths]\nexclude = ["vendor/**"]\n', encoding="utf-8")
            rc, output = run("scan", "--repo", str(repo), "--format", "json")
            self.assertEqual(rc, 0)
            self.assertFalse(any(item["sin"] == "sins-forgive-without-reason"
                                 for item in json.loads(output)["findings"]))


if __name__ == "__main__":
    unittest.main()
