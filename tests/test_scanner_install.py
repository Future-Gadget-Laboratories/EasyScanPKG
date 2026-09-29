"""Tests for the scanner tool installer (no network, no package managers)."""

from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import scanner_install as si  # noqa: E402


def _gitleaks_release() -> dict:
    return {
        "tag_name": "v8.99.0",
        "assets": [
            {"name": "gitleaks_8.99.0_checksums.txt", "browser_download_url": "https://x/checksums.txt"},
            {"name": "gitleaks_8.99.0_darwin_arm64.tar.gz", "browser_download_url": "https://x/darwin"},
            {"name": "gitleaks_8.99.0_linux_x64.tar.gz", "browser_download_url": "https://x/linux-x64"},
            {"name": "gitleaks_8.99.0_linux_arm64.tar.gz", "browser_download_url": "https://x/linux-arm64"},
        ],
    }


def _tar_with(member: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(member)
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


class SelectToolsTests(unittest.TestCase):
    def test_default_excludes_opt_in(self) -> None:
        names = [t.name for t in si.select_tools()]
        self.assertIn("ruff", names)
        self.assertIn("osv", names)
        self.assertNotIn("drmemory", names)

    def test_with_only_skip(self) -> None:
        self.assertIn("drmemory", [t.name for t in si.select_tools(with_optional=["drmemory"])])
        self.assertEqual([t.name for t in si.select_tools(only=["ruff", "osv"])], ["ruff", "osv"])
        self.assertNotIn("semgrep", [t.name for t in si.select_tools(skip=["semgrep"])])

    def test_unknown_tool(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown tool"):
            si.select_tools(only=["nope"])

    def test_every_scanner_with_a_binary_is_in_catalog(self) -> None:
        from scanners.config import DEFAULT_SCANNER_CONFIG

        installable = set(DEFAULT_SCANNER_CONFIG) - set(si.BUILTIN_NOTES)
        self.assertEqual(installable, set(si.catalog_by_name()))


class AssetTests(unittest.TestCase):
    def test_select_gitleaks_linux_x64(self) -> None:
        method = si.catalog_by_name()["gitleaks"].methods[1]
        asset = si.select_asset(_gitleaks_release(), method, "x86_64")
        self.assertEqual(asset["name"], "gitleaks_8.99.0_linux_x64.tar.gz")
        self.assertEqual(si.select_asset(_gitleaks_release(), method, "arm64")["name"],
                         "gitleaks_8.99.0_linux_arm64.tar.gz")

    def test_hadolint_case_insensitive_and_ignores_checksum_files(self) -> None:
        method = si.catalog_by_name()["hadolint"].methods[1]
        release = {"assets": [
            {"name": "hadolint-Linux-x86_64.sha256"},
            {"name": "hadolint-Linux-x86_64"},
        ]}
        self.assertEqual(si.select_asset(release, method, "x86_64")["name"], "hadolint-Linux-x86_64")

    def test_unsupported_arch(self) -> None:
        method = si.catalog_by_name()["drmemory"].methods[0]
        self.assertIsNone(si.select_asset({"assets": []}, method, "arm64"))


class ChecksumTests(unittest.TestCase):
    sha = "a" * 64

    def test_parse_listing_and_single(self) -> None:
        listing = f"{'b' * 64}  other.tar.gz\n{self.sha}  wanted.tar.gz\n"
        self.assertEqual(si.parse_checksums(listing, "wanted.tar.gz"), self.sha)
        self.assertEqual(si.parse_checksums(f"{self.sha}\n", "anything"), self.sha)
        self.assertIsNone(si.parse_checksums("garbage", "wanted.tar.gz"))

    def test_digest_field_preferred(self) -> None:
        asset = {"name": "x", "digest": f"sha256:{self.sha.upper()}"}
        fetch = mock.Mock()
        self.assertEqual(si.expected_sha256({"assets": []}, asset, fetch), self.sha)
        fetch.assert_not_called()

    def test_checksum_file_fallback(self) -> None:
        release = _gitleaks_release()
        asset = release["assets"][2]
        fetch = mock.Mock(return_value=f"{self.sha}  {asset['name']}\n")
        self.assertEqual(si.expected_sha256(release, asset, fetch), self.sha)
        fetch.assert_called_once_with("https://x/checksums.txt")

    def test_no_checksum(self) -> None:
        asset = {"name": "bin"}
        self.assertIsNone(si.expected_sha256({"assets": [asset]}, asset, mock.Mock()))


class GitHubInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"EASYSCAN_TOOLS_HOME": self._tmp.name})
        self.env.start()
        self.linux = mock.patch.object(si.platform, "system", return_value="Linux")
        self.linux.start()
        self.arch = mock.patch.object(si, "machine_arch", return_value="x86_64")
        self.arch.start()
        self.runner = si.Runner(log=lambda _m: None)
        self.method = si.catalog_by_name()["gitleaks"].methods[1]

    def tearDown(self) -> None:
        mock.patch.stopall()
        self._tmp.cleanup()

    def _install(self, archive: bytes, checksum: str | None, **kw):
        def fake_download(_url: str, dest: Path) -> str:
            dest.write_bytes(archive)
            return hashlib.sha256(archive).hexdigest()

        listing = f"{checksum}  gitleaks_8.99.0_linux_x64.tar.gz\n" if checksum else ""
        with mock.patch.object(si, "_github_json", return_value=_gitleaks_release()), \
             mock.patch.object(si, "_fetch_text", return_value=listing), \
             mock.patch.object(si, "_download", side_effect=fake_download):
            return si.install_github(self.method, "gitleaks", runner=self.runner, **kw)

    def test_verified_install_extracts_member(self) -> None:
        archive = _tar_with("gitleaks", b"#!/bin/sh\necho gitleaks\n")
        path, detail = self._install(archive, hashlib.sha256(archive).hexdigest(), allow_unverified=False)
        self.assertEqual(detail, "v8.99.0")
        self.assertEqual(Path(path).read_bytes(), b"#!/bin/sh\necho gitleaks\n")
        self.assertTrue(os.access(path, os.X_OK))

    def test_checksum_mismatch_rejected(self) -> None:
        archive = _tar_with("gitleaks", b"payload")
        path, detail = self._install(archive, "0" * 64, allow_unverified=False)
        self.assertIsNone(path)
        self.assertIn("mismatch", detail)
        self.assertFalse((Path(self._tmp.name) / "bin" / "gitleaks").exists())

    def test_unverified_refused_by_default(self) -> None:
        archive = _tar_with("gitleaks", b"payload")
        path, detail = self._install(archive, None, allow_unverified=False)
        self.assertIsNone(path)
        self.assertIn("--allow-unverified", detail)
        path, _ = self._install(archive, None, allow_unverified=True)
        self.assertIsNotNone(path)

    def test_path_traversal_rejected(self) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo("../escape")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        buf.seek(0)
        with tarfile.open(fileobj=buf) as tar, self.assertRaisesRegex(RuntimeError, "unsafe"):
            si._safe_extract(tar, Path(self._tmp.name) / "out")


