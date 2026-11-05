"""scan_summary waits for the CE task and a settled issue count (stub HTTP server)."""
from __future__ import annotations

import io
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import scan_summary  # noqa: E402

FAKE_TOKEN = "squ_FAKE_SUMMARY_TOKEN_abcdef0123456789"


class _Stub:
    def __init__(self, ce_statuses, totals):
        self.ce_statuses = list(ce_statuses)
        self.totals = list(totals)
        self.paths: list[str] = []
        self.auth_seen: set[str] = set()
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                stub.paths.append(self.path)
                stub.auth_seen.add(self.headers.get("Authorization", ""))
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                if u.path == "/api/ce/task":
                    st = stub.ce_statuses.pop(0) if len(stub.ce_statuses) > 1 else stub.ce_statuses[0]
                    body = {"task": {"id": q["id"][0], "status": st}}
                elif u.path == "/api/issues/search" and q.get("ps") == ["1"]:
                    t = stub.totals.pop(0) if len(stub.totals) > 1 else stub.totals[0]
                    body = {"total": t, "issues": []}
                elif u.path == "/api/issues/search":
                    body = {"total": stub.totals[-1], "issues": []}
                else:
                    self.send_response(404); self.end_headers(); return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ScanSummaryWaitTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        (self.ws / ".scannerwork").mkdir()
        (self.ws / ".scannerwork" / "report-task.txt").write_text(
            "projectKey=k\nceTaskId=AXtask123\nceTaskUrl=http://x/api/ce/task?id=AXtask123\n")

    def tearDown(self):
        self._tmp.cleanup()

    def _env(self, stub, **extra):
        env = {"SONARQUBE_URL": stub.url, "SONARQUBE_TOKEN": FAKE_TOKEN, "SFT_PROJECT_KEY": "k",
               "SFT_SCAN_WORKSPACE": str(self.ws), "SFT_SCAN_POLL_INTERVAL": "0.01",
               "SFT_SCAN_CE_TIMEOUT": "5", "SFT_SCAN_ISSUES_TIMEOUT": "5", "SFT_SCAN_STABLE_POLLS": "3"}
        env.update(extra)
        return env

    def test_rejects_non_http_url(self):
        with self.assertRaises(ValueError):
            scan_summary.SonarClient("file:///tmp/sonar", FAKE_TOKEN)

    def test_polling_retries_url_errors(self):
        client = Mock()
        client.get.side_effect = [urllib.error.URLError("offline"), {"task": {"status": "SUCCESS"}},
                                  urllib.error.HTTPError("http://example.com", 503, "busy", {}, None),
                                  {"total": 2}]
        self.assertEqual(scan_summary.wait_ce_task(client, "id", "k", 5, 0), ("SUCCESS", False))
        self.assertEqual(scan_summary.wait_issue_count(client, "k", 5, 0, 1), (2, False))
        self.assertEqual(client.get.call_count, 4)

    def test_polling_retries_transport_os_errors(self):
        for error in (OSError("read failed"), ssl.SSLError("TLS read failed")):
            with self.subTest(error=type(error).__name__):
                client = Mock()
                client.get.side_effect = [error, {"task": {"status": "SUCCESS"}},
                                          error, {"total": 2}]
                self.assertEqual(scan_summary.wait_ce_task(client, "id", "k", 5, 0),
                                 ("SUCCESS", False))
                self.assertEqual(scan_summary.wait_issue_count(client, "k", 5, 0, 1),
                                 (2, False))
                self.assertEqual(client.get.call_count, 4)

    def test_issue_listing_os_error_preserves_output_and_status(self):
        for unsettled, expected_rc in ((False, 0), (True, 5)):
            with self.subTest(unsettled=unsettled):
                out, err = io.StringIO(), io.StringIO()
                env = {"SONARQUBE_URL": "http://localhost", "SONARQUBE_TOKEN": FAKE_TOKEN,
                       "SFT_PROJECT_KEY": "k"}
                with patch.object(scan_summary, "wait_ce_task", return_value=("SUCCESS", False)), \
                     patch.object(scan_summary, "wait_issue_count", return_value=(2, unsettled)), \
                     patch.object(scan_summary, "print_issues", side_effect=OSError("read failed")):
                    rc = scan_summary.run(env, out=out, err=err)
                self.assertEqual(rc, expected_rc)
                self.assertIn("issue listing failed: OSError", err.getvalue())
                if unsettled:
                    self.assertIn("WARNING: issue count did not settle", err.getvalue())
                self.assertIn("UI: http://localhost/dashboard?id=k", out.getvalue())
                self.assertIn("Issues: http://localhost/project/issues?id=k&resolved=false", out.getvalue())

    def test_reads_task_id(self):
        self.assertEqual(scan_summary.read_ce_task_id(str(self.ws)), "AXtask123")

    def test_lagging_index_reports_settled_total(self):
        stub = _Stub(["PENDING", "IN_PROGRESS", "SUCCESS"], [0, 0, 7])
        self.addCleanup(stub.close)
        out, err = io.StringIO(), io.StringIO()
        rc = scan_summary.run(self._env(stub), out=out, err=err)
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("CE status: SUCCESS", out.getvalue())
        self.assertIn("total issues: 7", out.getvalue())
        self.assertNotIn("total issues: 0", out.getvalue())
        self.assertTrue(any(p.startswith("/api/ce/task?id=AXtask123") for p in stub.paths))
        # No issue totals were requested before CE finished.
        first_issue = next(i for i, p in enumerate(stub.paths) if p.startswith("/api/issues/search"))
        self.assertTrue(all(p.startswith("/api/ce/task") for p in stub.paths[:first_issue]))
        self.assertEqual(first_issue, 3)
        self.assertNotIn(FAKE_TOKEN, out.getvalue() + err.getvalue())
        self.assertFalse(any(FAKE_TOKEN in p for p in stub.paths))

    def test_ce_stuck_pending_times_out(self):
        stub = _Stub(["PENDING"], [0])
        self.addCleanup(stub.close)
        out, err = io.StringIO(), io.StringIO()
        rc = scan_summary.run(self._env(stub, SFT_SCAN_CE_TIMEOUT="0.3"), out=out, err=err)
        self.assertEqual(rc, 3)
        self.assertIn("WARNING: CE task AXtask123 did not finish", err.getvalue())
        self.assertNotIn("total issues", out.getvalue())
        self.assertFalse(any(p.startswith("/api/issues/search") for p in stub.paths))

    def test_unsettled_count_warns(self):
        stub = _Stub(["SUCCESS"], list(range(1000)))
        self.addCleanup(stub.close)
        out, err = io.StringIO(), io.StringIO()
        rc = scan_summary.run(self._env(stub, SFT_SCAN_ISSUES_TIMEOUT="0.3"), out=out, err=err)
        self.assertEqual(rc, 5)
        self.assertIn("WARNING: issue count did not settle", err.getvalue())
        self.assertIn("(unsettled)", out.getvalue())

    def test_cli_keeps_token_out_of_argv(self):
        stub = _Stub(["SUCCESS"], [2])
        self.addCleanup(stub.close)
        env = {**os.environ, **self._env(stub)}
        proc = subprocess.run([sys.executable, str(ROOT / "lib" / "scan_summary.py")],
                              env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("total issues: 2", proc.stdout)
        self.assertNotIn(FAKE_TOKEN, proc.stdout + proc.stderr)
        text = (ROOT / "bin" / "sonar-scan").read_text()
        self.assertIn('python3 "$ROOT/lib/scan_summary.py"', text)
        self.assertNotIn("api/ce/activity?ps=1", text)


if __name__ == "__main__":
    unittest.main()
