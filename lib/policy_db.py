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
SCHEMA_VERSION = 2

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


@dataclass
class AnalysisContext:
    name: str
    url: str | None = None
    token_ref: str | None = None
    org: str | None = None
    project_key: str | None = None
    tags: list[str] | None = None
    remediation_path: str | None = None
    volume_namespace: str | None = None
    active: bool = False
    updated_at: str | None = None


@dataclass
class ContextProfile:
    context_name: str
    language: str
    profile_name: str
    profile_key: str | None = None
    is_default: bool = False
    updated_at: str | None = None


class PolicyStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_CONFIG_DIR
        self.db_path = self.root / "policy.db"
        self.export_path = self.root / "preferences.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def ensure_schema(self) -> None:
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
                  context_name TEXT,
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

                CREATE TABLE IF NOT EXISTS contexts (
                  name TEXT PRIMARY KEY,
                  url TEXT,
                  token_ref TEXT,
                  org TEXT,
                  project_key TEXT,
                  tags_json TEXT NOT NULL DEFAULT '[]',
                  remediation_path TEXT,
                  volume_namespace TEXT,
                  active INTEGER NOT NULL DEFAULT 0,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS context_profiles (
                  context_name TEXT NOT NULL,
                  language TEXT NOT NULL,
                  profile_name TEXT NOT NULL,
                  profile_key TEXT,
                  is_default INTEGER NOT NULL DEFAULT 0,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (context_name, language, profile_name),
                  FOREIGN KEY (context_name) REFERENCES contexts(name) ON DELETE CASCADE
                );
                """
            )
            # Migrate older DBs that lack workspaces.context_name
            cols = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(workspaces)").fetchall()
            }
            if "context_name" not in cols:
                conn.execute("ALTER TABLE workspaces ADD COLUMN context_name TEXT")

            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            else:
                try:
                    current = int(row["value"])
                except ValueError:
                    current = 0
                if current < SCHEMA_VERSION:
                    conn.execute(
                        "UPDATE meta SET value = ? WHERE key = 'schema_version'",
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
                    "active_context": None,
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
        context_name: str | None = None,
    ) -> dict[str, Any]:
        workspace = str(Path(path).resolve())
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM workspaces WHERE path = ?", (workspace,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO workspaces(path, project_key, last_ide_port, context_name, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (workspace, project_key, last_ide_port, context_name, _utc_now()),
                )
            else:
                conn.execute(
                    """
                    UPDATE workspaces SET
                      project_key = COALESCE(?, project_key),
                      last_ide_port = COALESCE(?, last_ide_port),
                      context_name = COALESCE(?, context_name),
                      updated_at = ?
                    WHERE path = ?
                    """,
                    (project_key, last_ide_port, context_name, _utc_now(), workspace),
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

    @staticmethod
    def _row_to_context(row: sqlite3.Row) -> AnalysisContext:
        tags_raw = row["tags_json"] or "[]"
        try:
            tags = json.loads(tags_raw)
        except json.JSONDecodeError:
            tags = []
        if not isinstance(tags, list):
            tags = []
        return AnalysisContext(
            name=row["name"],
            url=row["url"],
            token_ref=row["token_ref"],
            org=row["org"],
            project_key=row["project_key"],
            tags=[str(t) for t in tags],
            remediation_path=row["remediation_path"],
            volume_namespace=row["volume_namespace"],
            active=bool(row["active"]),
            updated_at=row["updated_at"],
        )

    def list_contexts(self) -> list[AnalysisContext]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM contexts ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [self._row_to_context(r) for r in rows]

    def get_context(self, name: str) -> AnalysisContext | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM contexts WHERE name = ?", (name,)
            ).fetchone()
        return self._row_to_context(row) if row else None

    def get_active_context_name(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name FROM contexts WHERE active = 1 LIMIT 1"
            ).fetchone()
        if row:
            return str(row["name"])
        domain = self.get_domain("connection")
        active = domain.get("active_context")
        return str(active) if active else None

    def upsert_context(
        self,
        name: str,
        *,
        url: str | None = None,
        token_ref: str | None = None,
        org: str | None = None,
        project_key: str | None = None,
        tags: list[str] | None = None,
        remediation_path: str | None = None,
        volume_namespace: str | None = None,
        activate: bool = False,
        clear_org: bool = False,
        clear_project_key: bool = False,
        clear_remediation: bool = False,
    ) -> AnalysisContext:
        name = name.strip()
        if not name or any(c in name for c in "/\\ \t\n"):
            raise ValueError("context name must be a non-empty token without spaces/slashes")
        existing = self.get_context(name)
        next_tags = tags if tags is not None else (existing.tags if existing else [])
        next_url = url if url is not None else (existing.url if existing else None)
        next_token = token_ref if token_ref is not None else (existing.token_ref if existing else None)
        next_org = None if clear_org else (org if org is not None else (existing.org if existing else None))
        next_pk = (
            None
            if clear_project_key
            else (
                project_key
                if project_key is not None
                else (existing.project_key if existing else None)
            )
        )
        next_rem = (
            None
            if clear_remediation
            else (
                remediation_path
                if remediation_path is not None
                else (existing.remediation_path if existing else None)
            )
        )
        next_vol = (
            volume_namespace
            if volume_namespace is not None
            else (existing.volume_namespace if existing else None)
        )
        now = _utc_now()
        with self._connect() as conn:
            if activate:
                conn.execute("UPDATE contexts SET active = 0")
            active_flag = 1 if activate else (1 if existing and existing.active else 0)
            conn.execute(
                """
                INSERT INTO contexts(
                  name, url, token_ref, org, project_key, tags_json,
                  remediation_path, volume_namespace, active, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                  url = excluded.url,
                  token_ref = excluded.token_ref,
                  org = excluded.org,
                  project_key = excluded.project_key,
                  tags_json = excluded.tags_json,
                  remediation_path = excluded.remediation_path,
                  volume_namespace = excluded.volume_namespace,
                  active = excluded.active,
                  updated_at = excluded.updated_at
                """,
                (
                    name,
                    next_url,
                    next_token,
                    next_org,
                    next_pk,
                    json.dumps(list(next_tags or [])),
                    next_rem,
                    next_vol,
                    active_flag,
                    now,
                ),
            )
        if activate:
            self.set_pref("connection", "active_context", name)
        self.export_preferences()
        ctx = self.get_context(name)
        assert ctx is not None
        return ctx

    def use_context(self, name: str) -> AnalysisContext:
        ctx = self.get_context(name)
        if ctx is None:
            raise KeyError(f"unknown context: {name}")
        with self._connect() as conn:
            conn.execute("UPDATE contexts SET active = 0")
            conn.execute(
                "UPDATE contexts SET active = 1, updated_at = ? WHERE name = ?",
                (_utc_now(), name),
            )
        self.set_pref("connection", "active_context", name)
        if ctx.url:
            self.set_connection_prefs(last_url=ctx.url, last_org=ctx.org)
        self.export_preferences()
        out = self.get_context(name)
        assert out is not None
        return out

    def delete_context(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM context_profiles WHERE context_name = ?", (name,))
            conn.execute("DELETE FROM contexts WHERE name = ?", (name,))
        if self.get_domain("connection").get("active_context") == name:
            self.set_pref("connection", "active_context", None)
        self.export_preferences()

    def list_context_profiles(self, context_name: str) -> list[ContextProfile]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM context_profiles
                WHERE context_name = ?
                ORDER BY language, profile_name
                """,
                (context_name,),
            ).fetchall()
        return [
            ContextProfile(
                context_name=r["context_name"],
                language=r["language"],
                profile_name=r["profile_name"],
                profile_key=r["profile_key"],
                is_default=bool(r["is_default"]),
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def upsert_context_profile(
        self,
        context_name: str,
        *,
        language: str,
        profile_name: str,
        profile_key: str | None = None,
        is_default: bool = False,
    ) -> ContextProfile:
        if self.get_context(context_name) is None:
            raise KeyError(f"unknown context: {context_name}")
        now = _utc_now()
        with self._connect() as conn:
            if is_default:
                conn.execute(
                    """
                    UPDATE context_profiles SET is_default = 0
                    WHERE context_name = ? AND language = ?
                    """,
                    (context_name, language),
                )
            conn.execute(
                """
                INSERT INTO context_profiles(
                  context_name, language, profile_name, profile_key, is_default, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(context_name, language, profile_name) DO UPDATE SET
                  profile_key = excluded.profile_key,
                  is_default = excluded.is_default,
                  updated_at = excluded.updated_at
                """,
                (
                    context_name,
                    language,
                    profile_name,
                    profile_key,
                    1 if is_default else 0,
                    now,
                ),
            )
        self.export_preferences()
        profiles = [
            p
            for p in self.list_context_profiles(context_name)
            if p.language == language and p.profile_name == profile_name
        ]
        assert profiles
        return profiles[0]

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
        contexts = self.list_contexts()
        with self._connect() as conn:
            workspaces = [dict(r) for r in conn.execute("SELECT * FROM workspaces")]
            events = [
                dict(r)
                for r in conn.execute(
                    "SELECT kind, detail_json, created_at FROM events ORDER BY id DESC LIMIT 20"
                )
            ]
            profiles = [dict(r) for r in conn.execute("SELECT * FROM context_profiles")]
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
                "active_context": self.get_active_context_name(),
            },
            "scan": self.get_domain("scan"),
            "agent": self.get_domain("agent"),
            "hook": self.get_domain("hook"),
            "tool": self.get_domain("tool"),
            "workspaces": workspaces,
            "contexts": [
                {
                    "name": c.name,
                    "url": c.url,
                    "token_ref": c.token_ref,
                    "org": c.org,
                    "project_key": c.project_key,
                    "tags": c.tags or [],
                    "remediation_path": c.remediation_path,
                    "volume_namespace": c.volume_namespace,
                    "active": c.active,
                }
                for c in contexts
            ],
            "context_profiles": profiles,
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
