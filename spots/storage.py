"""SQLite persistence for sessions and shots (stdlib sqlite3, no extra deps)."""
from __future__ import annotations

import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    unit_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    seq INTEGER NOT NULL,
    x_px REAL NOT NULL,
    y_px REAL NOT NULL,
    x_units REAL,
    y_units REAL,
    created_at REAL NOT NULL
);
"""


class Storage:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate_locked()
            self._conn.commit()

    def _migrate_locked(self) -> None:
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(shots)")}
        if "snapshot_path" not in columns:
            self._conn.execute("ALTER TABLE shots ADD COLUMN snapshot_path TEXT")

    def new_session(self, unit_name: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sessions (created_at, unit_name) VALUES (?, ?)",
                (time.time(), unit_name),
            )
            self._conn.commit()
            return cur.lastrowid

    def add_shot(
        self,
        session_id: int,
        seq: int,
        x_px: float,
        y_px: float,
        x_units: float | None,
        y_units: float | None,
        snapshot_path: str | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO shots
                   (session_id, seq, x_px, y_px, x_units, y_units, snapshot_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, seq, x_px, y_px, x_units, y_units, snapshot_path, time.time()),
            )
            self._conn.commit()
            return cur.lastrowid

    def delete_last_shot(self, session_id: int) -> None:
        with self._lock:
            self._conn.execute(
                """DELETE FROM shots WHERE id = (
                       SELECT id FROM shots WHERE session_id = ?
                       ORDER BY seq DESC LIMIT 1
                   )""",
                (session_id,),
            )
            self._conn.commit()

    def get_shots(self, session_id: int) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT seq, x_px, y_px, x_units, y_units, snapshot_path, created_at FROM shots "
                "WHERE session_id = ? ORDER BY seq ASC",
                (session_id,),
            )
            rows = cur.fetchall()
        return [
            {
                "seq": r[0],
                "x_px": r[1],
                "y_px": r[2],
                "x_units": r[3],
                "y_units": r[4],
                "snapshot_path": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]

    def list_sessions(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                """SELECT s.id, s.created_at, s.unit_name, COUNT(sh.id)
                   FROM sessions s LEFT JOIN shots sh ON sh.session_id = s.id
                   GROUP BY s.id ORDER BY s.id DESC"""
            )
            rows = cur.fetchall()
        return [
            {"id": r[0], "created_at": r[1], "unit_name": r[2], "shot_count": r[3]}
            for r in rows
        ]

    def get_session(self, session_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, created_at, unit_name FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "created_at": row[1], "unit_name": row[2]}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
