"""SQLite storage. Real snapshots and lab scenarios live in separate tables;
the lab code path has no function that writes to `snapshots`."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY,
    persona_id TEXT NOT NULL,
    persona_name TEXT NOT NULL,
    platform TEXT NOT NULL,
    game TEXT NOT NULL,
    user_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (persona_id, platform, game)
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES profiles(id),
    captured_at TEXT NOT NULL,
    source_kind TEXT NOT NULL,          -- battlelog-live | manual-import
    source_detail TEXT NOT NULL,        -- URLs or file name
    raw_sha256 TEXT NOT NULL,           -- digest of the raw responses as received
    normalized_json TEXT NOT NULL,      -- ProfileParser output
    raw_json TEXT NOT NULL              -- redacted raw responses
);
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    trigger TEXT NOT NULL,              -- manual | auto | import
    status TEXT NOT NULL,               -- running | ok | error
    error_kind TEXT,
    error_message TEXT,
    snapshot_id INTEGER,
    changes_json TEXT
);
CREATE TABLE IF NOT EXISTS lab_scenarios (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    base_snapshot_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    modifications_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    classification TEXT NOT NULL,       -- READ | SIMULATED | AUTHORIZED_WRITE | UNSUPPORTED | SYNC | ADMIN
    target TEXT,
    outcome TEXT NOT NULL,              -- ok | refused | error
    details_json TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---- profiles / snapshots -------------------------------------------------
    def upsert_profile(self, persona_id: str, persona_name: str, platform: str, game: str,
                       user_id: str | None, now: str) -> int:
        row = self.one("SELECT id FROM profiles WHERE persona_id=? AND platform=? AND game=?",
                       (persona_id, platform, game))
        if row:
            self.execute("UPDATE profiles SET persona_name=?, user_id=COALESCE(?, user_id) WHERE id=?",
                         (persona_name, user_id, row["id"]))
            return row["id"]
        cur = self.execute(
            "INSERT INTO profiles (persona_id, persona_name, platform, game, user_id, created_at) "
            "VALUES (?,?,?,?,?,?)", (persona_id, persona_name, platform, game, user_id, now))
        return cur.lastrowid

    def insert_snapshot(self, profile_id: int, captured_at: str, source_kind: str, source_detail: str,
                        raw_sha256: str, normalized: dict, raw: Any) -> int:
        cur = self.execute(
            "INSERT INTO snapshots (profile_id, captured_at, source_kind, source_detail, raw_sha256, "
            "normalized_json, raw_json) VALUES (?,?,?,?,?,?,?)",
            (profile_id, captured_at, source_kind, source_detail, raw_sha256,
             json.dumps(normalized, sort_keys=True), json.dumps(raw)))
        return cur.lastrowid

    def snapshot(self, snapshot_id: int) -> dict | None:
        row = self.one("SELECT * FROM snapshots WHERE id=?", (snapshot_id,))
        if row:
            row["normalized"] = json.loads(row.pop("normalized_json"))
            row.pop("raw_json", None)
        return row

    def latest_snapshots(self, profile_id: int, limit: int = 2) -> list[dict]:
        rows = self.query("SELECT id FROM snapshots WHERE profile_id=? ORDER BY id DESC LIMIT ?",
                          (profile_id, limit))
        return [self.snapshot(r["id"]) for r in rows]

    def snapshot_history(self, profile_id: int, limit: int = 500) -> list[dict]:
        rows = self.query("SELECT id FROM snapshots WHERE profile_id=? ORDER BY id ASC LIMIT ?",
                          (profile_id, limit))
        return [self.snapshot(r["id"]) for r in rows]

    def snapshot_digest_table(self) -> list[tuple]:
        """Used by isolation tests: a fingerprint of every real snapshot row."""
        with self._lock:
            return [tuple(r) for r in self._conn.execute(
                "SELECT id, raw_sha256, normalized_json, raw_json FROM snapshots ORDER BY id").fetchall()]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
