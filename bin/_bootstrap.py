#!/usr/bin/env python3
"""Shared path bootstrap for agent-bridge bin scripts."""

from __future__ import annotations

import sys
from pathlib import Path


def bootstrap() -> Path:
    root = Path(__file__).resolve().parents[1]
    lib = root / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    import os

    os.environ.setdefault("BRIDGE", str(root))
    os.environ.setdefault("SFT_AGENT_BRIDGE", str(root))
    return root
