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

from scanners.bandit import parse_bandit_json  # noqa: E402
from scanners.base import Finding  # noqa: E402
from scanners.clang_analyzer import (  # noqa: E402
    parse_clang_analyzer_plist,
    parse_clang_analyzer_sarif,
)
from scanners.clang_tidy import ClangTidyScanner, parse_clang_tidy_output  # noqa: E402
from scanners.config import (  # noqa: E402
    DEFAULT_SCANNER_CONFIG,
    apply_cli_overrides,
    apply_env_overrides,
    resolve_scanner_config,
)
from scanners.cppcheck import parse_cppcheck_xml  # noqa: E402
from scanners.deps import parse_osv_json, parse_pip_audit_json  # noqa: E402
from scanners.drmemory import DrMemoryScanner, parse_drmemory_output  # noqa: E402
from scanners.flawfinder import parse_flawfinder_csv  # noqa: E402
from scanners.gitleaks import parse_gitleaks_json  # noqa: E402
from scanners.hadolint import parse_hadolint_json  # noqa: E402
from scanners.merge import dedupe_findings, findings_to_issues, merge_findings  # noqa: E402
from scanners.registry import available_scanners, run_scanners  # noqa: E402
from scanners.ruff import parse_ruff_json  # noqa: E402
from scanners.sanitizers import ASanScanner, parse_asan_output, parse_ubsan_output  # noqa: E402
from scanners.semgrep import parse_semgrep_json  # noqa: E402
from scanners.shellcheck import parse_shellcheck_json  # noqa: E402
from scanners.valgrind import ValgrindScanner, parse_valgrind_xml  # noqa: E402

_ALL_SCANNERS = {
    "sonar",
    "clang-tidy",
    "drmemory",
    "cppcheck",
    "ruff",
    "shellcheck",
    "semgrep",
    "bandit",
    "asan",
    "ubsan",
    "valgrind",
    "gitleaks",
    "pip-audit",
    "osv",
    "flawfinder",
    "clang-analyzer",
    "hadolint",
}


class ScannerConfigTests(unittest.TestCase):
    def test_defaults_sonar_on_others_off(self) -> None:
        cfg = resolve_scanner_config()
        self.assertTrue(cfg["sonar"]["enabled"])
        for name in _ALL_SCANNERS - {"sonar"}:
            self.assertFalse(cfg[name]["enabled"], name)

    def test_cli_enable_disable(self) -> None:
        cfg = apply_cli_overrides(
            DEFAULT_SCANNER_CONFIG,
            enable=["clang-tidy", "ruff"],
            disable=["sonar"],
        )
        self.assertFalse(cfg["sonar"]["enabled"])
        self.assertTrue(cfg["clang-tidy"]["enabled"])
        self.assertTrue(cfg["ruff"]["enabled"])

    def test_cli_only(self) -> None:
        cfg = apply_cli_overrides(DEFAULT_SCANNER_CONFIG, only=["drmemory"])
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
                            },
                            "cppcheck": {"enabled": True},
                        }
                    }
                ),
                encoding="utf-8",
            )
            cfg = resolve_scanner_config(ws)
            self.assertTrue(cfg["clang-tidy"]["enabled"])
            self.assertEqual(
                cfg["clang-tidy"]["compile_commands"],
                "build/compile_commands.json",
            )
            self.assertTrue(cfg["cppcheck"]["enabled"])

    def test_asan_command_cli(self) -> None:
        cfg = apply_cli_overrides(
            DEFAULT_SCANNER_CONFIG,
            enable=["asan"],
            asan_command=["./build/a.out", "--flag"],
        )
        self.assertTrue(cfg["asan"]["enabled"])
        self.assertTrue(cfg["asan"]["command"])


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
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                ClangTidyScanner().run(Path(tmp), {"enabled": True, "binary": "clang-tidy"})


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
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                DrMemoryScanner().run(Path(tmp), {"enabled": True, "binary": "drmemory"})


