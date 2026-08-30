"""SQLite persistence for sessions and shots (stdlib sqlite3, no extra deps)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time

_SCHEMA = """
-- No AUTOINCREMENT on sessions: that keyword exists precisely to stop
-- SQLite ever reusing a rowid, so deleted session numbers would be burned
-- forever and the ids would climb even after clearing the whole history.
-- new_session() picks the id itself (see _next_free_session_id_locked).
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
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

-- Rifles, scopes and ammo share one table with a `kind` discriminator:
-- they need identical add/rename/delete handling, and only scopes use the
-- click columns (turret value, which differs per scope).
CREATE TABLE IF NOT EXISTS equipment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    notes TEXT,
    click_value REAL,
    click_unit TEXT,
    -- Kind-specific specs (calibre, barrel length, bullet weight, ...) as a
    -- JSON object: the three kinds record genuinely different things, and a
    -- column per field across all of them would be mostly NULL. See
    -- spots/equipment_specs.py for the schema they're validated against.
    specs TEXT,
    created_at REAL NOT NULL
);

-- Small key/value store for app state that must outlive a restart but has
-- no business in config.yaml -- currently which rifle/scope/ammo is
-- selected. Keeping that next to the equipment it points at means the two
-- can never disagree, and selecting something no longer depends on the
-- config file being writable.
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

EQUIPMENT_KINDS = ("rifle", "scope", "ammo")

# Seeded once, on a database that has never had equipment, purely so the
# dropdowns aren't empty on first run. All of it is editable.
_DEFAULT_EQUIPMENT = [
    ("rifle", "Primary Rifle", None, None, None,
     {"calibre": ".308 Winchester", "barrel_length_in": 20.0, "twist_rate": "1:10",
      "action": "bolt"}),
    ("rifle", "Rimfire", None, None, None,
     {"calibre": ".22 LR", "barrel_length_in": 18.0, "twist_rate": "1:16", "action": "bolt"}),
    ("scope", "1/4 MOA scope", None, 0.25, "moa", {"focal_plane": "ffp", "zero_distance_m": 100.0}),
    ("scope", "1/8 MOA scope", None, 0.125, "moa", {"focal_plane": "ffp"}),
    ("scope", "0.1 mrad scope", None, 0.1, "mrad", {"focal_plane": "ffp", "zero_distance_m": 100.0}),
    ("ammo", "Factory match", None, None, None,
     {"calibre": ".308 Winchester", "bullet_grains": 168.0, "bullet": "HPBT",
      "manufacturer": "Federal"}),
    ("ammo", "Handload", None, None, None,
     {"calibre": ".308 Winchester", "bullet_grains": 168.0, "powder": "Varget",
      "charge_grains": 42.5}),
]


