"""SonarQube server health and authentication checks."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum


class AuthStatus(Enum):
    OK = "ok"
    MISSING_CREDENTIALS = "missing_credentials"
    UNAUTHORIZED = "unauthorized"
    UNREACHABLE = "unreachable"


@dataclass
class HealthResult:
    status: AuthStatus
    url: str
    detail: str
    http_status: int | None = None


def _auth_header(token: str) -> str:
    raw = f"{token}:".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def check_server(url: str, token: str | None, *, timeout: float = 5.0) -> HealthResult:
    base = url.rstrip("/")
    if not base:
        return HealthResult(AuthStatus.MISSING_CREDENTIALS, url, "URL is empty")
    if not token:
        return HealthResult(AuthStatus.MISSING_CREDENTIALS, base, "Token is missing")

    validate_url = f"{base}/api/authentication/validate"
    req = urllib.request.Request(validate_url, method="GET")
    req.add_header("Authorization", _auth_header(token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            if resp.status != 200:
                return HealthResult(
                    AuthStatus.UNAUTHORIZED,
                    base,
                    f"unexpected status {resp.status}",
                    resp.status,
                )
            try:
                valid = json.loads(body).get("valid") is True
            except json.JSONDecodeError:
                valid = False
            if valid:
                return HealthResult(AuthStatus.OK, base, "authenticated", resp.status)
            return HealthResult(
                AuthStatus.UNAUTHORIZED, base, "token rejected or expired", resp.status
            )
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return HealthResult(
                AuthStatus.UNAUTHORIZED, base, "token rejected or expired", exc.code
            )
        return HealthResult(
            AuthStatus.UNREACHABLE, base, f"HTTP {exc.code}: {exc.reason}", exc.code
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return HealthResult(AuthStatus.UNREACHABLE, base, str(exc))


def wait_for_system_up(url: str, *, timeout_s: float = 180.0, poll_s: float = 3.0) -> bool:
    import time

    base = url.rstrip("/")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/api/system/status", timeout=poll_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") == "UP":
                    return True
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            pass
        time.sleep(poll_s)
    return False
