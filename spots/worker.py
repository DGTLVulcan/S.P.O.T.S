"""Background thread wiring FrameSource -> ShotDetector -> Storage, and the
thread-safe SessionState the Flask routes read from.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from spots.camera.source import FrameSource
from spots.config import DetectionConfig, TargetConfig
from spots.storage import Storage
from spots.vision.calibration import Calibration
from spots.vision.detection import ShotDetector, invert_homography, warp_point
from spots.vision.groups import _UNIT_TO_METERS, GroupStats, compute_group_stats

logger = logging.getLogger(__name__)

# New Target refuses to start a session until the distance is set above
# this -- catches an unset/forgotten distance before shots get recorded
# against it, rather than silently defaulting to "no MOA."
_MIN_DISTANCE_M = 10.0


@dataclass
class ShotRecord:
    seq: int
    x_px: float
    y_px: float
    x_units: float | None
    y_units: float | None
    snapshot_path: str | None = None
    is_test: bool = False
    excluded: bool = False
    created_at: float = 0.0


@dataclass
class _State:
    session_id: int | None = None
    calibration: Calibration | None = None
    shots: list[ShotRecord] = field(default_factory=list)
    stats: GroupStats | None = None
    distance_m: float = 0.0


class SessionState:
    """Thread-safe snapshot of the current session, read by Flask routes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = _State()

    def snapshot(self) -> _State:
        with self._lock:
            return _State(
                session_id=self._state.session_id,
                calibration=self._state.calibration,
                shots=list(self._state.shots),
                stats=self._state.stats,
                distance_m=self._state.distance_m,
            )

    def reset(self, session_id: int, calibration: Calibration | None, distance_m: float) -> None:
        with self._lock:
            self._state = _State(session_id=session_id, calibration=calibration, distance_m=distance_m)

    def set_distance(self, distance_m: float) -> None:
        with self._lock:
            self._state.distance_m = distance_m

    def set_calibration(self, calibration: Calibration | None, unit_name: str) -> None:
        with self._lock:
            self._state.calibration = calibration
            self._recompute_units_locked(unit_name)

    def set_origin(self, origin_px: tuple[float, float], unit_name: str) -> bool:
        """Updates just the origin (target center) of the CURRENT calibration,
        leaving its scale (units_per_px) untouched. Returns False if there's
        no calibration to update yet (caller should ask for scale calibration
        first -- an origin alone can't convert pixels to real-world units).
        """
        with self._lock:
            if self._state.calibration is None:
                return False
            self._state.calibration.origin_px = origin_px
            self._state.calibration.origin_is_target_center = True
            self._recompute_units_locked(unit_name)
            return True

    def _stats_points_locked(self) -> list[tuple[float, float]]:
        """Shots that count toward the group: calibrated, and not excluded.
        Excluded shots stay in the list (and on the target) but are left out
        of every statistic.
        """
        return [
            (s.x_units, s.y_units)
            for s in self._state.shots
            if s.x_units is not None and not s.excluded
        ]

    def _recompute_stats_locked(self, unit_name: str) -> None:
        points = self._stats_points_locked()
        self._state.stats = compute_group_stats(points, unit_name) if points else None

    def set_excluded(self, seq: int, excluded: bool, unit_name: str) -> bool:
        with self._lock:
            for shot in self._state.shots:
                if shot.seq == seq:
                    shot.excluded = excluded
                    self._recompute_stats_locked(unit_name)
                    return True
            return False

    def _recompute_units_locked(self, unit_name: str) -> None:
        """Re-derives every recorded shot's x_units/y_units from its stored
        pixel position under the CURRENT calibration. Without this, changing
        calibration (scale or origin) mid-session would leave already-shown
        shots computed against the old one -- inconsistent with new shots
        and with the origin the diagram/overlay now draws around.
        """
        cal = self._state.calibration
        for shot in self._state.shots:
            if cal is not None:
                shot.x_units, shot.y_units = cal.to_units((shot.x_px, shot.y_px))
            else:
                shot.x_units, shot.y_units = None, None
        self._recompute_stats_locked(unit_name)

    def add_shot(self, record: ShotRecord, unit_name: str) -> None:
        with self._lock:
            self._state.shots.append(record)
            self._recompute_stats_locked(unit_name)

    def undo_last(self, unit_name: str) -> None:
        with self._lock:
            if self._state.shots:
                self._state.shots.pop()
            self._recompute_stats_locked(unit_name)

    def delete_shot(self, seq: int, unit_name: str) -> bool:
        with self._lock:
            before = len(self._state.shots)
            self._state.shots = [s for s in self._state.shots if s.seq != seq]
            if len(self._state.shots) == before:
                return False
            self._recompute_stats_locked(unit_name)
            return True


