"""Hardened stdlib wrappers for URL fetching and XML parsing.

EasyScanPKG is stdlib-only, so these stand in for requests/defusedxml:

* ``urlopen`` only follows ``http``/``https`` URLs. ``urllib.request.urlopen``
  also accepts ``file://`` and custom schemes, so a bad ``SONARQUBE_URL`` could
  otherwise read local files.
* ``parse_xml`` / ``parse_xml_file`` refuse documents with a DOCTYPE or entity
  declarations (entity expansion / XXE). None of the XML we read (Sonar profile
  backups, cppcheck, valgrind) uses them.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # nosec B405 — only reached through the guarded parsers below
from pathlib import Path
from typing import IO, Any

ALLOWED_SCHEMES = frozenset({"http", "https"})


def _url_of(target: str | urllib.request.Request) -> str:
    return target.full_url if isinstance(target, urllib.request.Request) else str(target)


def check_url(url: str) -> str:
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"refusing non-HTTP(S) URL: {url!r}")
    return url


def urlopen(target: str | urllib.request.Request, *, timeout: float, **kwargs: Any) -> IO[bytes]:
    """``urllib.request.urlopen`` restricted to http(s) URLs."""
    check_url(_url_of(target))
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    return urllib.request.urlopen(target, timeout=timeout, **kwargs)  # nosec B310 — scheme checked above


def _reject_dtd(text: str) -> None:
    head = text.lstrip("﻿")
    if "<!DOCTYPE" in head or "<!ENTITY" in head:
        raise ET.ParseError("DOCTYPE/ENTITY declarations are not allowed")


def parse_xml(text: str) -> ET.Element:
    """``ET.fromstring`` that rejects DTDs and entity declarations."""
    _reject_dtd(text)
    return ET.fromstring(text)  # nosec B314 — DTD/entities rejected above


def parse_xml_file(path: str | Path) -> ET.Element:
    return parse_xml(Path(path).read_text(encoding="utf-8", errors="replace"))
