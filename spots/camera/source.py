"""Frame sources for the detection worker.

FrameSource is the seam between "where video comes from" and everything
downstream. RtspFrameSource pulls the real Z CAM feed; SyntheticFrameSource
fabricates a paper target with holes appearing over time so the detection
pipeline and dashboard can be developed/tested without any hardware.
"""
from __future__ import annotations

import abc
import logging
import random
import threading
import time

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class FrameSource(abc.ABC):
    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def stop(self) -> None: ...

    @abc.abstractmethod
    def get_latest_frame(self) -> np.ndarray | None:
        """Returns the most recent BGR frame, or None if none is available yet."""


class RtspFrameSource(FrameSource):
    """Pulls frames from an RTSP URL in a background thread, reconnecting on drop."""

    _RECONNECT_DELAY_S = 2.0

    def __init__(self, rtsp_url: str):
        self._url = rtsp_url
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self._url)
            if not cap.isOpened():
                logger.warning("Could not open RTSP stream %s, retrying...", self._url)
                cap.release()
                time.sleep(self._RECONNECT_DELAY_S)
                continue
            logger.info("RTSP stream opened: %s", self._url)
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    logger.warning("Lost RTSP stream, reconnecting...")
                    break
                with self._lock:
                    self._frame = frame
            cap.release()
            if not self._stop.is_set():
                time.sleep(self._RECONNECT_DELAY_S)

    def get_latest_frame(self) -> np.ndarray | None:
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)


class SyntheticFrameSource(FrameSource):
    """Fabricates a paper target and stamps in new "shots" over time.

    Used for local development and dashboard/detection testing without a
    real camera or Pi. Not a physics simulation -- just enough realism
    (sensor noise, a slow drift, occasional new holes) to exercise the
    detection pipeline end to end.
    """

    _NEW_HOLE_INTERVAL_S = 4.0
    _MAX_HOLES = 12

    def __init__(self, width: int = 1920, height: int = 1080, seed: int = 42):
        self._width = width
        self._height = height
        self._rng = random.Random(seed)
        self._center = (width // 2, height // 2)
        self._target_radius = int(min(width, height) * 0.35)
        self._holes: list[tuple[int, int]] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._NEW_HOLE_INTERVAL_S):
            with self._lock:
                if len(self._holes) >= self._MAX_HOLES:
                    continue
                # Cluster new shots around a group center that wanders a
                # little, similar to a real shooter's group.
                group_spread = self._target_radius * 0.12
                angle = self._rng.uniform(0, 2 * 3.14159)
                radius = self._rng.uniform(0, group_spread)
                x = int(self._center[0] + radius * np.cos(angle))
                y = int(self._center[1] + radius * np.sin(angle))
                self._holes.append((x, y))
                logger.info("Synthetic source: new shot at (%d, %d)", x, y)

    def reset_target(self) -> None:
        with self._lock:
            self._holes.clear()

    def get_latest_frame(self) -> np.ndarray | None:
        frame = np.full((self._height, self._width, 3), 235, dtype=np.uint8)
        cx, cy = self._center
        for i, (radius, color) in enumerate(
            [
                (self._target_radius, (40, 40, 40)),
                (int(self._target_radius * 0.7), (235, 235, 235)),
                (int(self._target_radius * 0.45), (40, 40, 40)),
                (int(self._target_radius * 0.2), (235, 235, 235)),
            ]
        ):
            cv2.circle(frame, (cx, cy), radius, color, thickness=-1)

        with self._lock:
            holes = list(self._holes)
        for x, y in holes:
            cv2.circle(frame, (x, y), 8, (10, 10, 10), thickness=-1)

        noise = np.random.default_rng().normal(0, 4, frame.shape).astype(np.int16)
        frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        return frame

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._NEW_HOLE_INTERVAL_S + 1.0)
