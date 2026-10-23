"""bootstrap_cxx_quality_profile must not take over a custom default cxx profile."""

from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import sonar_cxx_plugin  # noqa: E402

URL = "http://127.0.0.1:9000"


class FakeSonar:
    """Minimal quality-profile API: search, copy, rules, activate, set_default."""

    def __init__(self, profiles: list[dict]) -> None:
        self.profiles = [dict(p, language="cxx") for p in profiles]
        self.set_default_calls: list[str] = []
        self.copies = 0

    def __call__(self, url, token, method, path, *, query=None, form=None, **_):
        if path == "/api/qualityprofiles/search":
            return 200, {"profiles": [dict(p) for p in self.profiles]}
        if path == "/api/qualityprofiles/copy":
            self.copies += 1
            prof = {"key": f"k-{form['toName']}", "name": form["toName"], "language": "cxx",
                    "isDefault": False, "isBuiltIn": False}
            self.profiles.append(prof)
            return 200, dict(prof)
        if path == "/api/rules/search":
            return 200, {"rules": [{"key": "cxx:FileHeader"}], "total": 1}
        if path == "/api/qualityprofiles/activate_rule":
            return 204, ""
        if path == "/api/qualityprofiles/set_default":
            self.set_default_calls.append(form["qualityProfile"])
            for p in self.profiles:
                p["isDefault"] = p["name"] == form["qualityProfile"]
            return 204, ""
        raise AssertionError(f"unexpected call {method} {path}")

    def default(self) -> str | None:
        return next((p["name"] for p in self.profiles if p.get("isDefault")), None)


SONAR_WAY = {"key": "sw", "name": "Sonar way", "isDefault": True, "isBuiltIn": True}


def _run(fake: FakeSonar, env: dict[str, str] | None = None) -> tuple[str | None, str]:
    err = io.StringIO()
    clean = {k: v for k, v in os.environ.items() if k != "SFT_SONAR_CXX_SET_DEFAULT"}
    clean.update(env or {})
    with patch.object(sonar_cxx_plugin, "api", side_effect=fake), \
            patch.dict(os.environ, clean, clear=True), \
            patch("sonar_runtime.read_settings", return_value={}), \
            redirect_stderr(err):
        name = sonar_cxx_plugin.bootstrap_cxx_quality_profile(URL, "tok")
    return name, err.getvalue()


class CxxDefaultProfileTests(unittest.TestCase):
    def test_builtin_default_is_replaced(self) -> None:
        fake = FakeSonar([SONAR_WAY])
        name, err = _run(fake)
        self.assertEqual(name, "EasyScan CXX")
        self.assertEqual(fake.set_default_calls, ["EasyScan CXX"])
        self.assertEqual(fake.default(), "EasyScan CXX")
        self.assertEqual(err, "")

    def test_no_default_is_set(self) -> None:
        fake = FakeSonar([dict(SONAR_WAY, isDefault=False)])
        _run(fake)
        self.assertEqual(fake.set_default_calls, ["EasyScan CXX"])

    def test_custom_default_is_kept_with_one_log_line(self) -> None:
        custom = {"key": "cb", "name": "House C++", "isDefault": True, "isBuiltIn": False}
        fake = FakeSonar([dict(SONAR_WAY, isDefault=False), custom])
        name, err = _run(fake)
        self.assertEqual(name, "EasyScan CXX")  # still created and kept up to date
        self.assertEqual(fake.set_default_calls, [])
        self.assertEqual(fake.default(), "House C++")
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("keeping custom default cxx profile 'House C++'", err)

    def test_opt_out_never_changes_default(self) -> None:
        for value in ("0", "false", "no", "off"):
            fake = FakeSonar([SONAR_WAY])
            _, err = _run(fake, {"SFT_SONAR_CXX_SET_DEFAULT": value})
            self.assertEqual(fake.set_default_calls, [], value)
            self.assertEqual(fake.default(), "Sonar way")
            self.assertIn("SFT_SONAR_CXX_SET_DEFAULT=0", err)

    def test_opt_out_from_runtime_settings_file(self) -> None:
        fake = FakeSonar([SONAR_WAY])
        with patch("sonar_runtime.read_settings", return_value={"SFT_SONAR_CXX_SET_DEFAULT": "0"}), \
                patch.object(sonar_cxx_plugin, "api", side_effect=fake), \
                patch.dict(os.environ, {}, clear=True), \
                redirect_stderr(io.StringIO()):
            sonar_cxx_plugin.bootstrap_cxx_quality_profile(URL, "tok")
        self.assertEqual(fake.set_default_calls, [])

    def test_idempotent_second_run(self) -> None:
        fake = FakeSonar([SONAR_WAY])
        _run(fake)
        name, err = _run(fake)
        self.assertEqual(name, "EasyScan CXX")
        self.assertEqual(fake.copies, 1)
        self.assertEqual(fake.set_default_calls, ["EasyScan CXX"])  # not re-set
        self.assertEqual(fake.default(), "EasyScan CXX")
        self.assertEqual(err, "")

    def test_custom_default_kept_on_every_run(self) -> None:
        custom = {"key": "cb", "name": "House C++", "isDefault": True, "isBuiltIn": False}
        fake = FakeSonar([dict(SONAR_WAY, isDefault=False), custom])
        _run(fake)
        _run(fake)
        self.assertEqual(fake.set_default_calls, [])
        self.assertEqual(fake.default(), "House C++")

    def test_profile_list_unavailable_changes_nothing(self) -> None:
        def api(url, token, method, path, **kw):
            if path == "/api/qualityprofiles/search" and kw.get("query") == {"language": "cxx"}:
                api.calls += 1
                if api.calls > 2:  # the default check (after find/copy lookups) fails
                    return 503, ""
            return fake(url, token, method, path, **kw)

        api.calls = 0
        fake = FakeSonar([SONAR_WAY])
        with patch.object(sonar_cxx_plugin, "api", side_effect=api), \
                patch("sonar_runtime.read_settings", return_value={}), \
                patch.dict(os.environ, {}, clear=True), \
                redirect_stderr(io.StringIO()) as err:
            sonar_cxx_plugin.bootstrap_cxx_quality_profile(URL, "tok")
        self.assertEqual(fake.set_default_calls, [])
        self.assertIn("left unchanged", err.getvalue())


if __name__ == "__main__":
    unittest.main()
