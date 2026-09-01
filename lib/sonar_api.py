"""Shared SonarQube Web API helpers (JSON + multipart)."""

from __future__ import annotations

import base64
import json
import mimetypes
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def auth_header(token: str) -> str:
    return "Basic " + base64.b64encode(f"{token}:".encode()).decode("ascii")


def api(
    base: str,
    token: str,
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    form: dict[str, Any] | None = None,
    timeout: float = 30,
) -> tuple[int, Any]:
    """Call a Sonar API endpoint. Returns (http_status, parsed_json_or_text)."""
    url = base.rstrip("/") + path
    data: bytes | None = None
    headers: dict[str, str] = {"Authorization": auth_header(token)}
    if form is not None:
        data = urllib.parse.urlencode(
            {k: v for k, v in form.items() if v is not None}
        ).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        if query:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None}
            )
    elif query:
        url += "?" + urllib.parse.urlencode(
            {k: v for k, v in query.items() if v is not None}
        )
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, _parse_body(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, _parse_body(body)


def api_raw(
    base: str,
    token: str,
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    timeout: float = 60,
) -> tuple[int, bytes]:
    """Call an endpoint and return raw bytes (e.g. quality profile XML backup)."""
    url = base.rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode(
            {k: v for k, v in query.items() if v is not None}
        )
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", auth_header(token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def api_multipart(
    base: str,
    token: str,
    path: str,
    *,
    fields: dict[str, str] | None = None,
    files: dict[str, Path] | None = None,
    timeout: float = 120,
) -> tuple[int, Any]:
    """POST multipart/form-data (used by qualityprofiles/restore)."""
    boundary = f"----EasyScanBoundary{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in (fields or {}).items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")
    for name, path_obj in (files or {}).items():
        filename = Path(path_obj).name
        content = Path(path_obj).read_bytes()
        ctype = mimetypes.guess_type(filename)[0] or "application/xml"
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            (
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\n'
            ).encode()
        )
        body.extend(f"Content-Type: {ctype}\r\n\r\n".encode())
        body.extend(content)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    url = base.rstrip("/") + path
    req = urllib.request.Request(url, data=bytes(body), method="POST")
    req.add_header("Authorization", auth_header(token))
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, _parse_body(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return exc.code, _parse_body(raw)


def _parse_body(body: str) -> Any:
    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body
