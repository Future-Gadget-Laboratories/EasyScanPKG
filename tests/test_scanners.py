#!/usr/bin/env python3
"""Unit tests for multi-scanner config, parsers, merge, and registry."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from scanners.base import Finding  # noqa: E402
from scanners.clang_tidy import ClangTidyScanner, parse_clang_tidy_output  # noqa: E402
from scanners.config import (  # noqa: E402
    DEFAULT_SCANNER_CONFIG,
    apply_cli_overrides,
    apply_env_overrides,
    resolve_scanner_config,
)
from scanners.drmemory import DrMemoryScanner, parse_drmemory_output  # noqa: E402
from scanners.merge import dedupe_findings, findings_to_issues, merge_findings  # noqa: E402
from scanners.registry import available_scanners, run_scanners  # noqa: E402


class ScannerConfigTests(unittest.TestCase):
    def test_defaults_sonar_on_others_off(self) -> None:
        cfg = resolve_scanner_config()
        self.assertTrue(cfg["sonar"]["enabled"])
        self.assertFalse(cfg["clang-tidy"]["enabled"])
        self.assertFalse(cfg["drmemory"]["enabled"])

    def test_cli_enable_disable(self) -> None:
        cfg = apply_cli_overrides(
            DEFAULT_SCANNER_CONFIG,
            enable=["clang-tidy"],
            disable=["sonar"],
        )
        self.assertFalse(cfg["sonar"]["enabled"])
        self.assertTrue(cfg["clang-tidy"]["enabled"])

    def test_cli_only(self) -> None:
        cfg = apply_cli_overrides(
            DEFAULT_SCANNER_CONFIG,
            only=["drmemory"],
        )
        self.assertFalse(cfg["sonar"]["enabled"])
        self.assertFalse(cfg["clang-tidy"]["enabled"])
        self.assertTrue(cfg["drmemory"]["enabled"])

    def test_env_scanners_csv(self) -> None:
        with mock.patch.dict(os.environ, {"EASYSCAN_SCANNERS": "clang-tidy,drmemory"}, clear=False):
            cfg = apply_env_overrides(DEFAULT_SCANNER_CONFIG)
        self.assertFalse(cfg["sonar"]["enabled"])
        self.assertTrue(cfg["clang-tidy"]["enabled"])
        self.assertTrue(cfg["drmemory"]["enabled"])

    def test_workspace_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            sft = ws / ".sft"
            sft.mkdir()
            (sft / "sonar-policy.json").write_text(
                json.dumps(
                    {
                        "scanners": {
                            "clang-tidy": {
                                "enabled": True,
                                "compile_commands": "build/compile_commands.json",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            cfg = resolve_scanner_config(ws)
            self.assertTrue(cfg["clang-tidy"]["enabled"])
            self.assertEqual(
                cfg["clang-tidy"]["compile_commands"], "build/compile_commands.json"
            )


class ClangTidyParserTests(unittest.TestCase):
    def test_parse_diagnostics(self) -> None:
        text = """\
/tmp/proj/src/a.cpp:10:5: warning: use after move [bugprone-use-after-move]
/tmp/proj/src/a.cpp:12:1: note: move occurred here
/tmp/proj/src/b.cpp:3:1: error: null dereference [clang-analyzer-core.NullDereference]
"""
        findings = parse_clang_tidy_output(text, workspace=Path("/tmp/proj"))
        self.assertEqual(len(findings), 2)
        self.assertEqual(findings[0].source, "clang-tidy")
        self.assertEqual(findings[0].rule, "clang-tidy:bugprone-use-after-move")
        self.assertEqual(findings[0].file, "src/a.cpp")
        self.assertEqual(findings[0].line, 10)
        self.assertEqual(findings[1].severity, "CRITICAL")

    def test_missing_compile_commands_errors(self) -> None:
        scanner = ClangTidyScanner()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                scanner.run(Path(tmp), {"enabled": True, "binary": "clang-tidy"})


class DrMemoryParserTests(unittest.TestCase):
    def test_parse_errors(self) -> None:
        text = """\
Error #1: UNADDRESSABLE ACCESS: reading 4 byte(s)
# 0 main [/tmp/proj/src/a.c:42]
Error #2: LEAK 32 direct bytes
# 0 malloc
# 1 helper [src/b.c:9]
"""
        findings = parse_drmemory_output(text, workspace=Path("/tmp/proj"))
        self.assertEqual(len(findings), 2)
        self.assertEqual(findings[0].rule, "drmemory:unaddressable-access")
        self.assertEqual(findings[0].file, "src/a.c")
        self.assertEqual(findings[0].line, 42)
        self.assertEqual(findings[1].rule, "drmemory:leak")

    def test_missing_command_errors(self) -> None:
        scanner = DrMemoryScanner()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                scanner.run(Path(tmp), {"enabled": True, "binary": "drmemory"})


class MergeRegistryTests(unittest.TestCase):
    def test_available_scanners(self) -> None:
        names = available_scanners()
        self.assertEqual(set(names), {"sonar", "clang-tidy", "drmemory"})

    def test_dedupe_across_sources(self) -> None:
        findings = [
            Finding(
                source="sonar",
                severity="MAJOR",
                type="BUG",
                rule="cpp:S1234",
                message="use after move",
                file="a.cpp",
                line=10,
                key="A-1",
            ),
            Finding(
                source="clang-tidy",
                severity="MAJOR",
                type="BUG",
                rule="clang-tidy:bugprone-use-after-move",
                message="use after move",
                file="a.cpp",
                line=10,
            ),
            Finding(
                source="clang-tidy",
                severity="MINOR",
                type="CODE_SMELL",
                rule="clang-tidy:readability-braces",
                message="braces",
                file="a.cpp",
                line=20,
            ),
        ]
        # Without shared rule token, both first two may remain; exercise merge+dedupe path.
        merged = merge_findings(findings)
        self.assertEqual(len(merged), 3)
        issues = findings_to_issues(merged)
        self.assertEqual(len(issues), 3)
        self.assertEqual(issues[0]["source"], "sonar")

    def test_run_scanners_skips_disabled(self) -> None:
        cfg = {
            "sonar": {"enabled": False},
            "clang-tidy": {"enabled": False},
            "drmemory": {"enabled": False},
        }
        with tempfile.TemporaryDirectory() as tmp:
            findings, run, skipped = run_scanners(Path(tmp), cfg, context={})
        self.assertEqual(findings, [])
        self.assertEqual(run, [])
        self.assertEqual(set(skipped), {"sonar", "clang-tidy", "drmemory"})


if __name__ == "__main__":
    unittest.main()
