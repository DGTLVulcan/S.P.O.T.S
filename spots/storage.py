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
        shot_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(shots)")}
        if "snapshot_path" not in shot_columns:
            self._conn.execute("ALTER TABLE shots ADD COLUMN snapshot_path TEXT")
        if "is_test" not in shot_columns:
            self._conn.execute("ALTER TABLE shots ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0")

        session_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(sessions)")}
        if "distance_m" not in session_columns:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN distance_m REAL")

    def new_session(self, unit_name: str, distance_m: float | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sessions (created_at, unit_name, distance_m) VALUES (?, ?, ?)",
                (time.time(), unit_name, distance_m),
            )
            self._conn.commit()
            return cur.lastrowid

    def update_session_distance(self, session_id: int, distance_m: float | None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET distance_m = ? WHERE id = ?", (distance_m, session_id)
            )
            self._conn.commit()

    def add_shot(
        self,
        session_id: int,
        seq: int,
        x_px: float,
        y_px: float,
        x_units: float | None,
        y_units: float | None,
        snapshot_path: str | None = None,
        is_test: bool = False,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO shots
                   (session_id, seq, x_px, y_px, x_units, y_units, snapshot_path, is_test, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, seq, x_px, y_px, x_units, y_units, snapshot_path, int(is_test), time.time()),
            )
            self._conn.commit()
            return cur.lastrowid

    def update_shot_units(
        self, session_id: int, seq: int, x_units: float | None, y_units: float | None
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE shots SET x_units = ?, y_units = ? WHERE session_id = ? AND seq = ?",
                (x_units, y_units, session_id, seq),
            )
            self._conn.commit()

    def delete_shot(self, session_id: int, seq: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM shots WHERE session_id = ? AND seq = ?", (session_id, seq)
            )
            self._conn.commit()

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
                "SELECT seq, x_px, y_px, x_units, y_units, snapshot_path, is_test, created_at "
                "FROM shots WHERE session_id = ? ORDER BY seq ASC",
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
                "is_test": bool(r[6]),
                "created_at": r[7],
            }
            for r in rows
        ]

    def list_sessions(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                """SELECT s.id, s.created_at, s.unit_name, s.distance_m, COUNT(sh.id)
                   FROM sessions s LEFT JOIN shots sh ON sh.session_id = s.id
                   GROUP BY s.id ORDER BY s.id DESC"""
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "created_at": r[1],
                "unit_name": r[2],
                "distance_m": r[3],
                "shot_count": r[4],
            }
            for r in rows
        ]

    def get_session(self, session_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, created_at, unit_name, distance_m FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "created_at": row[1], "unit_name": row[2], "distance_m": row[3]}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
