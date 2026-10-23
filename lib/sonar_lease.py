"""On-demand local SonarQube: start on first use, stop after the last user.

With ``SFT_SONAR_ON_DEMAND=1`` every lease-aware tool (easyscan-scan,
easyscan-check --require-local, sonar-local-lease) holds a lease while it uses
the server:

* acquire: under an exclusive ``flock``, prune stale leases, start the stack if
  it is down (and remember that leases started it), add our lease.
* release: under the same lock, drop our lease and stale ones. When no lease is
  left and leases started the server, wait for the Compute Engine queue to
  drain, then *stop* the containers (never remove them or their volumes).

A server that was already running when the first lease arrived was started by
someone else, so it is left running (``started_by_lease`` stays false).
A lease is stale when its process is gone or its PID was reused (start time
differs), so a crashed scan cannot keep the server up forever: the next
acquire/release, or ``sonar-local-down --if-idle``, cleans it up.
Tools that do not take leases (older EasyScanPKG versions, manual runs) are
invisible here; run them against a server you started yourself.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import socket
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

LEASE_DIR = Path.home() / ".config" / "sft" / "sonar-local"
LOCK_FILE = LEASE_DIR / "on-demand.lock"
STATE_FILE = LEASE_DIR / "on-demand.json"
LOCK_TIMEOUT_S = 900.0  # a cold start under the lock can take minutes
CE_IDLE_TIMEOUT_S = 300.0


def _proc_start_time(pid: int) -> str | None:
    """Kernel start time of pid (field 22 of /proc/<pid>/stat); None if gone."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    # comm may contain spaces/parens; fields after the last ')' are fixed.
    rest = raw.rsplit(")", 1)[-1].split()
    return rest[19] if len(rest) > 19 else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lease_is_stale(lease: dict) -> bool:
    if lease.get("host") and lease["host"] != socket.gethostname():
        return False  # cannot judge another host's process; keep it
    try:
        pid = int(lease.get("pid") or 0)
    except (TypeError, ValueError):
        return True
    if pid <= 0 or not _alive(pid):
        return True
    started = lease.get("proc_start")
    return bool(started) and _proc_start_time(pid) != started


@dataclass
class LeaseOps:
    """Server hooks (injectable for tests)."""

    is_running: Callable[[], bool]
    start: Callable[[], object]
    stop: Callable[[], object]
    wait_ce_idle: Callable[[], None]


def _default_ops() -> LeaseOps:
    import local_server

    return LeaseOps(
        is_running=local_server.is_running,
        start=lambda: local_server.start(wait=True),
        stop=lambda: local_server.stop(remove=False),
        wait_ce_idle=wait_ce_idle,
    )


