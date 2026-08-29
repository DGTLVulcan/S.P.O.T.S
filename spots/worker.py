"""Background thread wiring FrameSource -> ShotDetector -> Storage, and the
thread-safe SessionState the Flask routes read from.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field

import cv2
import numpy as np

from spots.camera.source import FrameSource
from spots.config import DetectionConfig, TargetConfig
from spots.storage import Storage
from spots.vision.calibration import Calibration
from spots.vision.detection import ShotDetector, invert_homography, warp_point
from spots.vision.groups import GroupStats, compute_group_stats

logger = logging.getLogger(__name__)


@dataclass
class ShotRecord:
    seq: int
    x_px: float
    y_px: float
    x_units: float | None
    y_units: float | None
    snapshot_path: str | None = None


@dataclass
class _State:
    session_id: int | None = None
    calibration: Calibration | None = None
    shots: list[ShotRecord] = field(default_factory=list)
    stats: GroupStats | None = None


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
            )

    def reset(self, session_id: int, calibration: Calibration | None) -> None:
        with self._lock:
            self._state = _State(session_id=session_id, calibration=calibration)

    def set_calibration(self, calibration: Calibration | None) -> None:
        with self._lock:
            self._state.calibration = calibration

    def add_shot(self, record: ShotRecord, unit_name: str) -> None:
        with self._lock:
            self._state.shots.append(record)
            points = [
                (s.x_units, s.y_units) for s in self._state.shots if s.x_units is not None
            ]
            self._state.stats = compute_group_stats(points, unit_name) if points else None

    def undo_last(self, unit_name: str) -> None:
        with self._lock:
            if self._state.shots:
                self._state.shots.pop()
            points = [
                (s.x_units, s.y_units) for s in self._state.shots if s.x_units is not None
            ]
            self._state.stats = compute_group_stats(points, unit_name) if points else None


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
        self._sample_interval_s = 1.0 / max(detection_config.sample_fps, 0.1)
        self._detector = ShotDetector(detection_config)
        self.state = SessionState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._frame_source.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._sample_interval_s):
            frame = self._frame_source.get_latest_frame()
            if frame is None:
                continue
            for shot in self._detector.process_frame(frame):
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
            ShotRecord(seq, x_px, y_px, x_units, y_units, snapshot_path),
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

    def new_target(self, calibration: Calibration | None = None) -> None:
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
        session_id = self._storage.new_session(self._target_config.unit_name)
        self.state.reset(session_id, calibration)

    def set_calibration(self, calibration: Calibration) -> None:
        self.state.set_calibration(calibration)

    def undo_last(self) -> None:
        self._detector.undo_last()
        state = self.state.snapshot()
        if state.session_id is not None:
            self._storage.delete_last_shot(state.session_id)
        self.state.undo_last(self._target_config.unit_name)

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