class DetectionWorker:
    def __init__(
        self,
        frame_source: FrameSource,
        storage: Storage,
        target_config: TargetConfig,
        detection_config: DetectionConfig,
        snapshot_dir: str,
    ):
        self._frame_source = frame_source
        self._storage = storage
        self._target_config = target_config
        self._snapshot_dir = snapshot_dir
        self._detection_config = detection_config
        self._sample_interval_s = 1.0 / max(detection_config.sample_fps, 0.1)
        self._detector = ShotDetector(detection_config)
        self.state = SessionState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Set by resume_last_session() when a restored session still needs a
        # reference frame; the worker loop arms the detector as soon as one
        # is available, since at startup the camera may not have produced a
        # frame yet.
        self._pending_rearm_seq: int | None = None
        # Bullet diameter of the selected ammo, in mm. With this and the
        # calibrated scale the expected hole size can be worked out, instead
        # of relying on pixel figures that only suit one framing.
        self._bullet_diameter_mm: float | None = None

    def start(self) -> None:
        self._frame_source.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def set_bullet_diameter_mm(self, diameter_mm: float | None) -> None:
        self._bullet_diameter_mm = diameter_mm if diameter_mm else None

    def hole_area_range(self) -> dict | None:
        """Expected hole size in the CURRENT view, from the bullet diameter
        and the calibrated scale.

        Returns None whenever it can't be worked out -- auto-sizing off, no
        calibration yet, no diameter recorded, or a unit with no fixed
        conversion to metres -- and the configured pixel figures are used
        instead. The window is generous either side of the expected area:
        holes tear larger than the bullet, and two touching holes merge into
        one blob that must still pass.
        """
        if not self._detection_config.auto_hole_area or not self._bullet_diameter_mm:
            return None
        calibration = self.state.snapshot().calibration
        if calibration is None or not calibration.units_per_px:
            return None
        metres_per_unit = _UNIT_TO_METERS.get(self._target_config.unit_name.strip().lower())
        if metres_per_unit is None:
            return None

        diameter_units = (self._bullet_diameter_mm / 1000.0) / metres_per_unit
        # units_per_px was measured in the current view, so this is already
        # in the pixels the detector actually sees, zoom included.
        diameter_px = diameter_units / calibration.units_per_px
        if diameter_px <= 0:
            return None
        area_px = math.pi * (diameter_px / 2.0) ** 2
        return {
            "diameter_px": diameter_px,
            "area_px": area_px,
            "min_area_px": max(4.0, area_px * 0.25),
            "max_area_px": area_px * 4.0,
        }

    def _run(self) -> None:
        while not self._stop.wait(self._sample_interval_s):
            frame = self._frame_source.get_latest_frame()
            if frame is None:
                continue
            if self._pending_rearm_seq is not None:
                # Re-baseline a resumed session against the target as it
                # looks now. Existing holes become part of the reference, so
                # only genuinely new impacts register from here -- the same
                # trick burn-in uses for tight groups.
                self._detector.reset(frame, next_seq=self._pending_rearm_seq)
                logger.info(
                    "Resumed session armed; detection continues from shot #%d",
                    self._pending_rearm_seq,
                )
                self._pending_rearm_seq = None
                continue
            zoom_level, _, _ = self.get_zoom()
            derived = self.hole_area_range()
            area_range = (
                (derived["min_area_px"], derived["max_area_px"]) if derived else None
            )
            for shot in self._detector.process_frame(
                frame, zoom_level=zoom_level, area_range=area_range
            ):
                self._commit_shot(shot.seq, shot.x_px, shot.y_px, frame)

    def _commit_shot(self, seq: int, x_px: float, y_px: float, frame_bgr: np.ndarray) -> None:
        state = self.state.snapshot()
        if state.session_id is None:
            logger.warning("Shot detected with no active session, dropping")
            return
        x_units = y_units = None
        if state.calibration is not None:
            x_units, y_units = state.calibration.to_units((x_px, y_px))
        # x_px/y_px are in the detector's anchor coordinate space; the raw
        # frame being saved is not warped, so map the marker back to it.
        homography_inv = invert_homography(self._detector.last_homography)
        marker_x, marker_y = warp_point((x_px, y_px), homography_inv)
        snapshot_path = self._save_snapshot(state.session_id, seq, marker_x, marker_y, frame_bgr)
        self._storage.add_shot(
            state.session_id, seq, x_px, y_px, x_units, y_units, snapshot_path
        )
        self.state.add_shot(
            ShotRecord(seq, x_px, y_px, x_units, y_units, snapshot_path, created_at=time.time()),
            self._target_config.unit_name,
        )
        logger.info("Committed shot #%d at px (%.1f, %.1f)", seq, x_px, y_px)

    def _save_snapshot(
        self, session_id: int, seq: int, marker_x: float, marker_y: float, frame_bgr: np.ndarray
    ) -> str | None:
        """Save a marked JPEG of the frame at commit time, so a false-positive
        detection can be visually confirmed later from session history.
        marker_x/marker_y must already be in frame_bgr's own coordinate space
        (i.e. re-alignment-corrected, not the detector's anchor space).
        Returns a path relative to the snapshot directory, or None on failure
        (never blocks shot recording -- the DB row is the source of truth).
        """
        try:
            session_dir = os.path.join(self._snapshot_dir, str(session_id))
            os.makedirs(session_dir, exist_ok=True)
            annotated = frame_bgr.copy()
            cv2.drawMarker(
                annotated,
                (int(marker_x), int(marker_y)),
                (0, 0, 255),
                cv2.MARKER_TILTED_CROSS,
                30,
                2,
            )
            filename = f"shot_{seq:03d}.jpg"
            cv2.imwrite(
                os.path.join(session_dir, filename), annotated, [cv2.IMWRITE_JPEG_QUALITY, 85]
            )
            return f"{session_id}/{filename}"
        except OSError:
            logger.exception("Failed to save snapshot for shot #%d", seq)
            return None

    def set_zoom(self, level: float, center_x: float, center_y: float) -> None:
        set_zoom = getattr(self._frame_source, "set_zoom", None)
        if callable(set_zoom):
            set_zoom(level, center_x, center_y)

    def get_zoom(self) -> tuple[float, float, float]:
        get_zoom = getattr(self._frame_source, "get_zoom", None)
        return get_zoom() if callable(get_zoom) else (1.0, 0.5, 0.5)

    def get_active_feed(self) -> str:
        get_active = getattr(self._frame_source, "get_active", None)
        return get_active() if callable(get_active) else "synthetic"

    def switch_feed(self, target: str) -> None:
        """Raises on failure (e.g. camera unreachable) -- caller's problem
        to report, not the worker's to swallow.
        """
        self._frame_source.switch_to(target)

    def get_zcam_client(self):
        get_client = getattr(self._frame_source, "get_zcam_client", None)
        return get_client() if callable(get_client) else None

    def add_simulated_hole(self, x: float, y: float) -> bool:
        """Places a virtual bullet hole for the synthetic source to render
        and the detector to pick up next cycle. Returns False (rather than
        raising) when the active feed isn't synthetic, since that's a
        routine "wrong mode" case the caller should just report cleanly.
        """
        add_hole = getattr(self._frame_source, "add_hole", None)
        if not callable(add_hole):
            return False
        add_hole(x, y)
        return True

    def new_target(self, calibration: Calibration | None = None, equipment: dict | None = None) -> None:
        if self._target_config.distance_m <= _MIN_DISTANCE_M:
            raise ValueError(
                f"Distance to target must be greater than {_MIN_DISTANCE_M} m before "
                "starting a new target"
            )
        # Dev/test-only hook: SyntheticFrameSource clears its fabricated holes
        # here, before the reference frame is captured, so the reference is
        # truly clean -- mirroring a real target being physically replaced
        # before the reference photo is taken. No-op for a real camera.
        reset_target = getattr(self._frame_source, "reset_target", None)
        if callable(reset_target):
            reset_target()
        frame = self._frame_source.get_latest_frame()
        if frame is None:
            raise RuntimeError("No frame available yet to set as reference")
        self._detector.reset(frame)
        # Drop any resume that hadn't been armed yet, or it would re-baseline
        # the detector over this fresh reference on the next loop.
        self._pending_rearm_seq = None
        equipment = equipment or {}
        session_id = self._storage.new_session(
            self._target_config.unit_name,
            self._target_config.distance_m,
            rifle=(equipment.get("rifle") or {}).get("name"),
            scope=(equipment.get("scope") or {}).get("name"),
            ammo=(equipment.get("ammo") or {}).get("name"),
            equipment_snapshot=equipment,
        )
        self.state.reset(session_id, calibration, self._target_config.distance_m)
        self._persist_calibration()

    def clear_session(self) -> None:
        """Detaches the dashboard from its current session without touching
        storage -- used when the session on screen has just been deleted.
        """
        self._pending_rearm_seq = None
        self.state.reset(None, None, self._target_config.distance_m)

    def resume_last_session(self) -> bool:
        """Restores the most recent session after a restart or Pi reboot.

        Shots, calibration and distance all live in SQLite already, but the
        in-memory state started empty on every launch, so a power blip
        mid-string left the dashboard blank and the calibration gone even
        though the data was on disk. Detection resumes into the *same*
        session (see _pending_rearm_seq) rather than forcing a New Target,
        which would have started a new one and split the string in two.
        """
        session_id = self._storage.latest_session_id()
        if session_id is None:
            return False
        session = self._storage.get_session(session_id)
        if session is None:
            return False

        calibration = None
        if session["calib_units_per_px"] is not None:
            calibration = Calibration(
                units_per_px=session["calib_units_per_px"],
                unit_name=session["unit_name"],
                origin_px=(session["calib_origin_x"], session["calib_origin_y"]),
                origin_is_target_center=session["calib_center_marked"],
            )

        distance_m = session["distance_m"] or 0.0
        self.state.reset(session_id, calibration, distance_m)
        # Keep the live config in step with the session being resumed, so
        # MOA and the New Target gate reflect what's actually loaded.
        self._target_config.distance_m = distance_m

        rows = self._storage.get_shots(session_id)
        for row in rows:
            self.state.add_shot(
                ShotRecord(
                    row["seq"],
                    row["x_px"],
                    row["y_px"],
                    row["x_units"],
                    row["y_units"],
                    row["snapshot_path"],
                    row["is_test"],
                    row["excluded"],
                    row["created_at"],
                ),
                self._target_config.unit_name,
            )

        next_seq = max((row["seq"] for row in rows), default=0) + 1
        self._pending_rearm_seq = next_seq
        logger.info(
            "Resumed session #%d with %d shot(s); detection will continue from #%d",
            session_id,
            len(rows),
            next_seq,
        )
        return True

    def set_calibration(self, calibration: Calibration | None) -> None:
        """Pass None to clear calibration entirely (scale + target-center
        origin) -- e.g. a "reset setup" action. Already-recorded shots keep
        their pixel positions but lose their real-world units until
        recalibrated, same as any other calibration change.
        """
        self.state.set_calibration(calibration, self._target_config.unit_name)
        self._persist_shot_units()
        self._persist_calibration()

    def set_distance(self, distance_m: float) -> None:
        """Updates the CURRENT session's distance (not just the config
        default for the next New Target), and persists it so session
        history reflects the correction too -- e.g. the target got moved,
        or the wrong distance was entered to begin with.
        """
        self._target_config.distance_m = distance_m
        self.state.set_distance(distance_m)
        snapshot = self.state.snapshot()
        if snapshot.session_id is not None:
            self._storage.update_session_distance(snapshot.session_id, distance_m)

    def mark_center(self, x_px: float, y_px: float) -> bool:
        """Sets the target's true center as the calibration origin, so shots
        are reported (and the target diagram drawn) relative to the actual
        bullseye rather than wherever the first calibration click landed.
        Returns False if scale hasn't been calibrated yet -- there's no
        Calibration object for an origin-only update to attach to.
        """
        ok = self.state.set_origin((x_px, y_px), self._target_config.unit_name)
        if ok:
            self._persist_shot_units()
            self._persist_calibration()
        return ok

    def add_test_shot(self, x_px: float, y_px: float) -> bool:
        """Manually records a shot at a clicked point on the live feed, for
        exercising calibration/stats/MOA without needing a real impact.
        Bypasses the detector entirely (there's nothing to diff against on
        real footage the way a synthetic hole can be drawn in) -- x_px/y_px
        are taken directly in the frame's own coordinate space, the same
        convention as a Calibrate or Mark Center click, not the detector's
        internal anchor space. Tagged is_test so it's never mistaken for a
        genuine detected impact when reviewing a session later.
        Returns False if there's no active session to record it against.
        """
        state = self.state.snapshot()
        if state.session_id is None:
            return False

        seq = self._detector.reserve_seq()
        x_units = y_units = None
        if state.calibration is not None:
            x_units, y_units = state.calibration.to_units((x_px, y_px))

        frame = self._frame_source.get_latest_frame()
        snapshot_path = None
        if frame is not None:
            snapshot_path = self._save_snapshot(state.session_id, seq, x_px, y_px, frame)

        self._storage.add_shot(
            state.session_id, seq, x_px, y_px, x_units, y_units, snapshot_path, is_test=True
        )
        self.state.add_shot(
            ShotRecord(
                seq, x_px, y_px, x_units, y_units, snapshot_path,
                is_test=True, created_at=time.time(),
            ),
            self._target_config.unit_name,
        )
        logger.info("Test shot #%d placed at px (%.1f, %.1f)", seq, x_px, y_px)
        return True

    def _persist_calibration(self) -> None:
        snapshot = self.state.snapshot()
        if snapshot.session_id is None:
            return
        cal = snapshot.calibration
        self._storage.save_calibration(
            snapshot.session_id,
            cal.units_per_px if cal else None,
            cal.origin_px if cal else None,
            cal.origin_is_target_center if cal else False,
        )

    def _persist_shot_units(self) -> None:
        snapshot = self.state.snapshot()
        if snapshot.session_id is None:
            return
        self._storage.update_many_shot_units(
            snapshot.session_id,
            [(shot.seq, shot.x_units, shot.y_units) for shot in snapshot.shots],
        )

    def undo_last(self) -> None:
        state = self.state.snapshot()
        if not state.shots:
            return
        # Only pop the detector's own committed-shot bookkeeping when the
        # last shot actually came from it -- a test shot never touched that
        # state (it borrows a seq number but bypasses detection entirely),
        # so popping it here would incorrectly discard the last REAL
        # detection instead of the test shot actually being undone.
        if not state.shots[-1].is_test:
            self._detector.undo_last()
        if state.session_id is not None:
            self._storage.delete_last_shot(state.session_id)
        self.state.undo_last(self._target_config.unit_name)

    def set_shot_excluded(self, seq: int, excluded: bool) -> bool:
        """Marks a shot as a flyer (or un-marks it): kept in the string and
        on the target, but left out of the group statistics. Returns False if
        no shot with that sequence number exists.
        """
        if not self.state.set_excluded(seq, excluded, self._target_config.unit_name):
            return False
        session_id = self.state.snapshot().session_id
        if session_id is not None:
            self._storage.set_shot_excluded(session_id, seq, excluded)
        return True

    def delete_shot(self, seq: int) -> bool:
        """Removes an arbitrary shot by sequence number, not just the last
        one. Deleting the last shot delegates to undo_last() so the
        detector's own bookkeeping (used for the *next* undo's chronological
        assumption) stays correct; removing an earlier shot doesn't need to
        touch it -- that bookkeeping never mattered for anything except "was
        the most recent commit a real one," which a mid-list removal doesn't
        change. Returns False if no shot with that seq exists.
        """
        state = self.state.snapshot()
        if not state.shots:
            return False
        if state.shots[-1].seq == seq:
            self.undo_last()
            return True
        removed = self.state.delete_shot(seq, self._target_config.unit_name)
        if removed and state.session_id is not None:
            self._storage.delete_shot(state.session_id, seq)
        return removed

    def get_latest_frame(self) -> np.ndarray | None:
        return self._frame_source.get_latest_frame()

    def get_last_homography(self) -> np.ndarray | None:
        """Current-frame -> anchor-frame transform from the detector's most
        recent successful re-alignment (None if disabled/unavailable).
        """
        return self._detector.last_homography

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._frame_source.stop()