def _decode_specs(raw: str | None) -> dict:
    """Specs are stored as JSON; a row written before that column existed
    (or somehow corrupted) reads as an empty dict rather than raising."""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class Storage:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate_locked()
            self._seed_equipment_locked()
            self._conn.commit()

    def _migrate_locked(self) -> None:
        shot_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(shots)")}
        if "snapshot_path" not in shot_columns:
            self._conn.execute("ALTER TABLE shots ADD COLUMN snapshot_path TEXT")
        if "is_test" not in shot_columns:
            self._conn.execute("ALTER TABLE shots ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0")
        # Excluded shots stay on the target and in history but are left out
        # of the group maths -- a called flyer shouldn't have to be deleted
        # (and lose its snapshot and place in the string) to stop skewing
        # the group size.
        if "excluded" not in shot_columns:
            self._conn.execute("ALTER TABLE shots ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0")

        equipment_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(equipment)")}
        if equipment_columns and "specs" not in equipment_columns:
            self._conn.execute("ALTER TABLE equipment ADD COLUMN specs TEXT")

        session_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(sessions)")}
        if "distance_m" not in session_columns:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN distance_m REAL")
        if "name" not in session_columns:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN name TEXT")
        # Calibration is stored per session so a restart (or a Pi reboot
        # mid-string) can restore the scale and target-centre origin rather
        # than making the user re-calibrate and orphaning the shots already
        # recorded against it.
        for column in ("calib_units_per_px", "calib_origin_x", "calib_origin_y"):
            if column not in session_columns:
                self._conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} REAL")
        for column in ("rifle", "scope", "ammo"):
            if column not in session_columns:
                self._conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} TEXT")
        if "calib_center_marked" not in session_columns:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN calib_center_marked INTEGER NOT NULL DEFAULT 0"
            )

    def _seed_equipment_locked(self) -> None:
        existing = self._conn.execute("SELECT COUNT(*) FROM equipment").fetchone()[0]
        if existing:
            return
        self._conn.executemany(
            "INSERT INTO equipment (kind, name, notes, click_value, click_unit, specs, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (k, n, notes, cv, cu, json.dumps(specs), time.time())
                for k, n, notes, cv, cu, specs in _DEFAULT_EQUIPMENT
            ],
        )

    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM app_state WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else default

    def set_state(self, key: str, value: str | None) -> None:
        with self._lock:
            if value is None:
                self._conn.execute("DELETE FROM app_state WHERE key = ?", (key,))
            else:
                self._conn.execute(
                    "INSERT INTO app_state (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
            self._conn.commit()

    def _selection_key(self, kind: str) -> str:
        return f"selected_{kind}"

    def get_selected_equipment(self) -> dict[str, int | None]:
        """Currently selected id per kind, self-healing: a selection whose
        equipment has since been deleted (or was written for another kind)
        is cleared rather than lingering as a dangling reference that shows
        up as "none" forever.
        """
        selected: dict[str, int | None] = {}
        for kind in EQUIPMENT_KINDS:
            raw = self.get_state(self._selection_key(kind))
            item_id = None
            if raw is not None:
                try:
                    candidate = int(raw)
                except (TypeError, ValueError):
                    candidate = None
                item = self.get_equipment(candidate) if candidate is not None else None
                if item is not None and item["kind"] == kind:
                    item_id = candidate
                else:
                    self.set_state(self._selection_key(kind), None)
            selected[kind] = item_id
        return selected

    def set_selected_equipment(self, kind: str, equipment_id: int | None) -> None:
        self.set_state(self._selection_key(kind), None if equipment_id is None else str(equipment_id))

    def list_equipment(self, kind: str | None = None) -> list[dict]:
        query = (
            "SELECT id, kind, name, notes, click_value, click_unit, specs FROM equipment"
            + (" WHERE kind = ?" if kind else "")
            + " ORDER BY kind ASC, name ASC"
        )
        with self._lock:
            rows = self._conn.execute(query, (kind,) if kind else ()).fetchall()
        return [
            {
                "id": r[0],
                "kind": r[1],
                "name": r[2],
                "notes": r[3],
                "click_value": r[4],
                "click_unit": r[5],
                "specs": _decode_specs(r[6]),
            }
            for r in rows
        ]

    def get_equipment(self, equipment_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, kind, name, notes, click_value, click_unit, specs FROM equipment"
                " WHERE id = ?",
                (equipment_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "kind": row[1],
            "name": row[2],
            "notes": row[3],
            "click_value": row[4],
            "click_unit": row[5],
            "specs": _decode_specs(row[6]),
        }

    def add_equipment(
        self,
        kind: str,
        name: str,
        notes: str | None = None,
        click_value: float | None = None,
        click_unit: str | None = None,
        specs: dict | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO equipment (kind, name, notes, click_value, click_unit, specs, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, name, notes, click_value, click_unit, json.dumps(specs or {}), time.time()),
            )
            self._conn.commit()
            return cur.lastrowid

    def update_equipment(
        self,
        equipment_id: int,
        name: str,
        notes: str | None = None,
        click_value: float | None = None,
        click_unit: str | None = None,
        specs: dict | None = None,
    ) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE equipment SET name = ?, notes = ?, click_value = ?, click_unit = ?,"
                " specs = ? WHERE id = ?",
                (name, notes, click_value, click_unit, json.dumps(specs or {}), equipment_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def delete_equipment(self, equipment_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM equipment WHERE id = ?", (equipment_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def _next_free_session_id_locked(self) -> int:
        """Smallest positive integer not currently used by a session.

        Deleting sessions frees their numbers for reuse, so the ids track how
        many sessions you actually have rather than how many you have ever
        created -- clear the history and the next session is #1 again. The
        candidate set is every existing id + 1 plus 1 itself, which always
        contains the answer: either a gap left by a deletion, or one past
        the highest in use.
        """
        row = self._conn.execute(
            """SELECT MIN(candidate) FROM (
                   SELECT 1 AS candidate
                   UNION ALL
                   SELECT id + 1 FROM sessions
               )
               WHERE candidate NOT IN (SELECT id FROM sessions)"""
        ).fetchone()
        return row[0] if row and row[0] is not None else 1

    def new_session(
        self,
        unit_name: str,
        distance_m: float | None = None,
        rifle: str | None = None,
        scope: str | None = None,
        ammo: str | None = None,
    ) -> int:
        with self._lock:
            # Chosen and inserted under the same lock, so two sessions can
            # never race onto the same reused id.
            session_id = self._next_free_session_id_locked()
            self._conn.execute(
                "INSERT INTO sessions (id, created_at, unit_name, distance_m, rifle, scope, ammo)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, time.time(), unit_name, distance_m, rifle, scope, ammo),
            )
            self._conn.commit()
            return session_id

    def rename_session(self, session_id: int, name: str | None) -> bool:
        """Sets a session's display name. Pass None (or blank) to clear it and
        fall back to the default "Session N" label. Returns False if no such
        session exists.
        """
        cleaned = (name or "").strip() or None
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET name = ? WHERE id = ?", (cleaned, session_id)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def delete_session(self, session_id: int) -> list[str]:
        """Removes a session and its shots, returning the snapshot paths that
        belonged to it so the caller can delete the image files too (this
        layer deliberately doesn't touch the filesystem).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT snapshot_path FROM shots WHERE session_id = ? AND snapshot_path IS NOT NULL",
                (session_id,),
            ).fetchall()
            self._conn.execute("DELETE FROM shots WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()
        return [r[0] for r in rows]

    def delete_all_sessions(self) -> list[str]:
        """Clears the entire history in one transaction, returning every
        snapshot path so the caller can remove the image files too. Session
        numbering restarts at 1 afterwards (see _next_free_session_id_locked).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT snapshot_path FROM shots WHERE snapshot_path IS NOT NULL"
            ).fetchall()
            self._conn.execute("DELETE FROM shots")
            self._conn.execute("DELETE FROM sessions")
            self._conn.commit()
        return [r[0] for r in rows]

    def save_calibration(
        self,
        session_id: int,
        units_per_px: float | None,
        origin_px: tuple[float, float] | None,
        center_marked: bool = False,
    ) -> None:
        origin_x, origin_y = origin_px if origin_px is not None else (None, None)
        with self._lock:
            self._conn.execute(
                """UPDATE sessions
                   SET calib_units_per_px = ?, calib_origin_x = ?, calib_origin_y = ?,
                       calib_center_marked = ?
                   WHERE id = ?""",
                (units_per_px, origin_x, origin_y, int(center_marked), session_id),
            )
            self._conn.commit()

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
        self.update_many_shot_units(session_id, [(seq, x_units, y_units)])

    def update_many_shot_units(
        self, session_id: int, rows: list[tuple[int, float | None, float | None]]
    ) -> None:
        """Re-writes several shots' real-world units in ONE transaction.

        Recalibrating or marking the target center re-derives units for every
        shot in the session; doing that a row at a time meant a separate
        commit (and so a separate fsync) per shot, which on a Pi's SD card
        turned a 30-shot session into a visibly slow "Mark Center". One
        commit for the batch keeps it instant.
        """
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE shots SET x_units = ?, y_units = ? WHERE session_id = ? AND seq = ?",
                [(x_units, y_units, session_id, seq) for seq, x_units, y_units in rows],
            )
            self._conn.commit()

    def set_shot_excluded(self, session_id: int, seq: int, excluded: bool) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE shots SET excluded = ? WHERE session_id = ? AND seq = ?",
                (int(excluded), session_id, seq),
            )
            self._conn.commit()
            return cur.rowcount > 0

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
                "SELECT seq, x_px, y_px, x_units, y_units, snapshot_path, is_test, created_at, "
                "excluded FROM shots WHERE session_id = ? ORDER BY seq ASC",
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
                "excluded": bool(r[8]),
            }
            for r in rows
        ]

    def list_sessions(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                """SELECT s.id, s.created_at, s.unit_name, s.distance_m, COUNT(sh.id), s.name,
                          s.rifle, s.scope, s.ammo
                   FROM sessions s LEFT JOIN shots sh ON sh.session_id = s.id
                   GROUP BY s.id ORDER BY s.created_at DESC, s.id DESC"""
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "created_at": r[1],
                "unit_name": r[2],
                "distance_m": r[3],
                "shot_count": r[4],
                "name": r[5],
                "rifle": r[6],
                "scope": r[7],
                "ammo": r[8],
            }
            for r in rows
        ]

    def get_session(self, session_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT id, created_at, unit_name, distance_m, name,
                          calib_units_per_px, calib_origin_x, calib_origin_y,
                          calib_center_marked, rifle, scope, ammo
                   FROM sessions WHERE id = ?""",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "created_at": row[1],
            "unit_name": row[2],
            "distance_m": row[3],
            "name": row[4],
            "calib_units_per_px": row[5],
            "calib_origin_x": row[6],
            "calib_origin_y": row[7],
            "calib_center_marked": bool(row[8]),
            "rifle": row[9],
            "scope": row[10],
            "ammo": row[11],
        }

    def latest_session_id(self) -> int | None:
        """Most recently CREATED session, by timestamp rather than by id.

        Ids get reused once a session is deleted, so the highest id is no
        longer necessarily the newest session -- a reused low number can be
        the freshest one. Startup resume depends on this, and picking by
        MAX(id) would restore an older session instead.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM sessions ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
