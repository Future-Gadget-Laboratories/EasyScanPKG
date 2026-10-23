"""Tests for the memory/autostart knobs, on-demand leases, and review fixes."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import easyscan_check
import local_server
import sonar_lease
import sonar_runtime
from server_health import AuthStatus


def _clean_env():
    keys = ("SFT_SONAR_MEM_PROFILE", "SFT_SONAR_MEM_LIMIT", "SFT_SONAR_AUTOSTART", "SFT_SONAR_ON_DEMAND")
    return patch.dict(os.environ, {k: "" for k in keys})


class MemoryKnobTests(unittest.TestCase):
    def test_parse_size(self) -> None:
        self.assertEqual(sonar_runtime.parse_size_mb("3g"), 3072)
        self.assertEqual(sonar_runtime.parse_size_mb("2560m"), 2560)
        self.assertEqual(sonar_runtime.parse_size_mb("2560"), 2560)
        self.assertEqual(sonar_runtime.parse_size_mb("2.5GiB"), 2560)
        with self.assertRaises(ValueError):
            sonar_runtime.parse_size_mb("lots")

    def test_default_is_low_profile(self) -> None:
        with _clean_env():
            mem = sonar_runtime.resolve_memory(settings={})
        self.assertEqual(mem, sonar_runtime.PROFILES["low"])
        self.assertLessEqual(mem.limit_mb, 3072)

    def test_every_profile_fits_heaps_inside_cap(self) -> None:
        for name, mem in sonar_runtime.PROFILES.items():
            with self.subTest(name):
                self.assertLessEqual(mem.heap_mb + sonar_runtime.JVM_OVERHEAD_MB, mem.limit_mb)

    def test_profile_from_settings_file_and_env_precedence(self) -> None:
        with _clean_env():
            mem = sonar_runtime.resolve_memory(settings={"SFT_SONAR_MEM_PROFILE": "normal"})
            self.assertEqual(mem, sonar_runtime.PROFILES["normal"])
            with patch.dict(os.environ, {"SFT_SONAR_MEM_PROFILE": "high"}):
                mem = sonar_runtime.resolve_memory(settings={"SFT_SONAR_MEM_PROFILE": "normal"})
            self.assertEqual(mem, sonar_runtime.PROFILES["high"])
            # explicit argument (sonar-local-up flag) beats both
            self.assertEqual(sonar_runtime.resolve_memory("low", settings={}).limit_mb, 2560)

    def test_limit_keeps_profile_heaps_when_they_fit(self) -> None:
        with _clean_env():
            mem = sonar_runtime.resolve_memory("low", "4g", settings={})
        self.assertEqual(mem.limit_mb, 4096)
        self.assertEqual(mem.ce_mb, sonar_runtime.PROFILES["low"].ce_mb)

    def test_limit_refits_heaps_when_profile_does_not_fit(self) -> None:
        with _clean_env():
            mem = sonar_runtime.resolve_memory("high", "3g", settings={})
        self.assertEqual(mem.limit_mb, 3072)
        self.assertLessEqual(mem.heap_mb + sonar_runtime.JVM_OVERHEAD_MB, 3072)
        for heap in (mem.web_mb, mem.ce_mb, mem.search_mb):
            self.assertGreaterEqual(heap, 256)
            self.assertEqual(heap % 64, 0)

    def test_limit_below_minimum_and_bad_profile_rejected(self) -> None:
        with _clean_env():
            with self.assertRaises(ValueError):
                sonar_runtime.resolve_memory("low", "1g", settings={})
            with self.assertRaises(ValueError):
                sonar_runtime.resolve_memory("huge", settings={})

    def test_compose_env_and_native_args(self) -> None:
        mem = sonar_runtime.PROFILES["low"]
        with _clean_env():
            env = sonar_runtime.compose_env(mem, settings={})
            args = sonar_runtime.native_run_args(mem, settings={})
        self.assertEqual(env["SFT_SQ_MEM_LIMIT"], "2560m")
        self.assertEqual(env["SFT_SQ_MEMSWAP_LIMIT"], env["SFT_SQ_MEM_LIMIT"])
        self.assertEqual(env["SFT_SQ_SEARCH_JAVAOPTS"], "-Xmx512m -Xms512m")
        self.assertEqual(env["SFT_SQ_WEB_JAVAOPTS"], "-Xmx512m -Xms128m")
        self.assertEqual(env["SFT_SQ_RESTART"], "unless-stopped")
        self.assertIn("--memory-swap", args)
        self.assertIn("SONAR_CE_JAVAOPTS=-Xmx640m -Xms128m", args)

    def test_compose_defaults_match_low_profile(self) -> None:
        text = (ROOT / "docker" / "docker-compose.sonar-local.yml").read_text(encoding="utf-8")
        with _clean_env():
            env = sonar_runtime.compose_env(sonar_runtime.PROFILES["low"], settings={})
        for key, value in env.items():
            with self.subTest(key):
                match = re.search(r"\$\{" + key + r":-([^}]*)\}", text)
                self.assertIsNotNone(match, key)
                self.assertEqual(match.group(1), value)
        self.assertEqual(text.count("${SFT_SQ_RESTART:-unless-stopped}"), 2)

    def test_autostart_switch(self) -> None:
        with _clean_env():
            self.assertEqual(sonar_runtime.restart_policy(settings={}), "unless-stopped")
            self.assertEqual(sonar_runtime.restart_policy(settings={"SFT_SONAR_AUTOSTART": "0"}), "no")
            with patch.dict(os.environ, {"SFT_SONAR_AUTOSTART": "0"}):
                self.assertEqual(sonar_runtime.restart_policy(settings={}), "no")

    def test_on_demand_default_off(self) -> None:
        with _clean_env():
            self.assertFalse(sonar_runtime.on_demand_enabled(settings={}))
            self.assertTrue(sonar_runtime.on_demand_enabled(settings={"SFT_SONAR_ON_DEMAND": "1"}))

    def test_settings_file_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sonar-runtime.env"
            path.write_text("# comment\nSFT_SONAR_MEM_PROFILE=normal\nSFT_SONAR_AUTOSTART='0'\n")
            self.assertEqual(
                sonar_runtime.read_settings(path),
                {"SFT_SONAR_MEM_PROFILE": "normal", "SFT_SONAR_AUTOSTART": "0"},
            )

    def test_scanner_opts_follow_profile(self) -> None:
        with _clean_env():
            self.assertEqual(sonar_runtime.scanner_java_opts(settings={}), "-Xmx1024m")
            self.assertEqual(
                sonar_runtime.scanner_java_opts(settings={"SFT_SONAR_MEM_PROFILE": "high"}), "-Xmx3072m"
            )

    def test_run_compose_passes_memory_env(self) -> None:
        captured = {}

        def fake_run_docker(args, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(args, 0, "", "")

        with _clean_env(), \
                patch.object(local_server, "_compose_cmd", return_value=["docker", "compose", "up", "-d"]), \
                patch.object(local_server.shutil, "which", return_value="docker"), \
                patch.object(local_server, "run_docker", side_effect=fake_run_docker), \
                patch.object(sonar_runtime, "read_settings", return_value={}):
            local_server._run_compose("up", "-d")
        self.assertEqual(captured["env"]["SFT_SQ_MEM_LIMIT"], "2560m")
        self.assertEqual(captured["env"]["SFT_SQ_RESTART"], "unless-stopped")


class FakeOps:
    def __init__(self, running: bool) -> None:
        self.running = running
        self.calls: list[str] = []

    def ops(self) -> sonar_lease.LeaseOps:
        return sonar_lease.LeaseOps(
            is_running=lambda: self.running,
            start=self._start,
            stop=self._stop,
            wait_ce_idle=lambda: self.calls.append("ce_idle"),
        )

    def _start(self) -> None:
        self.calls.append("start")
        self.running = True

    def _stop(self) -> None:
        self.calls.append("stop")
        self.running = False


class _LeaseDirMixin:
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.patches = [
            patch.object(sonar_lease, "LEASE_DIR", base),
            patch.object(sonar_lease, "LOCK_FILE", base / "on-demand.lock"),
            patch.object(sonar_lease, "STATE_FILE", base / "on-demand.json"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _dead_pid(self) -> int:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc.pid


class LeaseTests(_LeaseDirMixin, unittest.TestCase):
    def test_starts_when_down_and_last_out_stops(self) -> None:
        fake = FakeOps(running=False)
        lid = sonar_lease.acquire("scan", ops=fake.ops())
        self.assertTrue(fake.running)
        msg = sonar_lease.release(lid, ops=fake.ops())
        self.assertIn("stopped", msg)
        self.assertEqual(fake.calls, ["start", "ce_idle", "stop"])
        self.assertFalse(fake.running)

    def test_server_up_before_first_lease_is_left_alone(self) -> None:
        fake = FakeOps(running=True)
        lid = sonar_lease.acquire("scan", ops=fake.ops())
        msg = sonar_lease.release(lid, ops=fake.ops())
        self.assertIn("up before the first lease", msg)
        self.assertNotIn("stop", fake.calls)
        self.assertTrue(fake.running)

    def test_no_stop_under_another_lease(self) -> None:
        fake = FakeOps(running=False)
        first = sonar_lease.acquire("scan-a", ops=fake.ops())
        second = sonar_lease.acquire("scan-b", ops=fake.ops())
        self.assertIn("1 lease(s) left", sonar_lease.release(first, ops=fake.ops()))
        self.assertTrue(fake.running)
        self.assertIn("stopped", sonar_lease.release(second, ops=fake.ops()))
        self.assertEqual(fake.calls.count("stop"), 1)

    def test_stale_lease_from_crashed_scan_is_pruned(self) -> None:
        fake = FakeOps(running=False)
        crashed = sonar_lease.acquire("crashed", ops=fake.ops(), pid=self._dead_pid())
        mine = sonar_lease.acquire("mine", ops=fake.ops())
        self.assertIn("stopped", sonar_lease.release(mine, ops=fake.ops()))
        state = json.loads(sonar_lease.STATE_FILE.read_text())
        self.assertNotIn(crashed, state["leases"])

    def test_pid_reuse_is_stale(self) -> None:
        lease = {"pid": os.getpid(), "proc_start": "not-the-real-start", "host": sonar_lease.socket.gethostname()}
        self.assertTrue(sonar_lease.lease_is_stale(lease))
        lease["proc_start"] = sonar_lease._proc_start_time(os.getpid())
        self.assertFalse(sonar_lease.lease_is_stale(lease))

    def test_if_idle_reaps_after_last_holder_crashed(self) -> None:
        fake = FakeOps(running=False)
        sonar_lease.acquire("crashed", ops=fake.ops(), pid=self._dead_pid())
        self.assertIn("stopped", sonar_lease.stop_if_idle(ops=fake.ops()))
        self.assertFalse(fake.running)

    def test_if_idle_leaves_foreign_started_server(self) -> None:
        fake = FakeOps(running=True)
        self.assertIn("kept running", sonar_lease.stop_if_idle(ops=fake.ops()))
        self.assertEqual(fake.calls, [])

    def test_lease_started_server_survives_crash_then_new_user(self) -> None:
        fake = FakeOps(running=False)
        sonar_lease.acquire("crashed", ops=fake.ops(), pid=self._dead_pid())
        # server still up from the crashed run; a new user arrives and leaves
        lid = sonar_lease.acquire("next", ops=fake.ops())
        self.assertIn("stopped", sonar_lease.release(lid, ops=fake.ops()))

    def test_externally_stopped_server_is_restarted_and_owned(self) -> None:
        fake = FakeOps(running=True)
        first = sonar_lease.acquire("a", ops=fake.ops())  # pre-existing: not ours
        fake.running = False  # someone stopped it under us
        second = sonar_lease.acquire("b", ops=fake.ops())
        sonar_lease.release(first, ops=fake.ops())
        self.assertIn("stopped", sonar_lease.release(second, ops=fake.ops()))

    def test_lock_is_exclusive_across_processes(self) -> None:
        code = (
            "import sys, time; sys.path.insert(0, sys.argv[1]);"
            "import sonar_lease; from pathlib import Path;"
            "sonar_lease.LEASE_DIR = Path(sys.argv[2]); sonar_lease.LOCK_FILE = Path(sys.argv[2]) / 'on-demand.lock';"
            "cm = sonar_lease._locked(); cm.__enter__(); print('locked', flush=True); time.sleep(5)"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", code, str(ROOT / "lib"), self.tmp.name],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "locked")
            with self.assertRaises(TimeoutError), sonar_lease._locked(timeout_s=0.5):
                pass
        finally:
            child.kill()
            child.wait()
            child.stdout.close()
        with sonar_lease._locked(timeout_s=2):  # released when the holder died
            pass

    def test_lease_context_disabled_is_noop(self) -> None:
        with patch.object(sonar_lease, "acquire") as acquire, \
                sonar_lease.lease("x", enabled=False) as lid:
            self.assertIsNone(lid)
        acquire.assert_not_called()

    def test_sigterm_inside_lease_still_releases(self) -> None:
        with patch.object(sonar_lease, "acquire", return_value="L1"), \
                patch.object(sonar_lease, "release", return_value="stopped") as release, \
                self.assertRaises(SystemExit) as ctx, \
                sonar_lease.lease("x", enabled=True):
            os.kill(os.getpid(), signal.SIGTERM)
        self.assertEqual(ctx.exception.code, 128 + signal.SIGTERM)
        release.assert_called_once_with("L1")
        self.assertIs(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)


def _load_script(name: str):
    path = ROOT / "bin" / name
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class OnDemandWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scan = _load_script("easyscan-scan")

    def _args(self, **kw):
        base = {"plan": False, "context": None, "local": False}
        base.update(kw)
        return MagicMock(**base)

    def test_scan_takes_lease_when_on_demand(self) -> None:
        with patch("sonar_runtime.on_demand_enabled", return_value=True), \
                patch.object(self.scan, "_local_sonar", return_value=True), \
                patch("sonar_lease.lease", return_value="LEASE") as lease:
            cm = self.scan._on_demand_lease(self._args(), only=None, disable=[])
        self.assertEqual(cm, "LEASE")
        lease.assert_called_once_with("easyscan-scan")

    def test_scan_skips_lease_when_sonar_not_used_or_off(self) -> None:
        cases = [
            ({"only": ["ruff"], "disable": []}, True),
            ({"only": None, "disable": ["ruff,sonar"]}, True),
            ({"only": None, "disable": []}, False),
        ]
        for kwargs, on in cases:
            with self.subTest(kwargs=kwargs, on=on), \
                    patch("sonar_runtime.on_demand_enabled", return_value=on), \
                    patch.object(self.scan, "_local_sonar", return_value=True), \
                    patch("sonar_lease.lease") as lease:
                self.scan._on_demand_lease(self._args(), **kwargs)
                lease.assert_not_called()

    def test_scan_skips_lease_for_plan_and_remote(self) -> None:
        with patch("sonar_runtime.on_demand_enabled", return_value=True), \
                patch("sonar_lease.lease") as lease:
            self.scan._on_demand_lease(self._args(plan=True), only=None, disable=[])
            with patch.object(self.scan, "_local_sonar", return_value=False):
                self.scan._on_demand_lease(self._args(), only=None, disable=[])
        lease.assert_not_called()

    def test_check_wraps_require_local_in_lease(self) -> None:
        text = (ROOT / "bin" / "easyscan-check").read_text(encoding="utf-8")
        self.assertIn('with lease("easyscan-check", enabled=on_demand):', text)
        self.assertIn("args.require_local and not args.offline and on_demand_enabled()", text)

    def test_local_down_guards_active_leases(self) -> None:
        text = (ROOT / "bin" / "sonar-local-down").read_text(encoding="utf-8")
        self.assertIn("--if-idle", text)
        self.assertIn("with stop_guard(force=args.force):", text)


class ReviewFixTests(unittest.TestCase):
    def _up(self) -> MagicMock:
        resp = MagicMock()
        resp.__enter__.return_value.read.return_value = json.dumps({"status": "UP", "version": "26.10"}).encode()
        return resp

    def test_unverified_server_fails_require_local(self) -> None:
        report = easyscan_check.CheckReport(ok=True)
        with patch.object(easyscan_check, "safe_urlopen", return_value=self._up()), \
                patch.object(easyscan_check, "_server_owner_detail", return_value=("unknown", "docker unusable")):
            easyscan_check.check_local_server(report, require_local=True)
        self.assertFalse(report.ok)
        self.assertIn("not verified", report.items[-1].detail)

    def test_unverified_server_soft_without_require_local(self) -> None:
        report = easyscan_check.CheckReport(ok=True)
        with patch.object(easyscan_check, "safe_urlopen", return_value=self._up()), \
                patch.object(easyscan_check, "_server_owner_detail", return_value=("unknown", "docker unusable")):
            easyscan_check.check_local_server(report, require_local=False)
        self.assertTrue(report.ok)

    def test_unreachable_token_fails_only_when_required(self) -> None:
        health = MagicMock(status=AuthStatus.UNREACHABLE, detail="connection refused")
        with tempfile.TemporaryDirectory() as tmp, \
                patch("server_health.check_server", return_value=health):
            env = Path(tmp) / "sonar-local.env"
            env.write_text("SONARQUBE_URL=http://127.0.0.1:9000\n")
            ok, _ = easyscan_check._validate_local_token(env, require_local=True)
            self.assertFalse(ok)
            ok, _ = easyscan_check._validate_local_token(env, require_local=False)
            self.assertTrue(ok)

    def test_cached_token_still_requires_own_server(self) -> None:
        ok = MagicMock(status=AuthStatus.OK)
        env = {"SONARQUBE_URL": "http://127.0.0.1:9000", "SONARQUBE_TOKEN": "t"}
        with patch.object(local_server, "read_env_file", return_value=env), \
                patch.object(local_server, "check_server", return_value=ok), \
                patch.object(local_server, "is_running", return_value=True), \
                patch.object(local_server, "require_own_server", side_effect=RuntimeError("foreign")), \
                self.assertRaises(RuntimeError):
            local_server.ensure_token()

    def test_cached_token_remote_url_skips_identity(self) -> None:
        ok = MagicMock(status=AuthStatus.OK)
        env = {"SONARQUBE_URL": "https://sonar.example.com", "SONARQUBE_TOKEN": "t"}
        with patch.object(local_server, "read_env_file", return_value=env), \
                patch.object(local_server, "check_server", return_value=ok), \
                patch.object(local_server, "require_own_server") as req:
            self.assertEqual(local_server.ensure_token(), "t")
        req.assert_not_called()

    def test_sonar_scan_aborts_when_local_up_fails(self) -> None:
        text = (ROOT / "bin" / "sonar-scan").read_text(encoding="utf-8")
        self.assertNotIn('sonar-local-up" >/dev/null || true', text)
        self.assertIn('if ! "$ROOT/bin/sonar-local-up" --reuse-running >/dev/null; then', text)

    def test_sonar_scan_uses_group_database(self) -> None:
        text = (ROOT / "bin" / "sonar-scan").read_text(encoding="utf-8")
        self.assertIn("from docker_util import _in_docker_group", text)
        self.assertNotIn('id -nG "$(id -un)"', text)
        self.assertIn("  -e SONAR_SCANNER_JAVA_OPTS \\\n", text)


class CeIdleAndEdgeTests(unittest.TestCase):
    def _run(self, statuses):
        seq = iter(statuses)
        with patch("env_write.read_env_file", return_value={"SONARQUBE_TOKEN": "t"}), \
                patch.object(sonar_lease, "_ce_activity_status", side_effect=lambda *_: next(seq)), \
                patch.object(sonar_lease.time, "sleep"):
            sonar_lease.wait_ce_idle(timeout_s=60)

    def test_transient_errors_are_retried_until_queue_empty(self) -> None:
        busy = ({"pending": 1, "inProgress": 0}, False)
        self._run([(None, True), busy, (None, True), ({"pending": 0, "inProgress": 0}, False)])

    def test_permission_error_leaves_server_running(self) -> None:
        with self.assertRaises(TimeoutError):
            self._run([(None, False)])

    def test_deadline_leaves_server_running(self) -> None:
        with patch("env_write.read_env_file", return_value={}), \
                patch.object(sonar_lease, "_ce_activity_status", return_value=(None, True)), \
                patch.object(sonar_lease.time, "sleep"), \
                self.assertRaises(TimeoutError):
            sonar_lease.wait_ce_idle(timeout_s=0)

    def test_ce_status_classification(self) -> None:
        with patch("sonar_api.api", return_value=(200, {"pending": 0})):
            self.assertEqual(sonar_lease._ce_activity_status("u", "t"), ({"pending": 0}, False))
        with patch("sonar_api.api", return_value=(403, "forbidden")):
            self.assertEqual(sonar_lease._ce_activity_status("u", "t"), (None, False))
        with patch("sonar_api.api", return_value=(503, "")):
            self.assertEqual(sonar_lease._ce_activity_status("u", "t"), (None, True))
        with patch("sonar_api.api", side_effect=OSError("refused")):
            self.assertEqual(sonar_lease._ce_activity_status("u", "t"), (None, True))

    def test_release_failure_does_not_mask_caller(self) -> None:
        with patch.object(sonar_lease, "acquire", return_value="L1"), \
                patch.object(sonar_lease, "release", side_effect=TimeoutError("busy")), \
                sonar_lease.lease("x", enabled=True) as lid:
            self.assertEqual(lid, "L1")


class LeaseEdgeTests(_LeaseDirMixin, unittest.TestCase):
    def test_failed_start_keeps_ownership_for_if_idle(self) -> None:
        fake = FakeOps(running=False)
        ops = fake.ops()
        ops.start = MagicMock(side_effect=RuntimeError("foreign server on 9000"))
        with self.assertRaises(RuntimeError):
            sonar_lease.acquire("scan", ops=ops)
        state = json.loads(sonar_lease.STATE_FILE.read_text())
        self.assertTrue(state["started_by_lease"])
        self.assertEqual(state["leases"], {})
        self.assertIn("stopped", sonar_lease.stop_if_idle(ops=fake.ops()))

    def test_failed_start_keeps_ownership_of_half_started_stack(self) -> None:
        fake = FakeOps(running=False)
        ops = fake.ops()

        def half_start():
            fake.running = True
            raise RuntimeError("timed out waiting for UP")

        ops.start = half_start
        with self.assertRaises(RuntimeError):
            sonar_lease.acquire("scan", ops=ops)
        self.assertIn("stopped", sonar_lease.stop_if_idle(ops=fake.ops()))

    def test_active_leases_persists_pruning(self) -> None:
        fake = FakeOps(running=False)
        sonar_lease.acquire("crashed", ops=fake.ops(), pid=self._dead_pid())
        self.assertEqual(sonar_lease.active_leases()["leases"], {})
        self.assertEqual(json.loads(sonar_lease.STATE_FILE.read_text())["leases"], {})


if __name__ == "__main__":
    unittest.main()