@contextlib.contextmanager
def _locked(timeout_s: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    LEASE_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for {LOCK_FILE}") from None
                time.sleep(0.5)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the flock


def _load() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("leases", {})
    data.setdefault("started_by_lease", False)
    return data


def _save(data: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_FILE)


def _prune(data: dict) -> list[str]:
    stale = [lid for lid, lease in data["leases"].items() if lease_is_stale(lease)]
    for lid in stale:
        del data["leases"][lid]
    return stale


def acquire(purpose: str, *, ops: LeaseOps | None = None, pid: int | None = None) -> str:
    """Take a lease (starting the stack if needed). Returns the lease id."""
    ops = ops or _default_ops()
    pid = pid or os.getpid()
    lease_id = uuid.uuid4().hex
    with _locked():
        data = _load()
        _prune(data)
        if not ops.is_running():
            data["started_by_lease"] = True
            _save(data)  # even if start() fails half-way, --if-idle may stop it
        # Otherwise the recorded owner stays: false when the server was started
        # outside leases (leave it alone), true when leases started it (e.g. the
        # last lease holder crashed before releasing).
        # start() is idempotent: it brings the stack up if needed, waits for UP,
        # and refuses a foreign server on the port.
        try:
            ops.start()
        except BaseException:
            if not data["leases"] and not ops.is_running():
                data["started_by_lease"] = False  # nothing of ours is left running
                _save(data)
            raise
        data["leases"][lease_id] = {
            "pid": pid,
            "proc_start": _proc_start_time(pid),
            "host": socket.gethostname(),
            "purpose": purpose,
            "since": time.time(),
        }
        _save(data)
    return lease_id


def release(lease_id: str | None, *, ops: LeaseOps | None = None) -> str:
    """Drop a lease; stop the stack if it was the last and leases started it."""
    ops = ops or _default_ops()
    with _locked():
        data = _load()
        if lease_id:
            data["leases"].pop(lease_id, None)
        _prune(data)
        if data["leases"]:
            _save(data)
            return f"kept running ({len(data['leases'])} lease(s) left)"
        if not data["started_by_lease"]:
            _save(data)
            return "kept running (it was up before the first lease)"
        if ops.is_running():
            ops.wait_ce_idle()
            ops.stop()
        data["started_by_lease"] = False
        _save(data)
        return "stopped (last lease released; containers and volumes kept)"


def stop_if_idle(*, ops: LeaseOps | None = None) -> str:
    """Reap stale leases and stop a lease-started server nobody uses."""
    return release(None, ops=ops)


def active_leases() -> dict:
    with _locked():
        data = _load()
        if _prune(data):
            _save(data)
        return data


@contextlib.contextmanager
def lease(purpose: str, *, enabled: bool | None = None) -> Iterator[str | None]:
    """Hold a lease for the block when on-demand mode is on (no-op otherwise)."""
    if enabled is None:
        from sonar_runtime import on_demand_enabled

        enabled = on_demand_enabled()
    if not enabled:
        yield None
        return
    lease_id = acquire(purpose)
    previous = _trap_sigterm()
    try:
        yield lease_id
    finally:
        _restore_sigterm(previous)
        print(f"on-demand SonarQube: {release(lease_id)}", file=sys.stderr, flush=True)


def _raise_exit(signum, _frame):
    raise SystemExit(128 + signum)


def _trap_sigterm():
    """Turn SIGTERM (e.g. `timeout`) into SystemExit so the lease is released."""
    if threading.current_thread() is not threading.main_thread():
        return None
    return signal.signal(signal.SIGTERM, _raise_exit)


def _restore_sigterm(previous) -> None:
    if previous is not None:
        signal.signal(signal.SIGTERM, previous)


def _ce_activity_status(url: str, token: str) -> tuple[dict | None, bool]:
    """(status, retry). retry is False when the queue cannot be observed at all."""
    from sonar_api import api

    try:
        code, body = api(url, token, "GET", "/api/ce/activity_status", timeout=10)
    except OSError:
        return None, True  # transient: server busy or restarting
    if code == 200 and isinstance(body, dict):
        return body, False
    if code in (401, 403):
        return None, False  # token cannot see the queue; waiting will not help
    return None, True


def wait_ce_idle(timeout_s: float = CE_IDLE_TIMEOUT_S, *, poll_s: float = 3.0) -> bool:
    """Wait until the Compute Engine has no pending/in-progress tasks.

    Stopping mid-task would lose the analysis report a scan just uploaded.
    Only an explicit empty queue counts as idle; transient API errors are
    retried until the deadline. Returns True when the queue was seen empty.
    """
    from env_write import LOCAL_ENV, read_env_file

    local = read_env_file(LOCAL_ENV)
    url = local.get("SONARQUBE_URL", "http://127.0.0.1:9000")
    token = local.get("SONARQUBE_TOKEN", "")
    deadline = time.monotonic() + timeout_s
    while True:
        status, retry = _ce_activity_status(url, token)
        if status is not None and status.get("pending", 0) == 0 and status.get("inProgress", 0) == 0:
            return True
        if status is None and not retry:
            print("on-demand SonarQube: cannot read the CE queue (permission); stopping anyway",
                  file=sys.stderr, flush=True)
            return False
        if time.monotonic() >= deadline:
            print("on-demand SonarQube: CE queue still busy or unreadable at the deadline; stopping",
                  file=sys.stderr, flush=True)
            return False
        time.sleep(poll_s)
