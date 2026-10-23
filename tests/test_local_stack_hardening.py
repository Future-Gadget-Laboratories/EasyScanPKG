"""Tests for local-stack hardening: sg docker fallback, server identity,
non-destructive stop, hostname/network self-heal, cxx version source and
rollback, accurate checks, and secrets kept out of argv."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import docker_util
import easyscan_check
import local_server
import sonar_cxx_plugin


def _cp(rc: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["docker"], rc, out, err)


class DockerGroupTests(unittest.TestCase):
    def _group(self, members: list[str]) -> SimpleNamespace:
        return SimpleNamespace(gr_gid=999, gr_mem=members)

    def test_configured_member_with_stale_session(self) -> None:
        user = SimpleNamespace(pw_name="alice", pw_gid=1000)
        with patch.object(docker_util.grp, "getgrnam", return_value=self._group(["alice"])), \
                patch.object(docker_util.os, "getgroups", return_value=[1000]), \
                patch.object(docker_util.pwd, "getpwuid", return_value=user):
            self.assertTrue(docker_util._in_docker_group())

    def test_not_a_member(self) -> None:
        user = SimpleNamespace(pw_name="bob", pw_gid=1000)
        with patch.object(docker_util.grp, "getgrnam", return_value=self._group(["alice"])), \
                patch.object(docker_util.os, "getgroups", return_value=[1000]), \
                patch.object(docker_util.pwd, "getpwuid", return_value=user):
            self.assertFalse(docker_util._in_docker_group())

    def test_run_docker_retries_via_sg_on_permission_denied(self) -> None:
        denied = _cp(1, err="permission denied while trying to connect to the docker API at unix:///var/run/docker.sock")
        calls = []

        def fake_run(cmd, **_kw):
            calls.append(cmd)
            return denied if len(calls) == 1 else _cp(0, out="ok")

        with patch.object(docker_util.subprocess, "run", side_effect=fake_run), \
                patch.object(docker_util, "_in_docker_group", return_value=True), \
                patch.object(docker_util.shutil, "which", return_value="/usr/bin/sg"):
            result = docker_util.run_docker(["image", "inspect", "x"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls[1][:3], ["sg", "docker", "-c"])
        self.assertIn("docker image inspect x", calls[1][3])

    def test_run_docker_no_sg_retry_for_ordinary_failure(self) -> None:
        with patch.object(docker_util.subprocess, "run", return_value=_cp(1, err="No such image: x")) as run, \
                patch.object(docker_util, "_in_docker_group", return_value=True), \
                patch.object(docker_util.shutil, "which", return_value="/usr/bin/sg"):
            docker_util.run_docker(["image", "inspect", "x"])
        self.assertEqual(run.call_count, 1)


class ServerIdentityTests(unittest.TestCase):
    def _patch(self, *, host, running=True, published=True, inner=None, cversion=""):
        return [
            patch.object(local_server, "_http_system_status", return_value=host),
            patch.object(local_server, "is_running", return_value=running),
            patch.object(local_server, "_container_publishes_port", return_value=published),
            patch.object(local_server, "_container_system_status", return_value=inner),
            patch.object(local_server, "container_server_version", return_value=cversion),
        ]

    def _ident(self, **kw):
        patches = self._patch(**kw)
        for p in patches:
            p.start()
        try:
            return local_server.server_identity()
        finally:
            for p in patches:
                p.stop()

    def test_foreign_when_container_stopped_but_port_answers(self) -> None:
        ident = self._ident(host={"id": "A", "version": "9.9.8", "status": "UP"}, running=False)
        self.assertEqual(ident.owner, "foreign")
        self.assertIn("9.9.8", ident.detail)

    def test_foreign_when_container_has_no_port_binding(self) -> None:
        ident = self._ident(host={"id": "A", "version": "9.9.8"}, published=False)
        self.assertEqual(ident.owner, "foreign")

    def test_sft_when_ids_match(self) -> None:
        ident = self._ident(
            host={"id": "B", "version": "26.10.0"},
            inner={"id": "B", "version": "26.10.0"},
        )
        self.assertEqual(ident.owner, "sft")
        self.assertEqual(ident.container_version, "26.10.0")

    def test_foreign_when_ids_differ(self) -> None:
        ident = self._ident(
            host={"id": "A", "version": "9.9.8"},
            inner={"id": "B", "version": "26.10.0"},
        )
        self.assertEqual(ident.owner, "foreign")

    def test_version_fallback_without_curl(self) -> None:
        ident = self._ident(host={"id": "B", "version": "26.10.0"}, cversion="26.10.0")
        self.assertEqual(ident.owner, "sft")

    def test_none_and_starting(self) -> None:
        self.assertEqual(self._ident(host=None, running=False).owner, "none")
        self.assertEqual(self._ident(host=None, running=True).owner, "starting")

    def test_app_jar_version_parse(self) -> None:
        with patch.object(local_server, "run_docker", return_value=_cp(0, out="a.jar\nsonar-application-26.10.0.133078.jar\n")):
            self.assertEqual(local_server.container_server_version(), "26.10.0.133078")


class StartStopTests(unittest.TestCase):
    def test_start_refuses_foreign_server(self) -> None:
        foreign = local_server.ServerIdentity("foreign", "port 9000 is someone else")
        with patch.object(local_server, "server_identity", return_value=foreign), \
                patch.object(local_server, "_run_compose") as compose, \
                self.assertRaises(RuntimeError):
            local_server.start()
        compose.assert_not_called()

    def test_ensure_token_never_bootstraps_foreign_server(self) -> None:
        foreign = local_server.ServerIdentity("foreign", "other server")
        with patch.object(local_server, "read_env_file", return_value={}), \
                patch.object(local_server, "is_running", return_value=True), \
                patch.object(local_server, "server_identity", return_value=foreign), \
                patch.object(local_server, "_ensure_admin_password") as admin, \
                self.assertRaises(RuntimeError):
            local_server.ensure_token()
        admin.assert_not_called()

    def test_stop_keeps_containers_by_default(self) -> None:
        with patch.object(local_server, "_run_compose", return_value=_cp(0)) as compose, \
                patch.object(local_server, "status"):
            local_server.stop()
            compose.assert_called_with("stop")
            local_server.stop(remove=True)
            compose.assert_called_with("down")

    def test_native_fallback_stop_does_not_remove(self) -> None:
        with patch.object(local_server, "_compose_cmd", side_effect=RuntimeError("no compose")), \
                patch.object(local_server, "_native_stop", return_value=_cp(0)) as nstop, \
                patch.object(local_server, "_native_down") as ndown:
            local_server._run_compose("stop")
        nstop.assert_called_once()
        ndown.assert_not_called()

    def test_start_recovers_hostname_failure(self) -> None:
        sft = local_server.ServerIdentity("sft", "ok")
        waits = iter([False, True])
        with patch.object(local_server, "server_identity", return_value=sft), \
                patch.object(local_server, "_run_compose", return_value=_cp(0)), \
                patch.object(local_server, "_repair_network", side_effect=[False, True]) as repair, \
                patch.object(local_server, "run_docker", return_value=_cp(0, err="java.net.UnknownHostException: abc")), \
                patch.object(local_server, "wait_for_system_up", side_effect=lambda *a, **k: next(waits)), \
                patch.object(local_server, "pull_aux_images", return_value=[]), \
                patch.dict("os.environ", {"SFT_INSTALL_SONAR_CXX": "0"}), \
                patch.object(local_server, "status"):
            local_server.start()
        self.assertEqual(repair.call_count, 2)


class HostnameAndNetworkTests(unittest.TestCase):
    def test_compose_sets_resolvable_hostname(self) -> None:
        text = (ROOT / "docker" / "docker-compose.sonar-local.yml").read_text(encoding="utf-8")
        self.assertIn("hostname: sft-sonarqube", text)
        self.assertIn('"sft-sonarqube:127.0.0.1"', text)

    def test_native_run_sets_hostname_and_host_entry(self) -> None:
        seen: dict[str, list[str]] = {}
        with patch.object(local_server, "_ensure_network"), \
                patch.object(local_server, "_ensure_volumes"), \
                patch.object(local_server, "_wait_container_healthy"), \
                patch.object(local_server, "_ensure_container", side_effect=lambda n, a: seen.__setitem__(n, a)):
            local_server._native_up()
        args = seen[local_server.SQ_CONTAINER]
        self.assertIn("--hostname", args)
        self.assertIn(f"{local_server.SQ_HOSTNAME}:127.0.0.1", args)

    def test_repair_network_reconnects_and_restarts(self) -> None:
        calls = []

        def fake(args, **_kw):
            calls.append(args)
            if args[:2] == ["inspect", "--type"] and "-f" in args:
                name = args[-1]
                nets = {} if name == local_server.SQ_CONTAINER else {"docker_default": {}}
                return _cp(0, out=json.dumps(nets))
            return _cp(0)

        with patch.object(local_server, "run_docker", side_effect=fake):
            self.assertTrue(local_server._repair_network())
        self.assertIn(
            ["network", "connect", "--alias", "sonarqube", "--alias", "sft-sonarqube",
             "docker_default", "sft-sonarqube"],
            calls,
        )
        self.assertIn(["restart", "sft-sonarqube"], calls)

    def test_recover_disables_crashing_cxx_plugin(self) -> None:
        logs = _cp(0, out="ERROR ...\n\tat org.sonar.cxx.squidbridge.rules.CxxRuleRepository.define(X.java:1)\n")
        with patch.object(local_server, "run_docker", return_value=logs), \
                patch.object(local_server, "_disable_cxx_plugins_in_volume", return_value=True) as disable:
            self.assertIn("sonar-cxx", local_server._recover_failed_start() or "")
        disable.assert_called_once()


class CxxPluginTests(unittest.TestCase):
    def test_refuses_install_against_foreign_server(self) -> None:
        foreign = local_server.ServerIdentity("foreign", "9.9.8 answers", host_version="9.9.8")
        with patch.object(local_server, "is_running", return_value=True), \
                patch.object(local_server, "server_identity", return_value=foreign), \
                patch.object(sonar_cxx_plugin, "resolve_plugin_url") as resolve:
            result = sonar_cxx_plugin.ensure_cxx_plugin(force=True)
        self.assertFalse(result.ok)
        resolve.assert_not_called()

    def test_uses_container_version_not_http(self) -> None:
        sft = local_server.ServerIdentity("sft", "ok", host_version="26.10.0.1", container_version="26.10.0.1")
        with patch.object(local_server, "is_running", return_value=True), \
                patch.object(local_server, "server_identity", return_value=sft), \
                patch.object(sonar_cxx_plugin, "fetch_server_version", return_value="9.9.8") as http, \
                patch.object(sonar_cxx_plugin, "resolve_plugin_url", side_effect=ValueError("stop")) as resolve:
            sonar_cxx_plugin.ensure_cxx_plugin(force=True)
        resolve.assert_called_once_with("26.10.0.1")
        http.assert_not_called()

    def test_rolls_back_when_server_does_not_come_up(self) -> None:
        sft = local_server.ServerIdentity("sft", "ok", container_version="26.10.0.1")
        with patch.object(local_server, "is_running", return_value=True), \
                patch.object(local_server, "server_identity", return_value=sft), \
                patch.object(sonar_cxx_plugin, "_plugin_already_installed", return_value=False), \
                patch.object(sonar_cxx_plugin, "language_has_cxx", return_value=False), \
                patch.object(sonar_cxx_plugin, "download_plugin_jar", return_value=Path("/tmp/sonar-cxx-plugin-2.3.1.1656.jar")), \
                patch.object(sonar_cxx_plugin, "_remove_old_cxx_plugins"), \
                patch.object(sonar_cxx_plugin, "_docker_cp_into_plugins"), \
                patch.object(sonar_cxx_plugin, "_restart_sonarqube", side_effect=RuntimeError("not UP")), \
                patch.object(sonar_cxx_plugin, "_rollback_plugin", return_value=True) as rollback:
            result = sonar_cxx_plugin.ensure_cxx_plugin(force=True)
        self.assertFalse(result.ok)
        self.assertIn("plugin disabled", result.detail)
        rollback.assert_called_once()


class CheckAccuracyTests(unittest.TestCase):
    def test_images_checked_through_run_docker(self) -> None:
        report = easyscan_check.CheckReport(ok=True)
        with patch.object(easyscan_check.shutil, "which", return_value="/usr/bin/docker"), \
                patch.object(easyscan_check, "run_docker", return_value=_cp(0)) as rd:
            easyscan_check.check_images(report, offline=False, pull=False)
        self.assertTrue(report.items[-1].ok)
        self.assertEqual(rd.call_args_list[0].args[0][:2], ["image", "inspect"])

    def test_missing_image_hints_pull(self) -> None:
        report = easyscan_check.CheckReport(ok=True)
        with patch.object(easyscan_check.shutil, "which", return_value="/usr/bin/docker"), \
                patch.object(easyscan_check, "run_docker", return_value=_cp(1, err="No such image")):
            easyscan_check.check_images(report, offline=False, pull=False)
        self.assertFalse(report.ok)
        self.assertIn("--pull-images", report.items[-1].detail)

    def _up_response(self, version: str) -> MagicMock:
        resp = MagicMock()
        resp.__enter__.return_value.read.return_value = json.dumps(
            {"status": "UP", "version": version}
        ).encode()
        return resp

    def test_local_server_fails_for_foreign_server(self) -> None:
        report = easyscan_check.CheckReport(ok=True)
        with patch.object(easyscan_check, "safe_urlopen", return_value=self._up_response("9.9.8")), \
                patch.object(easyscan_check, "_server_owner_detail", return_value=("foreign", "9.9.8 holds 9000")):
            easyscan_check.check_local_server(report, require_local=True)
        self.assertFalse(report.ok)
        self.assertIn("wrong server", report.items[-1].detail)

    def test_local_server_ok_for_own_server(self) -> None:
        report = easyscan_check.CheckReport(ok=True)
        with patch.object(easyscan_check, "safe_urlopen", return_value=self._up_response("26.10.0")), \
                patch.object(easyscan_check, "_server_owner_detail", return_value=("sft", "sft-sonarqube answers")):
            easyscan_check.check_local_server(report, require_local=True)
        self.assertTrue(report.ok)


class SecretsNotInArgvTests(unittest.TestCase):
    def test_sonar_scan_passes_token_by_name(self) -> None:
        text = (ROOT / "bin" / "sonar-scan").read_text(encoding="utf-8")
        self.assertNotIn('-e SONAR_TOKEN="', text)
        self.assertNotIn("-e SONAR_TOKEN=$", text)
        self.assertIn("  -e SONAR_TOKEN \\\n", text)
        self.assertIn("_docker run --rm", text)

    def test_sonar_desktop_does_not_use_env_assignment_for_token(self) -> None:
        text = (ROOT / "bin" / "sonar-desktop").read_text(encoding="utf-8")
        self.assertNotIn('      SONARQUBE_TOKEN="${SONARQUBE_TOKEN:-}" \\', text)

    def test_sg_preserves_bare_env_flag(self) -> None:
        """The sg wrapper keeps `-e SONAR_TOKEN` bare: no value in the command."""
        quoted = " ".join(docker_util._shell_quote(["docker", "run", "-e", "SONAR_TOKEN", "img"]))
        self.assertNotIn("=", quoted)

    def test_sonar_local_up_hides_password_by_default(self) -> None:
        text = (ROOT / "bin" / "sonar-local-up").read_text(encoding="utf-8")
        self.assertIn("--show-password", text)
        self.assertIn('login["password"] if args.show_password else None', text)


if __name__ == "__main__":
    unittest.main()
