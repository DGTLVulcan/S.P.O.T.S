"""Session/shot persistence: id reuse, calibration round-trip, exclusion,
and migrating a database written by an older version."""

import os
import sys

# Import the app regardless of where the runner sets its working directory
# (Visual Studio Test Explorer, `python -m unittest`, and pytest all differ).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os
import shutil
import sqlite3
import tempfile
import time
import unittest

from spots.storage import Storage


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "test.db")
        self.storage = Storage(self.path)

    def tearDown(self):
        self.storage.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def ids(self):
        return sorted(s["id"] for s in self.storage.list_sessions())


class SessionIdReuseTests(StorageTestCase):
    def test_ids_start_at_one_and_increment(self):
        self.assertEqual([self.storage.new_session("cm") for _ in range(3)], [1, 2, 3])

    def test_deleting_the_highest_frees_it(self):
        for _ in range(3):
            self.storage.new_session("cm")
        self.storage.delete_session(3)
        self.assertEqual(self.storage.new_session("cm"), 3)

    def test_a_gap_in_the_middle_is_filled(self):
        for _ in range(3):
            self.storage.new_session("cm")
        self.storage.delete_session(2)
        self.assertEqual(self.storage.new_session("cm"), 2)

    def test_clearing_everything_restarts_at_one(self):
        for _ in range(5):
            self.storage.new_session("cm")
        for session_id in self.ids():
            self.storage.delete_session(session_id)
        self.assertEqual(self.storage.new_session("cm"), 1)

    def test_repeated_create_delete_never_climbs(self):
        for _ in range(30):
            session_id = self.storage.new_session("cm")
            self.assertEqual(session_id, 1)
            self.storage.delete_session(session_id)

    def test_latest_session_is_by_creation_not_by_id(self):
        """A reused low id can be the newest session, so resume-on-startup
        must not simply take MAX(id)."""
        self.storage.new_session("cm")           # 1
        middle = self.storage.new_session("cm")  # 2
        self.storage.new_session("cm")           # 3
        self.storage.delete_session(middle)
        time.sleep(0.01)
        newest = self.storage.new_session("cm")  # reuses id 2
        self.assertEqual(newest, 2)
        self.assertEqual(max(self.ids()), 3)
        self.assertEqual(self.storage.latest_session_id(), newest)
        self.assertEqual(self.storage.list_sessions()[0]["id"], newest)

    def test_reused_id_starts_with_no_shots(self):
        first = self.storage.new_session("cm")
        self.storage.add_shot(first, 1, 10, 10, 1.0, 1.0)
        self.storage.delete_session(first)
        reused = self.storage.new_session("cm")
        self.assertEqual(reused, first)
        self.assertEqual(self.storage.get_shots(reused), [])


class CalibrationPersistenceTests(StorageTestCase):
    def test_calibration_round_trips(self):
        session_id = self.storage.new_session("cm", 100.0)
        self.storage.save_calibration(session_id, 0.05, (960.0, 540.0), center_marked=True)
        session = self.storage.get_session(session_id)
        self.assertAlmostEqual(session["calib_units_per_px"], 0.05)
        self.assertAlmostEqual(session["calib_origin_x"], 960.0)
        self.assertAlmostEqual(session["calib_origin_y"], 540.0)
        self.assertTrue(session["calib_center_marked"])

    def test_center_marked_defaults_false(self):
        session_id = self.storage.new_session("cm", 100.0)
        self.storage.save_calibration(session_id, 0.05, (10.0, 20.0))
        self.assertFalse(self.storage.get_session(session_id)["calib_center_marked"])