class Wave1ParserTests(unittest.TestCase):
    def test_cppcheck_xml(self) -> None:
        xml = """<?xml version="1.0"?>
<results version="2">
  <error id="nullPointer" severity="error" msg="Null pointer dereference">
    <location file="/tmp/proj/a.c" line="3"/>
  </error>
</results>
"""
        findings = parse_cppcheck_xml(xml, workspace=Path("/tmp/proj"))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].source, "cppcheck")
        self.assertEqual(findings[0].rule, "cppcheck:nullPointer")
        self.assertEqual(findings[0].file, "a.c")
        self.assertEqual(findings[0].line, 3)
        self.assertEqual(findings[0].severity, "CRITICAL")

    def test_ruff_json(self) -> None:
        raw = json.dumps(
            [
                {
                    "code": "E501",
                    "filename": "/tmp/proj/lib/a.py",
                    "message": "line too long",
                    "location": {"row": 2},
                }
            ]
        )
        findings = parse_ruff_json(raw, workspace=Path("/tmp/proj"))
        self.assertEqual(findings[0].source, "ruff")
        self.assertEqual(findings[0].rule, "ruff:E501")
        self.assertEqual(findings[0].type, "CODE_SMELL")

    def test_shellcheck_json(self) -> None:
        raw = json.dumps(
            [
                {
                    "file": "/tmp/proj/bin/x.sh",
                    "line": 4,
                    "level": "warning",
                    "code": 2086,
                    "message": "Double quote",
                }
            ]
        )
        findings = parse_shellcheck_json(raw, workspace=Path("/tmp/proj"))
        self.assertEqual(findings[0].rule, "shellcheck:SC2086")
        self.assertEqual(findings[0].severity, "MAJOR")


class Wave2ParserTests(unittest.TestCase):
    def test_semgrep_json(self) -> None:
        raw = json.dumps(
            {
                "results": [
                    {
                        "check_id": "python.lang.security.audit",
                        "path": "/tmp/proj/a.py",
                        "start": {"line": 8},
                        "extra": {"message": "bad", "severity": "ERROR"},
                    }
                ]
            }
        )
        findings = parse_semgrep_json(raw, workspace=Path("/tmp/proj"))
        self.assertEqual(findings[0].source, "semgrep")
        self.assertEqual(findings[0].severity, "CRITICAL")
        self.assertEqual(findings[0].type, "VULNERABILITY")

    def test_bandit_json(self) -> None:
        raw = json.dumps(
            {
                "results": [
                    {
                        "test_id": "B101",
                        "filename": "/tmp/proj/a.py",
                        "line_number": 1,
                        "issue_severity": "HIGH",
                        "issue_text": "assert used",
                    }
                ]
            }
        )
        findings = parse_bandit_json(raw, workspace=Path("/tmp/proj"))
        self.assertEqual(findings[0].rule, "bandit:B101")
        self.assertEqual(findings[0].type, "VULNERABILITY")


class Wave3ParserTests(unittest.TestCase):
    def test_asan_output(self) -> None:
        text = (
            "==1==ERROR: AddressSanitizer: heap-use-after-free on address 0x1\n"
            "    #0 0x1 in boom /tmp/proj/a.c:10\n"
        )
        findings = parse_asan_output(text, workspace=Path("/tmp/proj"))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].source, "asan")
        self.assertEqual(findings[0].file, "a.c")
        self.assertEqual(findings[0].line, 10)

    def test_ubsan_output(self) -> None:
        text = "/tmp/proj/a.c:5:1: runtime error: signed integer overflow\n"
        findings = parse_ubsan_output(text, workspace=Path("/tmp/proj"))
        self.assertEqual(findings[0].source, "ubsan")
        self.assertEqual(findings[0].file, "a.c")

    def test_valgrind_xml(self) -> None:
        xml = """\
<valgrindoutput>
  <error>
    <kind>InvalidRead</kind>
    <what>Invalid read of size 4</what>
    <stack><frame><file>/tmp/proj/a.c</file><line>9</line></frame></stack>
  </error>
</valgrindoutput>
"""
        findings = parse_valgrind_xml(xml, workspace=Path("/tmp/proj"))
        self.assertEqual(findings[0].source, "valgrind")
        self.assertIn("valgrind:", findings[0].rule)

    def test_asan_requires_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                ASanScanner().run(Path(tmp), {"enabled": True})

    def test_valgrind_requires_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                ValgrindScanner().run(
                    Path(tmp), {"enabled": True, "binary": "valgrind"}
                )


class Wave4ParserTests(unittest.TestCase):
    def test_gitleaks_json(self) -> None:
        raw = json.dumps(
            [
                {
                    "RuleID": "aws-key",
                    "File": "/tmp/proj/.env",
                    "StartLine": 2,
                    "Description": "AWS Access Key",
                }
            ]
        )
        findings = parse_gitleaks_json(raw, workspace=Path("/tmp/proj"))
        self.assertEqual(findings[0].source, "gitleaks")
        self.assertEqual(findings[0].type, "VULNERABILITY")

    def test_pip_audit_json(self) -> None:
        raw = json.dumps(
            [
                {
                    "name": "requests",
                    "version": "2.0",
                    "vulns": [{"id": "CVE-2023-1", "description": "x"}],
                }
            ]
        )
        findings = parse_pip_audit_json(raw, workspace=Path("/tmp/proj"))
        self.assertEqual(findings[0].source, "pip-audit")
        self.assertIn("CVE-2023-1", findings[0].rule)

    def test_osv_json(self) -> None:
        raw = json.dumps(
            {
                "results": [
                    {
                        "source": {"path": "/tmp/proj/requirements.txt"},
                        "packages": [
                            {
                                "package": {"name": "django"},
                                "vulnerabilities": [
                                    {"id": "GHSA-1", "summary": "bad"}
                                ],
                            }
                        ],
                    }
                ]
            }
        )
        findings = parse_osv_json(raw, workspace=Path("/tmp/proj"))
        self.assertEqual(findings[0].source, "osv")
        self.assertIn("GHSA-1", findings[0].rule)


