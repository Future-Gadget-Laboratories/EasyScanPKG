"""Behavioral check: bin/sonar-scan never puts the Sonar token in docker argv.

A stub `docker` on PATH records every argv it receives (one arg per line,
NUL-free) plus whether SONAR_TOKEN reached it through the environment.  The
token used here is a fake sentinel; no real credential is involved.
"""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = ROOT / "bin" / "sonar-scan"
FAKE_TOKEN = "squ_FAKE_TEST_TOKEN_0123456789abcdef"

STUB = r"""#!/usr/bin/env bash
log="$STUB_LOG_DIR/argv.log"
{
  echo "--- call"
  for a in "$@"; do printf '%s\n' "$a"; done
  if [[ "${SONAR_TOKEN:-}" == "$STUB_EXPECT_TOKEN" ]]; then echo "ENV_TOKEN=match"; else echo "ENV_TOKEN=missing"; fi
} >>"$log"
# Record any --env-file so the test can check its mode and later removal.
prev=""
for a in "$@"; do
  if [[ "$prev" == "--env-file" ]]; then
    stat -c '%a' "$a" >>"$STUB_LOG_DIR/envfile.mode" 2>/dev/null
    printf '%s\n' "$a" >>"$STUB_LOG_DIR/envfile.path"
  fi
  case "$a" in --env-file=*) f="${a#--env-file=}"; stat -c '%a' "$f" >>"$STUB_LOG_DIR/envfile.mode"; printf '%s\n' "$f" >>"$STUB_LOG_DIR/envfile.path";; esac
  prev="$a"
done
exit 0
"""


class SonarScanTokenArgvTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bindir = self.tmp / "stubbin"
        self.bindir.mkdir()
        self.logdir = self.tmp / "log"
        self.logdir.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        stub = self.bindir / "docker"
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.bindir}:/usr/bin:/bin",
            "HOME": str(self.home),
            "STUB_LOG_DIR": str(self.logdir),
            "STUB_EXPECT_TOKEN": FAKE_TOKEN,
            "SONARQUBE_TOKEN": FAKE_TOKEN,
            # Non-local URL: skip sonar-local-up; the .invalid host makes the
            # post-scan API calls fail fast without touching any real server.
            "SONARQUBE_URL": "http://sonar.invalid:9",
            "SONARQUBE_PROJECT_KEY": "argv-test",
            # Bound the post-scan Sonar waits added in #12 so .invalid cannot
            # outlast this subprocess timeout; harmless without scan_summary.
            "SFT_SCAN_CE_TIMEOUT": "1",
            "SFT_SCAN_ISSUES_TIMEOUT": "1",
        }
        return subprocess.run(
            ["bash", str(SCAN), "--workspace", str(self.ws)],
            env=env, capture_output=True, text=True, timeout=120, check=False,
        )

    def test_token_never_in_docker_argv(self) -> None:
        proc = self._run()
        log = (self.logdir / "argv.log").read_text()
        self.assertIn("run", log.split("\n"), "docker run was never invoked")
        self.assertNotIn(FAKE_TOKEN, log, "token value leaked into docker argv")
        self.assertNotIn(FAKE_TOKEN, proc.stdout + proc.stderr, "token printed")
        # The run call passes the bare name and docker gets the value via env.
        run_call = [c for c in log.split("--- call\n") if "\nrun\n" in "\n" + c][0]
        args = run_call.splitlines()
        self.assertIn("SONAR_TOKEN", args)
        self.assertFalse(any(a.startswith("SONAR_TOKEN=") for a in args))
        self.assertFalse(any("sonar.token" in a or "sonar.login" in a for a in args))
        self.assertIn("ENV_TOKEN=match", run_call)

    def test_env_file_if_used_is_0600_and_removed(self) -> None:
        self._run()
        paths = self.logdir / "envfile.path"
        if not paths.exists():
            self.skipTest("launcher uses bare -e SONAR_TOKEN (no env-file)")
        for mode in (self.logdir / "envfile.mode").read_text().split():
            self.assertEqual(mode, "600")
        for p in paths.read_text().split():
            self.assertFalse(os.path.exists(p), f"env-file not cleaned up: {p}")


if __name__ == "__main__":
    unittest.main()