class ShotTests(StorageTestCase):
    def test_exclusion_round_trips(self):
        session_id = self.storage.new_session("cm")
        self.storage.add_shot(session_id, 1, 10, 10, 1.0, 1.0)
        self.assertFalse(self.storage.get_shots(session_id)[0]["excluded"])
        self.assertTrue(self.storage.set_shot_excluded(session_id, 1, True))
        self.assertTrue(self.storage.get_shots(session_id)[0]["excluded"])
        self.storage.set_shot_excluded(session_id, 1, False)
        self.assertFalse(self.storage.get_shots(session_id)[0]["excluded"])

    def test_excluding_a_missing_shot_reports_false(self):
        session_id = self.storage.new_session("cm")
        self.assertFalse(self.storage.set_shot_excluded(session_id, 99, True))

    def test_shots_carry_a_timestamp(self):
        session_id = self.storage.new_session("cm")
        self.storage.add_shot(session_id, 1, 10, 10, 1.0, 1.0)
        self.assertGreater(self.storage.get_shots(session_id)[0]["created_at"], 0)

    def test_delete_session_returns_snapshot_paths(self):
        session_id = self.storage.new_session("cm")
        self.storage.add_shot(session_id, 1, 1, 1, None, None, snapshot_path="1/shot_001.jpg")
        self.storage.add_shot(session_id, 2, 2, 2, None, None)
        self.assertEqual(self.storage.delete_session(session_id), ["1/shot_001.jpg"])

    def test_delete_all_clears_everything(self):
        for _ in range(3):
            session_id = self.storage.new_session("cm")
            self.storage.add_shot(session_id, 1, 1, 1, None, None, snapshot_path=f"{session_id}/a.jpg")
        self.assertEqual(len(self.storage.delete_all_sessions()), 3)
        self.assertEqual(self.storage.list_sessions(), [])
        self.assertEqual(self.storage.new_session("cm"), 1)


class RenameTests(StorageTestCase):
    def test_rename_and_clear(self):
        session_id = self.storage.new_session("cm")
        self.assertTrue(self.storage.rename_session(session_id, "Bench rest 100m"))
        self.assertEqual(self.storage.get_session(session_id)["name"], "Bench rest 100m")
        self.storage.rename_session(session_id, "   ")
        self.assertIsNone(self.storage.get_session(session_id)["name"])

    def test_rename_missing_session(self):
        self.assertFalse(self.storage.rename_session(999, "nope"))


class LegacyMigrationTests(unittest.TestCase):
    """A database written before these columns existed must upgrade in place
    -- the Pi has one, and wiping it would lose recorded sessions."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "legacy.db")
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                unit_name TEXT NOT NULL);
            CREATE TABLE shots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL, seq INTEGER NOT NULL,
                x_px REAL NOT NULL, y_px REAL NOT NULL,
                x_units REAL, y_units REAL, created_at REAL NOT NULL);
            """
        )
        for _ in range(3):
            conn.execute(
                "INSERT INTO sessions (created_at, unit_name) VALUES (?, ?)", (time.time(), "cm")
            )
        conn.execute(
            "INSERT INTO shots (session_id, seq, x_px, y_px, x_units, y_units, created_at)"
            " VALUES (1, 1, 10, 20, 1.5, 2.5, ?)",
            (time.time(),),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_existing_rows_survive_and_new_columns_appear(self):
        storage = Storage(self.path)
        try:
            session = storage.get_session(1)
            self.assertIsNone(session["name"])
            self.assertIsNone(session["calib_units_per_px"])
            self.assertFalse(session["calib_center_marked"])
            shot = storage.get_shots(1)[0]
            self.assertAlmostEqual(shot["x_units"], 1.5)
            self.assertFalse(shot["excluded"])
        finally:
            storage.close()

    def test_ids_are_reused_despite_the_legacy_autoincrement(self):
        """AUTOINCREMENT would otherwise refuse to reuse a rowid, so the
        explicit id assignment has to override it without a table rebuild."""
        storage = Storage(self.path)
        try:
            for session_id in [s["id"] for s in storage.list_sessions()]:
                storage.delete_session(session_id)
            self.assertEqual(storage.new_session("cm"), 1)
        finally:
            storage.close()


if __name__ == "__main__":
    unittest.main()
