"""Installer tests use local zip fixtures and never access the network."""

from __future__ import annotations

import hashlib
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sins.engines import ast_grep


def archive(entries: dict[str, bytes]) -> bytes:
    """Build a small in-memory test archive."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
    return output.getvalue()


class EngineInstallTests(unittest.TestCase):
    def _install_with_archive(self, root: Path, payload: bytes, expected: str) -> Path:
        zip_path = root / "fixture.zip"
        zip_path.write_bytes(payload)
        platform_key = ast_grep._platform_key()
        fake_pins = {"version": "0.51.3", "platforms": {
            platform_key: {"url": zip_path.as_uri(), "zip_sha256": expected}}}
        with patch.dict(os.environ, {"SINS_ALLOW_FILE_URL": "1"}), patch.object(ast_grep, "_pins",
                                                                          return_value=fake_pins):
            return ast_grep.install(root / "installed")

    def test_good_zip_installs_and_stamps_hash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-install-good-") as folder:
            root = Path(folder)
            payload = archive({"ast-grep": b"fixture binary", "sg": b"must be ignored"})
            binary = self._install_with_archive(root, payload, hashlib.sha256(payload).hexdigest())
            self.assertEqual(binary.read_bytes(), b"fixture binary")
            self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
            self.assertEqual((binary.parent / "ast-grep.sha256").read_text().strip(),
                             hashlib.sha256(binary.read_bytes()).hexdigest())
            self.assertFalse((binary.parent / "sg").exists())

    def test_bad_hash_refuses_install_without_leftovers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-install-bad-hash-") as folder:
            root = Path(folder)
            payload = archive({"ast-grep": b"fixture"})
            with self.assertRaisesRegex(ast_grep.EngineError, "archive sha256 mismatch"):
                self._install_with_archive(root, payload, "0" * 64)
            self.assertFalse((root / "installed").exists())

    def test_parent_member_is_not_extracted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-install-slip-") as folder:
            root = Path(folder)
            payload = archive({"../ast-grep": b"unsafe"})
            with self.assertRaisesRegex(ast_grep.EngineError, "exactly one ast-grep member"):
                self._install_with_archive(root, payload, hashlib.sha256(payload).hexdigest())
            self.assertFalse((root / "ast-grep").exists())
            self.assertFalse((root / "installed").exists())

    def test_file_url_requires_opt_in_and_http_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-install-scheme-") as folder:
            path = Path(folder) / "fixture.zip"
            path.write_bytes(b"fixture")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SINS_ALLOW_FILE_URL", None)
                with self.assertRaisesRegex(ast_grep.EngineError, "HTTPS"):
                    ast_grep._download(path.as_uri())
            with patch.dict(os.environ, {"SINS_ALLOW_FILE_URL": "1"}):
                self.assertEqual(ast_grep._download(path.as_uri()), b"fixture")
                with self.assertRaisesRegex(ast_grep.EngineError, "HTTPS"):
                    ast_grep._download("http://example.invalid/ast-grep.zip")

    def test_install_replaces_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sins-install-replace-") as folder:
            root = Path(folder)
            destination = root / "installed"
            destination.mkdir()
            (destination / "old-file").write_text("old", encoding="utf-8")
            payload = archive({"ast-grep": b"new fixture"})
            zip_path = root / "fixture.zip"
            zip_path.write_bytes(payload)
            platform_key = ast_grep._platform_key()
            fake_pins = {"version": "0.51.3", "platforms": {platform_key: {
                "url": zip_path.as_uri(), "zip_sha256": hashlib.sha256(payload).hexdigest()}}}
            with patch.dict(os.environ, {"SINS_ALLOW_FILE_URL": "1"}), patch.object(
                    ast_grep, "_pins", return_value=fake_pins):
                binary = ast_grep.install(destination)
            self.assertEqual(binary.read_bytes(), b"new fixture")
            self.assertFalse((destination / "old-file").exists())


if __name__ == "__main__":
    unittest.main()
