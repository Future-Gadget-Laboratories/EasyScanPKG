"""PR 10 behavioral regressions; no Docker or network."""
import contextlib
import copy
import json
import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import docker_util
import env_write
import local_server
import sonar_cxx_plugin
import sonar_lease
import sonar_runtime


class ReviewTests(unittest.TestCase):
    def test_shell_literals(self):
        args = ["docker", "a;echo", chr(96) + "id" + chr(96), "x|cat", "x>out", "*", "(x)", "", "a'b"]
        command = " ".join(docker_util._shell_quote(args))
        self.assertEqual(shlex.split(command), args)
        result = subprocess.run(["sh", "-c", "printf '%s\\n' " + command],
                                check=True, text=True, capture_output=True)
        self.assertEqual(result.stdout, "\n".join(args) + "\n")

    def test_cached_token_not_sent_to_foreign_server(self):
        with patch.object(local_server, "read_env_file", return_value={
            "SONARQUBE_URL": local_server.DEFAULT_LOCAL_URL, "SONARQUBE_TOKEN": "secret"
        }), patch.object(local_server, "is_running", return_value=True), patch.object(
            local_server, "require_own_server", side_effect=RuntimeError("foreign")
        ), patch.object(local_server, "check_server") as check:
            with self.assertRaises(RuntimeError):
                local_server.ensure_token()
            check.assert_not_called()

    def test_cxx_unknown_identity_refuses_install(self):
        with patch.object(local_server, "is_running", return_value=True), patch.object(
            local_server, "server_identity",
            return_value=local_server.ServerIdentity("unknown", "no IDs")
        ), patch.object(sonar_cxx_plugin, "resolve_plugin_url") as resolve:
            self.assertFalse(sonar_cxx_plugin.ensure_cxx_plugin(force=True).ok)
            resolve.assert_not_called()

    def test_docker_failure_is_not_stopped(self):
        for err in ("permission denied", "Cannot connect to Docker daemon"):
            failed = subprocess.CompletedProcess([], 1, "", err)
            with patch.object(local_server, "run_docker", return_value=failed), \
                    self.assertRaises(RuntimeError):
                local_server.lease_running()

    def test_stop_does_not_resolve_invalid_memory_settings(self):
        with patch.object(local_server, "_compose_cmd", return_value=["docker", "compose", "stop"]), patch.object(
            local_server, "resolve_memory", side_effect=ValueError("bad memory")
        ), patch.object(local_server, "run_docker",
                        return_value=subprocess.CompletedProcess([], 0, "", "")):
            self.assertEqual(local_server._run_compose("stop").returncode, 0)

    def test_native_configuration_changes_recreate(self):
        args = ["--restart", "no", *sonar_runtime.native_run_args(
            sonar_runtime.PROFILES["low"], settings={}), "sonarqube:community"]
        spec = {
            "HostConfig": {"RestartPolicy": {"Name": "no"},
                           "Memory": 2560 * 1024**2, "MemorySwap": 2560 * 1024**2},
            "Config": {"Env": [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-e"]},
        }
        self.assertTrue(local_server._native_configuration_matches(spec, args))
        for key in ("Memory", "MemorySwap"):
            changed = copy.deepcopy(spec)
            changed["HostConfig"][key] = 0
            self.assertFalse(local_server._native_configuration_matches(changed, args))
        spec["Config"]["Env"] = []
        ok = subprocess.CompletedProcess([], 0, "", "")
        inspect = subprocess.CompletedProcess([], 0, json.dumps([spec]), "")
        with patch.object(local_server, "run_docker", side_effect=[inspect, ok, ok]) as run:
            local_server._ensure_container(local_server.SQ_CONTAINER, args)
            self.assertEqual(run.call_args_list[1].args[0], ["rm", "-f", local_server.SQ_CONTAINER])
            self.assertEqual(run.call_args_list[2].args[0][:2], ["run", "-d"])

    def test_reuse_does_not_recreate_and_bootstraps_token(self):
        with patch.object(local_server, "lease_running", return_value=True), patch.object(
            local_server, "wait_for_system_up", return_value=True
        ), patch.object(local_server, "require_own_server") as own, patch.object(
            local_server, "start"
        ) as start, patch.object(local_server, "ensure_token") as token:
            sonar_lease._prepare_local_server()
            start.assert_not_called()
            own.assert_called_once()
            token.assert_called_once()

    def test_ce_unknown_or_busy_never_means_idle(self):
        for status in (None, {}, {"pending": 1, "inProgress": 0}, {"pending": 0, "inProgress": 1}):
            with patch("env_write.read_env_file", return_value={}), patch.object(
                sonar_lease, "_ce_activity_status", return_value=(status, True)
            ), patch.object(sonar_lease.time, "monotonic", side_effect=[0, 0, 2]), patch.object(
                sonar_lease.time, "sleep"
            ), self.assertRaises(TimeoutError):
                sonar_lease.wait_ce_idle(timeout_s=1)
        with patch("env_write.read_env_file", return_value={}), patch.object(
            sonar_lease, "_ce_activity_status", return_value=({"pending": 0, "inProgress": 0}, False)
        ):
            sonar_lease.wait_ce_idle()

    def test_private_credentials_use_atomic_publication(self):
        stream = MagicMock()
        stream.name = "/virtual/private-temp"
        stream.__enter__.return_value = stream
        with patch.object(Path, "mkdir"), patch.object(Path, "unlink"), patch.object(
            env_write.tempfile, "NamedTemporaryFile", return_value=stream
        ), patch.object(env_write.os, "replace") as replace:
            env_write.write_private(Path("/virtual/credential"), "secret")
            stream.write.assert_called_once_with("secret")
            replace.assert_called_once_with(Path(stream.name), Path("/virtual/credential"))

    def test_project_key_is_data(self):
        text = (ROOT / "bin" / "sonar-scan").read_text()
        snippets = [part.split("\n", 1)[1] for part in text.split("python3 - <<'PY'")[1:]]
        self.assertEqual(len(snippets), 2)
        key = 'x"; raise RuntimeError("injected"); #'
        for snippet in snippets:
            prefix = snippet.split("\nPY", 1)[0].split("auth =", 1)[0]
            env = dict(os.environ, SONARQUBE_URL="http://localhost:9000",
                       SONARQUBE_TOKEN="dummy", SFT_PROJECT_KEY=key)
            done = subprocess.run(
                [sys.executable, "-c", prefix + "\nprint(key, end='')\n"],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertEqual(done.stdout, key)


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.state = {"leases": {}, "started_by_lease": False}
        self.locked = False

        @contextlib.contextmanager
        def lock():
            self.assertFalse(self.locked)
            self.locked = True
            try:
                yield
            finally:
                self.locked = False

        def save(data):
            self.assertTrue(self.locked)
            self.state = copy.deepcopy(data)

        for item in (
            patch.object(sonar_lease, "_locked", lock),
            patch.object(sonar_lease, "_load", side_effect=lambda: copy.deepcopy(self.state)),
            patch.object(sonar_lease, "_save", side_effect=save),
        ):
            item.start()
            self.addCleanup(item.stop)
        self.ops = sonar_lease.LeaseOps(MagicMock(return_value=False), MagicMock(),
                                        MagicMock(), MagicMock())

    def test_join_does_not_reconfigure_under_another_lease(self):
        sonar_lease.acquire("a", ops=self.ops)
        self.ops.is_running.return_value = True
        sonar_lease.acquire("b", ops=self.ops)
        self.assertEqual(self.ops.start.call_count, 1)

    def test_manual_stop_holds_lock_and_refuses_active_users(self):
        sonar_lease.acquire("a", ops=self.ops)
        with self.assertRaises(RuntimeError), sonar_lease.stop_guard():
            self.fail("active lease ignored")
        with sonar_lease.stop_guard(force=True):
            self.assertTrue(self.locked)

    def test_partial_start_reaped_even_if_only_database_remains(self):
        self.ops.start.side_effect = RuntimeError("partial start")
        with self.assertRaises(RuntimeError):
            sonar_lease.acquire("a", ops=self.ops)
        self.assertTrue(self.state["started_by_lease"])
        sonar_lease.stop_if_idle(ops=self.ops)
        self.ops.stop.assert_called_once()

    def test_ce_failure_persists_release_and_retains_ownership(self):
        lid = sonar_lease.acquire("a", ops=self.ops)
        self.ops.is_running.return_value = True
        self.ops.wait_ce_idle.side_effect = TimeoutError("busy")
        with self.assertRaises(TimeoutError):
            sonar_lease.release(lid, ops=self.ops)
        self.assertEqual(self.state["leases"], {})
        self.assertTrue(self.state["started_by_lease"])
        self.ops.stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
