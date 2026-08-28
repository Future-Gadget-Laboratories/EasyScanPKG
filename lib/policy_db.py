"""Persistent Sonar agent-bridge policy / scan preferences.

Store lives at ~/.config/sft/sonar-policy/policy.db by default.
Never stores tokens — only connection metadata and behavior prefs.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


DEFAULT_CONFIG_DIR = Path.home() / ".config" / "sft" / "sonar-policy"
SCHEMA_VERSION = 1

DEFAULT_SCAN_PREFS: dict[str, Any] = {
    "include_globs": ["**/*"],
    "exclude_globs": [
        "**/node_modules/**",
        "**/.git/**",
        "**/bin/**",
        "**/obj/**",
        "**/dist/**",
        "**/build/**",
    ],
    "max_files_per_analyze": 40,
    "languages": [],
    "require_ide_for_file_list": True,
    "allow_snippet_fallback": True,
}

DEFAULT_AGENT_PREFS: dict[str, Any] = {
    "disable_automatic_analysis_at_task_start": True,
    "must_analyze_on_task_end": True,
    "blocking_severities": ["BLOCKER", "CRITICAL"],
    "claim_quality_gate_from_local": False,
}

DEFAULT_HOOK_PREFS: dict[str, Any] = {
    "after_file_edit_enabled": True,
    "fail_open": True,
    "debounce_ms": 750,
    "path_allowlist": [],
}

DEFAULT_TOOL_PREFS: dict[str, Any] = {
    "toolsets": "analysis,ide,issues,projects,quality-gates,rules,security-hotspots",
    "read_only": False,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ConnectionPrefs:
    prefer_connected: bool = True
    last_url: str | None = None
    last_org: str | None = None
    mcp_image: str = "sonarsource/sonarqube-mcp"
    mcp_container_name: str = "sft-sonarqube-mcp"


class PolicyStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_CONFIG_DIR
        self.db_path = self.root / "policy.db"
        self.export_path = self.root / "preferences.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS connection_prefs (
                  id INTEGER PRIMARY KEY CHECK (id = 1),
                  prefer_connected INTEGER NOT NULL DEFAULT 1,
                  last_url TEXT,
                  last_org TEXT,
                  mcp_image TEXT NOT NULL,
                  mcp_container_name TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workspaces (
                  path TEXT PRIMARY KEY,
                  project_key TEXT,
                  last_ide_port INTEGER,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS kv_prefs (
                  domain TEXT NOT NULL,
                  key TEXT NOT NULL,
                  value_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (domain, key)
                );

                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  kind TEXT NOT NULL,
                  detail_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                """
            )
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            if conn.execute("SELECT 1 FROM connection_prefs WHERE id = 1").fetchone() is None:
                conn.execute(
                    """
                    INSERT INTO connection_prefs(
                      id, prefer_connected, last_url, last_org,
                      mcp_image, mcp_container_name, updated_at
                    ) VALUES (1, 1, NULL, NULL, ?, ?, ?)
                    """,
                    (
                        "sonarsource/sonarqube-mcp",
                        "sft-sonarqube-mcp",
                        _utc_now(),
                    ),
                )
            self._ensure_domain_defaults(conn, "scan", DEFAULT_SCAN_PREFS)
            self._ensure_domain_defaults(conn, "agent", DEFAULT_AGENT_PREFS)
            self._ensure_domain_defaults(conn, "hook", DEFAULT_HOOK_PREFS)
            self._ensure_domain_defaults(conn, "tool", DEFAULT_TOOL_PREFS)
            self._ensure_domain_defaults(
                conn,
                "connection",
                {
                    "fallback_to_local_server": True,
                    "local_url": "http://127.0.0.1:9000",
                    "local_project_prefix": "local-",
                    "active_backend": "unknown",
                },
            )

    @staticmethod
    def _ensure_domain_defaults(
        conn: sqlite3.Connection, domain: str, defaults: Mapping[str, Any]
    ) -> None:
        for key, value in defaults.items():
            exists = conn.execute(
                "SELECT 1 FROM kv_prefs WHERE domain = ? AND key = ?",
                (domain, key),
            ).fetchone()
            if exists is None:
                conn.execute(
                    """
                    INSERT INTO kv_prefs(domain, key, value_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (domain, key, json.dumps(value), _utc_now()),
                )

    def get_connection_prefs(self) -> ConnectionPrefs:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM connection_prefs WHERE id = 1").fetchone()
        assert row is not None
        return ConnectionPrefs(
            prefer_connected=bool(row["prefer_connected"]),
            last_url=row["last_url"],
            last_org=row["last_org"],
            mcp_image=row["mcp_image"],
            mcp_container_name=row["mcp_container_name"],
        )

    def set_connection_prefs(
        self,
        *,
        prefer_connected: bool | None = None,
        last_url: str | None = None,
        last_org: str | None = None,
        mcp_image: str | None = None,
        mcp_container_name: str | None = None,
        clear_url: bool = False,
        clear_org: bool = False,
    ) -> ConnectionPrefs:
        current = self.get_connection_prefs()
        next_prefs = ConnectionPrefs(
            prefer_connected=(
                current.prefer_connected if prefer_connected is None else prefer_connected
            ),
            last_url=None if clear_url else (current.last_url if last_url is None else last_url),
            last_org=None if clear_org else (current.last_org if last_org is None else last_org),
            mcp_image=current.mcp_image if mcp_image is None else mcp_image,
            mcp_container_name=(
                current.mcp_container_name
                if mcp_container_name is None
                else mcp_container_name
            ),
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE connection_prefs SET
                  prefer_connected = ?,
                  last_url = ?,
                  last_org = ?,
                  mcp_image = ?,
                  mcp_container_name = ?,
                  updated_at = ?
                WHERE id = 1
                """,
                (
                    1 if next_prefs.prefer_connected else 0,
                    next_prefs.last_url,
                    next_prefs.last_org,
                    next_prefs.mcp_image,
                    next_prefs.mcp_container_name,
                    _utc_now(),
                ),
            )
        self.export_preferences()
        return next_prefs

    def get_domain(self, domain: str) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, value_json FROM kv_prefs WHERE domain = ?",
                (domain,),
            ).fetchall()
        return {row["key"]: json.loads(row["value_json"]) for row in rows}

    def set_pref(self, domain: str, key: str, value: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kv_prefs(domain, key, value_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(domain, key) DO UPDATE SET
                  value_json = excluded.value_json,
                  updated_at = excluded.updated_at
                """,
                (domain, key, json.dumps(value), _utc_now()),
            )
        self.export_preferences()

    def upsert_workspace(
        self,
        path: str | Path,
        *,
        project_key: str | None = None,
        last_ide_port: int | None = None,
    ) -> dict[str, Any]:
        workspace = str(Path(path).resolve())
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM workspaces WHERE path = ?", (workspace,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO workspaces(path, project_key, last_ide_port, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (workspace, project_key, last_ide_port, _utc_now()),
                )
            else:
                conn.execute(
                    """
                    UPDATE workspaces SET
                      project_key = COALESCE(?, project_key),
                      last_ide_port = COALESCE(?, last_ide_port),
                      updated_at = ?
                    WHERE path = ?
                    """,
                    (project_key, last_ide_port, _utc_now(), workspace),
                )
            row = conn.execute(
                "SELECT * FROM workspaces WHERE path = ?", (workspace,)
            ).fetchone()
        self.export_preferences()
        assert row is not None
        return dict(row)

    def get_workspace(self, path: str | Path) -> dict[str, Any] | None:
        workspace = str(Path(path).resolve())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE path = ?", (workspace,)
            ).fetchone()
        return dict(row) if row else None

    def record_event(self, kind: str, detail: Mapping[str, Any]) -> None:
        # Strip any accidental token-looking keys
        safe = {
            k: v
            for k, v in detail.items()
            if "token" not in k.lower() and "password" not in k.lower()
        }
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events(kind, detail_json, created_at) VALUES (?, ?, ?)",
                (kind, json.dumps(safe), _utc_now()),
            )
            # Keep last 200 events
            conn.execute(
                """
                DELETE FROM events WHERE id NOT IN (
                  SELECT id FROM events ORDER BY id DESC LIMIT 200
                )
                """
            )

    def snapshot(self) -> dict[str, Any]:
        conn_prefs = self.get_connection_prefs()
        with self._connect() as conn:
            workspaces = [dict(r) for r in conn.execute("SELECT * FROM workspaces")]
            events = [
                dict(r)
                for r in conn.execute(
                    "SELECT kind, detail_json, created_at FROM events ORDER BY id DESC LIMIT 20"
                )
            ]
        for event in events:
            event["detail"] = json.loads(event.pop("detail_json"))
        return {
            "schema_version": SCHEMA_VERSION,
            "connection": {
                "prefer_connected": conn_prefs.prefer_connected,
                "last_url": conn_prefs.last_url,
                "last_org": conn_prefs.last_org,
                "mcp_image": conn_prefs.mcp_image,
                "mcp_container_name": conn_prefs.mcp_container_name,
            },
            "scan": self.get_domain("scan"),
            "agent": self.get_domain("agent"),
            "hook": self.get_domain("hook"),
            "tool": self.get_domain("tool"),
            "workspaces": workspaces,
            "recent_events": events,
        }

    def export_preferences(self) -> Path:
        data = self.snapshot()
        self.export_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return self.export_path

    def merge_project_overlay(self, workspace: str | Path) -> dict[str, Any]:
        """Merge optional <repo>/.sft/sonar-policy.json on top of global prefs."""
        base = self.snapshot()
        overlay_path = Path(workspace).resolve() / ".sft" / "sonar-policy.json"
        if not overlay_path.is_file():
            return base
        try:
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return base
        for domain in ("scan", "agent", "hook", "tool"):
            if isinstance(overlay.get(domain), dict):
                base[domain] = {**base.get(domain, {}), **overlay[domain]}
        if isinstance(overlay.get("connection"), dict):
            # Never accept tokens from overlay files either
            cleaned = {
                k: v
                for k, v in overlay["connection"].items()
                if "token" not in k.lower()
            }
            base["connection"] = {**base["connection"], **cleaned}
        if overlay.get("project_key"):
            self.upsert_workspace(workspace, project_key=str(overlay["project_key"]))
            ws = self.get_workspace(workspace)
            if ws:
                # refresh workspaces list in snapshot
                base = self.snapshot()
                for domain in ("scan", "agent", "hook", "tool"):
                    if isinstance(overlay.get(domain), dict):
                        base[domain] = {**base.get(domain, {}), **overlay[domain]}
        return base


def resolve_store(root: str | Path | None = None) -> PolicyStore:
    if root is not None:
        return PolicyStore(Path(root))
    env_root = os.environ.get("SFT_SONAR_POLICY_DIR")
    return PolicyStore(Path(env_root) if env_root else None)
