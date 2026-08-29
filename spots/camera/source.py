"""Frame sources for the detection worker.

FrameSource is the seam between "where video comes from" and everything
downstream. RtspFrameSource pulls the real Z CAM feed; SyntheticFrameSource
fabricates a paper target (holes placed manually via add_hole(), e.g. from a
dashboard click) so the detection pipeline and dashboard can be
developed/tested without any hardware.
"""
from __future__ import annotations

import abc
import logging
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
        """Returns the most recent BGR frame, or None if none is available yet.

        Implementations MUST return an array the caller owns outright, never
        a view of an internal buffer that a later frame will overwrite.
        Callers rely on this to draw overlays in place without copying first
        (see the MJPEG stream), so handing back shared memory would let one
        consumer's annotations bleed into another's frame.
        """


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


class ZoomFrameSource(FrameSource):
    """Wraps another FrameSource with software (crop + resize) zoom.

    For a fixed camera whose lens can't get physically close enough to fill
    the frame with the target, this crops around a pan center and rescales
    back to the original frame size -- so everything downstream (detection,
    calibration, display) keeps operating in the same pixel dimensions and
    doesn't need to know zoom exists. At level 1.0 this is a pure passthrough.

    Changing zoom/pan while a session is active invalidates the current
    reference frame and calibration the same way physically moving the
    camera would -- callers should prompt for New Target + re-calibration.
    """

    def __init__(self, inner: FrameSource, level: float = 1.0, center_x: float = 0.5, center_y: float = 0.5):
        self._inner = inner
        self._level = max(1.0, level)
        self._center_x = min(max(center_x, 0.0), 1.0)
        self._center_y = min(max(center_y, 0.0), 1.0)

    def __getattr__(self, name):
        # Falls through to the wrapped source for anything this wrapper
        # doesn't itself define -- e.g. SyntheticFrameSource.reset_target(),
        # a dev/test-only hook that DetectionWorker calls via getattr(). Only
        # invoked when normal attribute lookup misses, so it never shadows
        # the methods defined below.
        return getattr(self._inner, name)

    def set_zoom(self, level: float, center_x: float, center_y: float) -> None:
        self._level = max(1.0, level)
        self._center_x = min(max(center_x, 0.0), 1.0)
        self._center_y = min(max(center_y, 0.0), 1.0)

    def get_zoom(self) -> tuple[float, float, float]:
        return self._level, self._center_x, self._center_y

    def start(self) -> None:
        self._inner.start()

    def stop(self) -> None:
        self._inner.stop()

    def get_latest_frame(self) -> np.ndarray | None:
        frame = self._inner.get_latest_frame()
        if frame is None or self._level <= 1.0:
            return frame

        height, width = frame.shape[:2]
        crop_w = max(1, int(width / self._level))
        crop_h = max(1, int(height / self._level))
        cx = int(self._center_x * width)
        cy = int(self._center_y * height)
        x0 = min(max(0, cx - crop_w // 2), width - crop_w)
        y0 = min(max(0, cy - crop_h // 2), height - crop_h)

        cropped = frame[y0 : y0 + crop_h, x0 : x0 + crop_w]
        return cv2.resize(cropped, (width, height), interpolation=cv2.INTER_LINEAR)


class SyntheticFrameSource(FrameSource):
    """Fabricates a paper target for local development/testing without a
    real camera or Pi. Holes only appear when placed manually (e.g. a
    dashboard click via add_hole()) -- this does not spawn any on its own.
    """

    _HOLE_RADIUS_PX = 8
    # Number of distinct pre-generated dither patterns cycled through. They
    # come from overlapping row-offset *views* of one oversized buffer, so
    # this costs one frame of memory rather than _DITHER_FRAMES of it.
    _DITHER_FRAMES = 8
    _DITHER_AMPLITUDE = 3

    def __init__(self, width: int = 1920, height: int = 1080, seed: int = 42):
        self._width = width
        self._height = height
        self._center = (width // 2, height // 2)
        self._target_radius = int(min(width, height) * 0.35)
        self._holes: list[tuple[int, int]] = []
        self._lock = threading.Lock()
        # A fixed speckle texture baked in once, so ORB has stable features
        # to match frame-to-frame. Fresh per-frame noise (as this used to
        # add) erases any consistent texture entirely -- a real camera's
        # scene detail stays put; only a much smaller sensor-noise dither
        # actually varies frame to frame.
        self._base = self._build_base_frame(seed)
        # Base with the current holes already drawn in, rebuilt only when the
        # hole list changes rather than re-drawn every single frame.
        self._composite = self._base
        # Generating a fresh full-resolution gaussian per frame cost ~68 ms
        # at 1080p -- with the MJPEG stream and the detector both pulling
        # frames that alone was more than a Pi core. Pre-generate the noise
        # once as int8 and cycle row-offset views of it instead (~2 ms).
        self._dither = np.random.default_rng(seed).integers(
            -self._DITHER_AMPLITUDE,
            self._DITHER_AMPLITUDE + 1,
            (height + self._DITHER_FRAMES, width, 3),
            dtype=np.int8,
        )
        self._dither_index = 0

    def _build_base_frame(self, seed: int) -> np.ndarray:
        frame = np.full((self._height, self._width, 3), 235, dtype=np.uint8)
        cx, cy = self._center
        for radius, color in [
            (self._target_radius, (40, 40, 40)),
            (int(self._target_radius * 0.7), (235, 235, 235)),
            (int(self._target_radius * 0.45), (40, 40, 40)),
            (int(self._target_radius * 0.2), (235, 235, 235)),
        ]:
            cv2.circle(frame, (cx, cy), radius, color, thickness=-1)
        speckle = np.random.default_rng(seed).normal(0, 6, frame.shape)
        return np.clip(frame.astype(np.int16) + speckle, 0, 255).astype(np.uint8)

    def start(self) -> None:
        pass

    def _rebuild_composite_locked(self) -> None:
        composite = self._base.copy()
        for x, y in self._holes:
            cv2.circle(composite, (x, y), self._HOLE_RADIUS_PX, (10, 10, 10), thickness=-1)
        self._composite = composite

    def reset_target(self) -> None:
        with self._lock:
            self._holes.clear()
            self._composite = self._base

    def add_hole(self, x: int, y: int) -> None:
        """Manually places a hole (e.g. from a dashboard click), so the
        detection pipeline picks it up as a shot next sample cycle.
        """
        with self._lock:
            self._holes.append((int(x), int(y)))
            self._rebuild_composite_locked()
        logger.info("Synthetic source: hole placed at (%d, %d)", x, y)

    def get_latest_frame(self) -> np.ndarray | None:
        with self._lock:
            composite = self._composite
            offset = self._dither_index
            self._dither_index = (self._dither_index + 1) % self._DITHER_FRAMES

        # Small dither over the fixed base -- well below diff_threshold, so
        # it doesn't trigger false contours, but enough to look like a live
        # feed rather than a static image. cv2.add saturates at 0/255 rather
        # than wrapping, so no separate clip pass is needed, and it returns a
        # new array so `composite` itself is never mutated.
        return cv2.add(
            composite, self._dither[offset : offset + self._height], dtype=cv2.CV_8U
        )

    def stop(self) -> None:
        pass


class SwitchableFrameSource(FrameSource):
    """Live-toggles between the synthetic test target and a real camera
    without restarting the app.

    The Z CAM connection is made lazily, on first switch to "zcam" -- so a
    synthetic-only dev setup with no camera configured/reachable never
    attempts one at startup. Once connected it's kept alive so toggling back
    and forth afterward is instant, not a fresh reconnect each time.

    Switching feeds changes every pixel in the frame, exactly like swapping
    cameras or moving one would -- it invalidates the current reference
    frame and calibration; callers should prompt for New Target + re-calibration.
    """

    def __init__(self, synthetic: SyntheticFrameSource, zcam_factory):
        self._synthetic = synthetic
        self._zcam_factory = zcam_factory  # () -> (FrameSource, ZCamClient), raises on failure
        self._zcam_source: FrameSource | None = None
        self._zcam_client = None
        self._active = "synthetic"
        self._lock = threading.Lock()

    def __getattr__(self, name):
        # Falls through to whichever source is CURRENTLY ACTIVE for anything
        # not defined here -- e.g. SyntheticFrameSource.reset_target()/
        # add_hole(), which should only ever apply while synthetic is active.
        source = self._zcam_source if self._active == "zcam" else self._synthetic
        if source is None:
            raise AttributeError(name)
        return getattr(source, name)

    def start(self) -> None:
        # The synthetic source has no background work (start()/stop() are
        # no-ops), so switching to it is always instant. The Z CAM source is
        # started separately, in switch_to(), the moment it's first connected.
        self._synthetic.start()

    def stop(self) -> None:
        self._synthetic.stop()
        if self._zcam_source is not None:
            self._zcam_source.stop()

    def get_active(self) -> str:
        return self._active

    def get_zcam_client(self):
        return self._zcam_client

    def switch_to(self, target: str) -> None:
        if target not in ("synthetic", "zcam"):
            raise ValueError(f"Unknown feed target: {target!r} (expected 'synthetic' or 'zcam')")
        with self._lock:
            if target == "zcam" and self._zcam_source is None:
                self._zcam_source, self._zcam_client = self._zcam_factory()
                self._zcam_source.start()
            self._active = target

    def get_latest_frame(self) -> np.ndarray | None:
        source = self._zcam_source if self._active == "zcam" else self._synthetic
        return source.get_latest_frame() if source is not None else None
