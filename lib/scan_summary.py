"""Post-scan summary for bin/sonar-scan that waits for Sonar to settle.

Sonar's issue index lags behind CE processing: right after the CE task reports
SUCCESS, api/issues/search can still return total=0 for roughly a minute.  So
this module:

1. reads ceTaskId from <workspace>/.scannerwork/report-task.txt and polls
   api/ce/task?id=... until SUCCESS (FAILED/CANCELED end early), with a timeout;
2. polls api/issues/search?ps=1 until `total` holds steady across
   ``stable_polls`` consecutive reads, with a timeout;
3. only then prints totals, or a clear WARNING when a wait timed out.

The token is read from the environment (SONARQUBE_TOKEN) and sent only as an
Authorization header; it never touches argv or output.

Tunables (env): SFT_SCAN_CE_TIMEOUT (s, default 300), SFT_SCAN_ISSUES_TIMEOUT
(s, default 180), SFT_SCAN_POLL_INTERVAL (s, default 3), SFT_SCAN_STABLE_POLLS
(default 3).
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

TRANSIENT = (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError,
             ConnectionError, json.JSONDecodeError, KeyError, ValueError)

CE_DONE = ("SUCCESS", "FAILED", "CANCELED")


class SonarClient:
    def __init__(self, url: str, token: str, timeout: float = 30.0) -> None:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("SonarQube URL must be an HTTP(S) URL with a host")
        self.url = url.rstrip("/")
        self._auth = base64.b64encode(f"{token}:".encode()).decode()
        self.timeout = timeout

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        full = f"{self.url}{path}"
        if params:
            full += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(full, headers={"Authorization": f"Basic {self._auth}"})
        # The URL scheme is validated in __init__.
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            return json.loads(resp.read().decode())


def read_ce_task_id(workspace: str) -> Optional[str]:
    path = Path(workspace) / ".scannerwork" / "report-task.txt"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("ceTaskId="):
                return line.split("=", 1)[1].strip() or None
    except OSError:
        return None
    return None


def wait_ce_task(client: SonarClient, task_id: Optional[str], project_key: str,
                 timeout: float, interval: float,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> tuple[str, bool]:
    """Return (status, timed_out)."""
    deadline = clock() + timeout
    status = "UNKNOWN"
    while True:
        try:
            if task_id:
                status = client.get("/api/ce/task", {"id": task_id}).get("task", {}).get("status", "UNKNOWN")
            else:
                tasks = client.get("/api/ce/activity", {"component": project_key, "ps": 1}).get("tasks", [])
                status = tasks[0].get("status", "UNKNOWN") if tasks else "UNKNOWN"
        except TRANSIENT as exc:  # transient server hiccup: keep polling
            status = f"ERROR ({type(exc).__name__})"
        if status in CE_DONE:
            return status, False
        if clock() >= deadline:
            return status, True
        sleep(interval)


def wait_issue_count(client: SonarClient, project_key: str, timeout: float,
                     interval: float, stable_polls: int,
                     sleep: Callable[[float], None] = time.sleep,
                     clock: Callable[[], float] = time.monotonic) -> tuple[Optional[int], bool]:
    """Poll the issue total until it repeats `stable_polls` times in a row.

    Return (last_total, timed_out).
    """
    deadline = clock() + timeout
    last: Optional[int] = None
    streak = 0
    while True:
        try:
            total = int(client.get("/api/issues/search", {
                "componentKeys": project_key, "resolved": "false", "ps": 1,
            }).get("total", 0))
        except TRANSIENT:
            total = None
        if total is not None:
            streak = streak + 1 if total == last else 1
            last = total
            if streak >= max(1, stable_polls):
                return last, False
        if clock() >= deadline:
            return last, True
        sleep(interval)


def print_issues(client: SonarClient, project_key: str, out=sys.stdout) -> None:
    data = client.get("/api/issues/search", {
        "componentKeys": project_key, "ps": 50, "resolved": "false",
        "facets": "severities,types,rules",
    })
    for issue in data.get("issues", [])[:25]:
        comp = issue.get("component", "").split(":")[-1]
        print(f"  [{issue.get('severity')}/{issue.get('type')}] "
              f"{comp}:{issue.get('line', '-')}  {issue.get('message', '')[:120]}  ({issue.get('rule')})", file=out)


def run(env: dict, out=sys.stdout, err=sys.stderr,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic) -> int:
    client = SonarClient(env["SONARQUBE_URL"], env["SONARQUBE_TOKEN"])
    key = env["SFT_PROJECT_KEY"]
    interval = float(env.get("SFT_SCAN_POLL_INTERVAL", "3"))
    task_id = read_ce_task_id(env.get("SFT_SCAN_WORKSPACE", "."))
    status, ce_to = wait_ce_task(client, task_id, key, float(env.get("SFT_SCAN_CE_TIMEOUT", "300")),
                                 interval, sleep, clock)
    print(f"    CE status: {status}", file=out)
    if ce_to:
        print(f"WARNING: CE task {task_id or '(latest)'} did not finish before timeout "
              f"(last status {status}); issue totals not reported.", file=err)
        return 3
    if status != "SUCCESS":
        print(f"WARNING: CE task ended {status}; issue totals not reported.", file=err)
        return 4
    total, iss_to = wait_issue_count(client, key, float(env.get("SFT_SCAN_ISSUES_TIMEOUT", "180")),
                                     interval, int(env.get("SFT_SCAN_STABLE_POLLS", "3")), sleep, clock)
    if iss_to:
        print(f"WARNING: issue count did not settle before timeout (last seen: {total}); "
              "the index may still be catching up, re-check the dashboard.", file=err)
    print(f"total issues: {total if total is not None else 'unknown'}{' (unsettled)' if iss_to else ''}", file=out)
    try:
        print_issues(client, key, out)
    except TRANSIENT as exc:
        print(f"    issue listing failed: {type(exc).__name__}", file=err)
    if total and total > 25:
        print(f"  ... and {total - 25} more", file=out)
    q = urllib.parse.quote(key)
    print(f"\nUI: {client.url}/dashboard?id={q}", file=out)
    print(f"Issues: {client.url}/project/issues?id={q}&resolved=false", file=out)
    return 5 if iss_to else 0


if __name__ == "__main__":
    sys.exit(run(dict(os.environ)))