class InstallOrchestrationTests(unittest.TestCase):
    def test_present_tools_are_left_alone(self) -> None:
        tools = si.select_tools(only=["ruff"])
        with mock.patch.object(si, "find_binary", return_value="/usr/bin/ruff"):
            results = si.install_tools(tools, runner=si.Runner(dry_run=True, log=lambda _m: None))
        self.assertEqual(results[0].status, "present")

    def test_falls_back_to_pip_when_no_system_packages(self) -> None:
        tools = si.select_tools(only=["shellcheck", "cppcheck"])
        runner = si.Runner(dry_run=True, log=lambda _m: None)
        with mock.patch.object(si, "find_binary", return_value=None), \
             mock.patch.object(si, "_package_manager", return_value=None):
            results = {r.name: r for r in si.install_tools(tools, runner=runner)}
        self.assertEqual(results["shellcheck"].status, "planned")
        self.assertEqual(results["shellcheck"].via, "pip:shellcheck-py")
        self.assertEqual(results["cppcheck"].status, "failed")
        self.assertIn("no apt-get/dnf", results["cppcheck"].detail)

    def test_disabled_methods_reported(self) -> None:
        tools = si.select_tools(only=["osv"])
        runner = si.Runner(dry_run=True, log=lambda _m: None)
        with mock.patch.object(si, "find_binary", return_value=None):
            results = si.install_tools(tools, runner=runner, use_github=False)
        self.assertEqual(results[0].status, "failed")
        self.assertIn("disabled", results[0].detail)


if __name__ == "__main__":
    unittest.main()
