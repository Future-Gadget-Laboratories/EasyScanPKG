"""Helpers for Sonar quality profile XML backups."""

from __future__ import annotations

import xml.etree.ElementTree as ET  # nosec B405 — parsing goes through safe_io
from pathlib import Path

from safe_io import parse_xml_file


def profile_name_from_xml(path: Path) -> str | None:
    try:
        root = parse_xml_file(path)
    except ET.ParseError:
        return None
    name_el = root.find(".//name")
    if name_el is not None and (name_el.text or "").strip():
        return name_el.text.strip()
    return None


def language_from_xml(path: Path) -> str | None:
    try:
        root = parse_xml_file(path)
    except ET.ParseError:
        return None
    lang = root.find(".//language")
    if lang is not None and (lang.text or "").strip():
        return lang.text.strip()
    return None
