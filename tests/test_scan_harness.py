"""Tests for the scan harness: project detection, routing, and exit-code checks."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from scanners._util import ensure_exit, find_binary  # noqa: E402
from scanners.config import AUTO, DEFAULT_SCANNER_CONFIG  # noqa: E402
from scanners.detect import detect_project  # noqa: E402
from scanners.gate import gated_issues, parse_threshold, summarize  # noqa: E402
from scanners.routing import MODE_MANUAL, plan_scanners  # noqa: E402


def _write(root: Path, rel: str, text: str = "", *, executable: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _mixed_project(root: Path) -> None:
    _write(root, "app/main.py", "print('hi')\n")
    _write(root, "tests/test_main.py", "assert True\n")
    _write(root, "bin/tool", "#!/usr/bin/env python3\nprint(1)\n", executable=True)
    _write(root, "bin/deploy", "#!/bin/bash\necho hi\n", executable=True)
    _write(root, "scripts/setup.sh", "echo hi\n")
    _write(root, "native/core.cpp", "int main() { return 0; }\n")
    _write(root, "native/core.h", "#pragma once\n")
    _write(root, "build/compile_commands.json", "[]\n")
    _write(root, "docker/Dockerfile", "FROM alpine\n")
    _write(root, "requirements-dev.txt", "pytest\n")
    _write(root, "requirements.txt", "requests\n")
    _write(root, "web/package-lock.json", "{}\n")
    _write(root, "web/app.ts", "export {}\n")
    # Excluded trees must never be routed.
    _write(root, "node_modules/pkg/index.py", "x = 1\n")
    _write(root, ".venv/lib/site.py", "x = 1\n")


class DetectTests(unittest.TestCase):
    def test_classifies_mixed_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _mixed_project(root)
            profile = detect_project(root)
        self.assertEqual(profile.python_files, ["app/main.py", "tests/test_main.py"])
        self.assertEqual(profile.python_scripts, ["bin/tool"])
        self.assertEqual(sorted(profile.shell_files), ["bin/deploy", "scripts/setup.sh"])
        self.assertEqual(profile.cpp_files, ["native/core.cpp"])
        self.assertEqual(profile.c_files, ["native/core.h"])
        self.assertEqual(profile.dockerfiles, ["docker/Dockerfile"])
        self.assertEqual(profile.compile_commands, "build/compile_commands.json")
        self.assertEqual(profile.requirements_files, ["requirements-dev.txt", "requirements.txt"])
        self.assertIn("web/package-lock.json", profile.dependency_manifests)
        self.assertIn("typescript", profile.languages())
        self.assertEqual(profile.python_targets(), ["app", "tests", "bin/tool"])
        self.assertEqual(profile.python_targets(include_tests=False), ["app", "bin/tool"])
        self.assertEqual(profile.c_family_targets(), ["native"])

    def test_extra_excludes_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "vendor/lib.py", "x = 1\n")
            _write(root, "src/app.py", "x = 1\n")
            with mock.patch.dict(os.environ, {"EASYSCAN_EXCLUDE_DIRS": "vendor"}):
                profile = detect_project(root)
        self.assertEqual(profile.python_files, ["src/app.py"])

    def test_empty_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = detect_project(Path(tmp))
        self.assertFalse(profile.has_code)
        self.assertEqual(profile.file_count, 0)


class RoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _mixed_project(self.root)
        self.profile = detect_project(self.root)
        self.config = {name: dict(cfg) for name, cfg in DEFAULT_SCANNER_CONFIG.items()}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _plan(self, *, installed: set[str] | None = None, mode: str = "auto", sonar=(False, "down")):
        installed = set(DEFAULT_SCANNER_CONFIG) | {"osv-scanner", "scan-build"} if installed is None else installed
        with mock.patch(
            "scanners.routing.find_binary",
            side_effect=lambda b: f"/usr/bin/{b}" if b in installed else None,
        ):
            resolved, decisions = plan_scanners(
                self.config, self.profile, mode=mode, sonar_probe=lambda _ctx: sonar
            )
        return resolved, {d.name: d for d in decisions}

    def test_auto_routes_each_file_type(self) -> None:
        resolved, d = self._plan()
        for name in ("ruff", "bandit", "shellcheck", "cppcheck", "flawfinder", "clang-tidy",
                     "hadolint", "semgrep", "gitleaks", "pip-audit", "osv"):
            self.assertTrue(d[name].enabled, f"{name}: {d[name].reason}")
            self.assertTrue(resolved[name]["enabled"] is True, name)
        self.assertEqual(resolved["ruff"]["paths"], ["app", "tests", "bin/tool"])
        self.assertEqual(resolved["bandit"]["paths"], ["app", "bin/tool"])
        self.assertEqual(resolved["shellcheck"]["paths"], ["bin/deploy", "scripts/setup.sh"])
        self.assertEqual(resolved["cppcheck"]["paths"], ["native"])
        self.assertEqual(resolved["hadolint"]["paths"], ["docker/Dockerfile"])
        self.assertEqual(resolved["clang-tidy"]["compile_commands"], "build/compile_commands.json")
        self.assertEqual(resolved["pip-audit"]["requirements"], "requirements.txt")

    def test_auto_skips_tools_needing_input(self) -> None:
        _, d = self._plan()
        for name in ("asan", "ubsan", "valgrind", "drmemory"):
            self.assertFalse(d[name].enabled, name)
            self.assertIn("run command", d[name].reason)
        self.assertFalse(d["clang-analyzer"].enabled)
        self.assertIn("build_command", d["clang-analyzer"].reason)

    def test_auto_skips_missing_binaries_with_hint(self) -> None:
        _, d = self._plan(installed={"ruff"})
        self.assertTrue(d["ruff"].enabled)
        self.assertFalse(d["bandit"].enabled)
        self.assertIn("easyscan-install-scanners", d["bandit"].reason)
        self.assertIn("osv-scanner", d["osv"].reason)

    def test_sonar_follows_probe(self) -> None:
        _, d = self._plan(sonar=(False, "server down"))
        self.assertFalse(d["sonar"].enabled)
        self.assertIn("server down", d["sonar"].reason)
        _, d = self._plan(sonar=(True, "server ready"))
        self.assertTrue(d["sonar"].enabled)

    def test_not_applicable_when_no_matching_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp), "main.go", "package main\n")
            self.profile = detect_project(Path(tmp))
            _, d = self._plan()
        self.assertFalse(d["ruff"].enabled)
        self.assertEqual(d["ruff"].reason, "no Python files")
        self.assertTrue(d["semgrep"].enabled)

    def test_explicit_flags_win(self) -> None:
        self.config["ruff"]["enabled"] = False
        self.config["cppcheck"]["enabled"] = True
        self.config["cppcheck"]["paths"] = ["native/core.cpp"]
        resolved, d = self._plan(installed=set())
        self.assertFalse(d["ruff"].enabled)
        self.assertEqual(d["ruff"].reason, "disabled")
        # Forced on even though the tool is "missing"; user-set paths untouched.
        self.assertTrue(d["cppcheck"].enabled)
        self.assertEqual(d["cppcheck"].origin, "explicit")
        self.assertEqual(resolved["cppcheck"]["paths"], ["native/core.cpp"])

    def test_string_flags_from_policy(self) -> None:
        self.config["ruff"]["enabled"] = "off"
        self.config["bandit"]["enabled"] = "YES"
        self.config["semgrep"]["enabled"] = "maybe"
        _, d = self._plan()
        self.assertFalse(d["ruff"].enabled)
        self.assertEqual(d["bandit"].origin, "explicit")
        self.assertEqual(d["semgrep"].origin, "auto")

    def test_explicit_enable_gets_routed_paths(self) -> None:
        self.config["ruff"]["enabled"] = True
        resolved, _ = self._plan()
        self.assertEqual(resolved["ruff"]["paths"], ["app", "tests", "bin/tool"])

    def test_manual_mode_is_sonar_only(self) -> None:
        resolved, d = self._plan(mode=MODE_MANUAL)
        self.assertTrue(d["sonar"].enabled)
        self.assertEqual(d["sonar"].origin, "manual")
        self.assertEqual([n for n, c in resolved.items() if c["enabled"] is True], ["sonar"])

    def test_dynamic_scanner_with_command(self) -> None:
        self.config["valgrind"]["command"] = ["./a.out"]
        _, d = self._plan()
        self.assertTrue(d["valgrind"].enabled)
        _, d = self._plan(installed={"ruff"})
        self.assertFalse(d["valgrind"].enabled)

    def test_no_auto_left_after_planning(self) -> None:
        resolved, _ = self._plan()
        self.assertFalse(any(cfg["enabled"] == AUTO for cfg in resolved.values()))


class EnsureExitTests(unittest.TestCase):
    def test_accepts_findings_exit_code(self) -> None:
        proc = subprocess.CompletedProcess(["x"], 1, stdout="[]", stderr="")
        self.assertIs(ensure_exit(proc, "ruff"), proc)

    def test_raises_on_crash_with_last_line(self) -> None:
        proc = subprocess.CompletedProcess(["x"], 2, stdout="", stderr="trace\nProxyError: 403\n")
        with self.assertRaisesRegex(RuntimeError, "semgrep exited 2: ProxyError: 403"):
            ensure_exit(proc, "semgrep")

    def test_custom_ok_codes(self) -> None:
        proc = subprocess.CompletedProcess(["x"], 128, stdout="", stderr="")
        ensure_exit(proc, "osv-scanner", (0, 1, 128))


class GateTests(unittest.TestCase):
    issues = [
        {"source": "bandit", "severity": "CRITICAL", "status": "OPEN"},
        {"source": "semgrep", "severity": "MAJOR", "status": "OPEN"},
        {"source": "ruff", "severity": "MINOR", "status": "OPEN"},
        {"source": "sonar", "severity": "BLOCKER", "status": "RESOLVED"},
    ]

    def test_aliases(self) -> None:
        self.assertEqual(parse_threshold("medium"), "MAJOR")
        self.assertEqual(parse_threshold("High"), "CRITICAL")
        self.assertEqual(parse_threshold("low"), "MINOR")
        self.assertEqual(parse_threshold("blocker"), "BLOCKER")
        with self.assertRaisesRegex(ValueError, "unknown severity"):
            parse_threshold("severe")

    def test_medium_and_above(self) -> None:
        failing = gated_issues(self.issues, "MAJOR")
        self.assertEqual([i["source"] for i in failing], ["bandit", "semgrep"])
        self.assertEqual(summarize(failing), "bandit 1, semgrep 1")

    def test_resolved_never_gate(self) -> None:
        self.assertEqual(gated_issues(self.issues, "BLOCKER"), [])


class GateCliTests(unittest.TestCase):
    """easyscan-scan exits 3 when a finding meets --fail-on-severity."""

    def _run(self, *extra: str) -> subprocess.CompletedProcess[str]:
        script = ROOT / "bin" / "easyscan-scan"
        # Fake ruff that reports one finding; S (security) codes map to MAJOR.
        fake = (
            "#!/bin/sh\n"
            "echo '[{\"code\":\"S602\",\"filename\":\"'\"$PWD\"'/pkg/mod.py\","
            "\"message\":\"undefined name\",\"location\":{\"row\":1}}]'\n"
            "exit 1\n"
        )
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as tools:
            _write(Path(ws), "pkg/mod.py", "x = y\n")
            _write(Path(tools), "bin/ruff", fake, executable=True)
            env = {
                **os.environ,
                "EASYSCAN_TOOLS_HOME": tools,
                "PATH": "/usr/bin:/bin",
                "SONARQUBE_URL": "http://127.0.0.1:1",
            }
            return subprocess.run(
                [sys.executable, str(script), "--workspace", ws, "--scanners", "ruff", *extra],
                capture_output=True, text=True, env=env, check=False, timeout=120,
            )

    def test_gate_fails_on_major(self) -> None:
        proc = self._run("--fail-on-severity", "medium")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("Severity gate MAJOR+: 1 finding(s) (ruff 1) — FAIL", proc.stdout)

    def test_gate_passes_below_threshold(self) -> None:
        proc = self._run("--fail-on-severity", "critical")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("— pass", proc.stdout)

    def test_no_gate_by_default(self) -> None:
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("Severity gate", proc.stdout)


class FindBinaryTests(unittest.TestCase):
    def test_finds_tools_bin_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            _write(bin_dir.parent, "bin/faketool-xyz", "#!/bin/sh\n", executable=True)
            with mock.patch.dict(os.environ, {"EASYSCAN_TOOLS_HOME": tmp}):
                self.assertEqual(find_binary("faketool-xyz"), str(bin_dir / "faketool-xyz"))
                self.assertIsNone(find_binary("missing-tool-xyz"))


class EasyscanScanPlanTests(unittest.TestCase):
    def test_plan_json_end_to_end(self) -> None:
        script = ROOT / "bin" / "easyscan-scan"
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as tools:
            ws = Path(tmp)
            _write(ws, "pkg/mod.py", "x = 1\n")
            _write(Path(tools), "bin/ruff", "#!/bin/sh\n", executable=True)
            env = {
                **os.environ,
                "EASYSCAN_TOOLS_HOME": tools,
                "PATH": "/usr/bin:/bin",
                "SONARQUBE_URL": "http://127.0.0.1:1",
            }
            proc = subprocess.run(
                [sys.executable, str(script), "--workspace", str(ws), "--plan", "--json"],
                capture_output=True, text=True, env=env, check=False, timeout=120,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        by_name = {d["name"]: d for d in data["scanners"]}
        self.assertTrue(by_name["ruff"]["enabled"])
        self.assertEqual(by_name["ruff"]["targets"], ["pkg"])
        self.assertFalse(by_name["cppcheck"]["enabled"])
        self.assertEqual(data["profile"]["languages"], ["python"])


if __name__ == "__main__":
    unittest.main()