class Wave5ParserTests(unittest.TestCase):
    def test_flawfinder_csv(self) -> None:
        text = (
            "File,Line,Column,Level,Warning,Name,CWEs\n"
            "a.c,1,1,5,use strcpy,strcpy,CWE-120\n"
        )
        findings = parse_flawfinder_csv(text, workspace=Path("/tmp/proj"))
        self.assertEqual(findings[0].source, "flawfinder")
        self.assertEqual(findings[0].severity, "CRITICAL")

    def test_clang_analyzer_plist(self) -> None:
        data = {
            "files": ["/tmp/proj/a.c"],
            "diagnostics": [
                {
                    "description": "null",
                    "check_name": "core.NullDereference",
                    "location": {"file": 0, "line": 3},
                }
            ],
        }
        findings = parse_clang_analyzer_plist(data, workspace=Path("/tmp/proj"))
        self.assertEqual(findings[0].source, "clang-analyzer")
        self.assertEqual(findings[0].file, "a.c")

    def test_clang_analyzer_sarif(self) -> None:
        raw = json.dumps(
            {
                "runs": [
                    {
                        "results": [
                            {
                                "ruleId": "core.NullDereference",
                                "message": {"text": "null"},
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {
                                                "uri": "/tmp/proj/a.c"
                                            },
                                            "region": {"startLine": 3},
                                        }
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }
        )
        findings = parse_clang_analyzer_sarif(raw, workspace=Path("/tmp/proj"))
        self.assertEqual(findings[0].rule, "clang-analyzer:core.NullDereference")

    def test_hadolint_json(self) -> None:
        raw = json.dumps(
            [
                {
                    "code": "DL3008",
                    "level": "warning",
                    "message": "pin versions",
                    "file": "Dockerfile",
                    "line": 2,
                }
            ]
        )
        findings = parse_hadolint_json(raw, workspace=Path("/tmp/proj"))
        self.assertEqual(findings[0].source, "hadolint")
        self.assertEqual(findings[0].rule, "hadolint:DL3008")


class MergeRegistryTests(unittest.TestCase):
    def test_available_scanners(self) -> None:
        self.assertEqual(set(available_scanners()), _ALL_SCANNERS)

    def test_dedupe_across_sources(self) -> None:
        findings = [
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
                source="sonar",
                severity="MAJOR",
                type="BUG",
                rule="cpp:use-after-move",
                message="use after move",
                file="a.cpp",
                line=10,
                key="A-1",
            ),
            Finding(
                source="cppcheck",
                severity="CRITICAL",
                type="BUG",
                rule="cppcheck:nullPointer",
                message="null",
                file="a.cpp",
                line=11,
            ),
            Finding(
                source="clang-analyzer",
                severity="MAJOR",
                type="BUG",
                rule="clang-analyzer:core.NullDereference",
                message="null",
                file="a.cpp",
                line=11,
            ),
            Finding(
                source="ruff",
                severity="MINOR",
                type="CODE_SMELL",
                rule="ruff:E501",
                message="long",
                file="a.py",
                line=1,
            ),
        ]
        merged = merge_findings(findings)
        self.assertEqual(len(merged), 5)
        deduped = dedupe_findings(merged)
        # use-after-move pair + null-dereference pair collapse
        self.assertEqual(len(deduped), 3)
        issues = findings_to_issues(deduped)
        self.assertEqual(len(issues), 3)

    def test_run_scanners_skips_disabled(self) -> None:
        cfg = {name: {"enabled": False} for name in _ALL_SCANNERS}
        with tempfile.TemporaryDirectory() as tmp:
            findings, run, skipped = run_scanners(Path(tmp), cfg, context={})
        self.assertEqual(findings, [])
        self.assertEqual(run, [])
        self.assertEqual(set(skipped), _ALL_SCANNERS)


if __name__ == "__main__":
    unittest.main()
