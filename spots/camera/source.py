"""Frame sources for the detection worker.

FrameSource is the seam between where video comes from and everything
downstream. RtspFrameSource pulls the real Z CAM feed; SyntheticFrameSource
fabricates a paper target so the rest can be developed without hardware.
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

        Implementations MUST return an array the caller owns outright, not
        a view of a buffer a later frame overwrites: callers draw overlays
        straight into it, so shared memory would bleed between consumers.
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

    Crops around a pan centre and rescales to the original frame size, so
    nothing downstream needs to know zoom exists. 1.0 is a passthrough.

    Changing zoom or pan mid-session invalidates the reference frame and
    calibration exactly as moving the camera would.
    """

    def __init__(self, inner: FrameSource, level: float = 1.0, center_x: float = 0.5, center_y: float = 0.5):
        self._inner = inner
        self._level = max(1.0, level)
        self._center_x = min(max(center_x, 0.0), 1.0)
        self._center_y = min(max(center_y, 0.0), 1.0)

    def __getattr__(self, name):
        # Falls through to the wrapped source for anything not defined
        # here, e.g. SyntheticFrameSource.reset_target(). Only runs when
        # normal lookup misses, so it never shadows the methods below.
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

    Two modes. "simple" is the original: flat rings, black discs for holes,
    a still frame. "realistic" is the one worth trusting -- a paper sheet
    pinned in front of a berm, so a hole is a torn edge with the ground
    showing through rather than a black disc, and the sheet moves in the
    wind while the ground behind it does not. Those are the two things the
    detector actually has to cope with outdoors, and the simple mode gives
    it neither.
    """

    MODES = ("simple", "realistic")

    _HOLE_RADIUS_PX = 8
    # Dither patterns cycled through. They're row-offset views of one
    # oversized buffer, so this costs one frame of memory, not this many.
    _DITHER_FRAMES = 8
    _DITHER_AMPLITUDE = 3
    # Wind. Amplitude is in pixels of sheet movement, well beyond what
    # re-alignment can ignore, so it has to actually do its job.
    _SWAY_PX = 6.0
    _SWAY_DEG = 0.35
    _SWAY_PERIOD = 47          # frames per cycle; prime, so x and y drift apart

    def __init__(self, width: int = 1920, height: int = 1080, seed: int = 42,
                 mode: str = "simple"):
        self._width = width
        self._height = height
        self._center = (width // 2, height // 2)
        self._target_radius = int(min(width, height) * 0.35)
        self._holes: list[tuple[int, int]] = []
        self._lock = threading.Lock()
        self._mode = mode if mode in self.MODES else "simple"
        self._seed = seed
        self._frame_index = 0

        # Baked in once so ORB has stable features to match. Regenerating
        # it per frame would leave no consistent texture at all, which a
        # real camera's scene detail always has.
        self._base = self._build_base_frame(seed)
        # Base with the current holes already drawn in, rebuilt only when the
        # hole list changes rather than re-drawn every single frame.
        self._composite = self._base

        # Realistic mode keeps the scene in two layers: the ground, which
        # stays put, and the paper, which sways and has holes torn in it.
        self._backing = self._build_backing(seed)
        self._sheet = self._build_sheet(seed)
        self._sheet_alpha = self._build_sheet_alpha()
        self._roi = self._sheet_roi()
        self._realistic_composite = None

        # A fresh 1080p gaussian per frame cost ~68 ms, more than a Pi core
        # once the stream and detector were both pulling. Pre-generating it
        # once and cycling row-offset views costs ~2 ms.
        self._dither = np.random.default_rng(seed).integers(
            -self._DITHER_AMPLITUDE,
            self._DITHER_AMPLITUDE + 1,
            (height + self._DITHER_FRAMES, width, 3),
            dtype=np.int8,
        )
        self._dither_index = 0

    # ---- mode ---------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> str:
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}")
        with self._lock:
            self._mode = mode
        logger.info("Synthetic source: %s target", mode)
        return mode

    # ---- the simple scene ---------------------------------------------

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

    # ---- the realistic scene -------------------------------------------

    def _sheet_half(self) -> int:
        return int(self._target_radius * 1.25)

    def _sheet_roi(self) -> tuple[int, int, int, int]:
        """Bounding box of the sheet plus room to sway, so only that part of
        the frame is warped and composited each time."""
        half = self._sheet_half() + int(self._SWAY_PX * 3) + 8
        cx, cy = self._center
        x0 = max(0, cx - half)
        y0 = max(0, cy - half)
        x1 = min(self._width, cx + half)
        y1 = min(self._height, cy + half)
        return x0, y0, x1, y1

    def _build_backing(self, seed: int) -> np.ndarray:
        """The ground behind the target: what shows through a hole."""
        rng = np.random.default_rng(seed + 1)
        # A dull earth gradient, darker low down, with coarse mottling. It
        # matters that this is neither black nor flat -- a hole reads as a
        # patch of ground, which is the whole point.
        rows = np.linspace(105, 62, self._height, dtype=np.float32)[:, None]
        base = np.repeat(rows, self._width, axis=1)
        coarse = rng.normal(0, 26, (self._height // 24 + 1, self._width // 24 + 1))
        coarse = cv2.resize(coarse, (self._width, self._height), interpolation=cv2.INTER_CUBIC)
        grey = np.clip(base + coarse + rng.normal(0, 4, base.shape), 20, 190)
        frame = np.stack([grey * 0.82, grey * 0.94, grey * 1.06], axis=-1)
        return np.clip(frame, 0, 255).astype(np.uint8)

    def _build_sheet(self, seed: int) -> np.ndarray:
        """The printed paper face, on its own layer so it can move."""
        frame = np.full((self._height, self._width, 3), 238, dtype=np.uint8)
        cx, cy = self._center
        for radius, color in [
            (self._target_radius, (48, 46, 44)),
            (int(self._target_radius * 0.7), (238, 238, 236)),
            (int(self._target_radius * 0.45), (48, 46, 44)),
            (int(self._target_radius * 0.2), (238, 238, 236)),
        ]:
            cv2.circle(frame, (cx, cy), radius, color, thickness=-1)
        speckle = np.random.default_rng(seed).normal(0, 5, frame.shape)
        return np.clip(frame.astype(np.int16) + speckle, 0, 255).astype(np.uint8)

    def _build_sheet_alpha(self) -> np.ndarray:
        """255 where paper covers the ground, 0 elsewhere -- so the sheet is
        a sheet, with an edge, rather than a full-frame backdrop."""
        alpha = np.zeros((self._height, self._width), dtype=np.uint8)
        cx, cy = self._center
        half = self._sheet_half()
        cv2.rectangle(alpha, (cx - half, cy - half), (cx + half, cy + half), 255, -1)
        return alpha

    def _punch(self, alpha: np.ndarray, sheet: np.ndarray, x: int, y: int, rng) -> None:
        """Tear a hole: a bruised rim on the paper, and the paper gone in the
        middle so the ground shows through."""
        radius = self._HOLE_RADIUS_PX
        angles = np.linspace(0, 2 * np.pi, 14, endpoint=False)
        jitter = rng.uniform(0.82, 1.18, angles.shape)
        outer = np.stack([
            x + np.cos(angles) * radius * 1.35 * jitter,
            y + np.sin(angles) * radius * 1.35 * jitter,
        ], axis=-1).astype(np.int32)
        inner = np.stack([
            x + np.cos(angles) * radius * jitter,
            y + np.sin(angles) * radius * jitter,
        ], axis=-1).astype(np.int32)
        # Bullet wipe: a dark smudge on the paper around the tear.
        cv2.fillPoly(sheet, [outer], (58, 56, 54))
        cv2.fillPoly(alpha, [inner], 0)

    def _rebuild_realistic_locked(self) -> None:
        sheet = self._sheet.copy()
        alpha = self._sheet_alpha.copy()
        rng = np.random.default_rng(self._seed + 7)
        for x, y in self._holes:
            self._punch(alpha, sheet, x, y, rng)
        x0, y0, x1, y1 = self._roi
        self._realistic_composite = (sheet[y0:y1, x0:x1], alpha[y0:y1, x0:x1])

    def _sway_matrix(self, index: int) -> np.ndarray:
        phase = 2 * np.pi * index / self._SWAY_PERIOD
        dx = np.sin(phase) * self._SWAY_PX
        dy = np.sin(phase * 0.6 + 1.1) * self._SWAY_PX * 0.5
        angle = np.sin(phase * 0.37) * self._SWAY_DEG
        x0, y0, x1, y1 = self._roi
        centre = ((x1 - x0) / 2.0, (y1 - y0) / 2.0)
        matrix = cv2.getRotationMatrix2D(centre, angle, 1.0)
        matrix[0, 2] += dx
        matrix[1, 2] += dy
        return matrix

    def _realistic_frame(self, index: int) -> np.ndarray:
        if self._realistic_composite is None:
            self._rebuild_realistic_locked()
        sheet_roi, alpha_roi = self._realistic_composite
        x0, y0, x1, y1 = self._roi
        matrix = self._sway_matrix(index)
        size = (x1 - x0, y1 - y0)
        # One warp of each layer, over the sheet's bounding box only: the
        # ground does not move, so most of the frame is a straight copy.
        moved_sheet = cv2.warpAffine(sheet_roi, matrix, size, flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_CONSTANT)
        moved_alpha = cv2.warpAffine(alpha_roi, matrix, size, flags=cv2.INTER_NEAREST,
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        frame = self._backing.copy()
        window = frame[y0:y1, x0:x1]
        cv2.copyTo(moved_sheet, moved_alpha, window)
        return frame

    # ---- the FrameSource contract ---------------------------------------

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
            self._realistic_composite = None

    def add_hole(self, x: int, y: int) -> None:
        """Manually places a hole (e.g. from a dashboard click), so the
        detection pipeline picks it up as a shot next sample cycle.
        """
        with self._lock:
            self._holes.append((int(x), int(y)))
            self._rebuild_composite_locked()
            self._realistic_composite = None
        logger.info("Synthetic source: hole placed at (%d, %d)", x, y)

    def get_latest_frame(self) -> np.ndarray | None:
        with self._lock:
            mode = self._mode
            offset = self._dither_index
            self._dither_index = (self._dither_index + 1) % self._DITHER_FRAMES
            index = self._frame_index
            self._frame_index += 1
            if mode == "realistic":
                frame = self._realistic_frame(index)
            else:
                frame = self._composite

        # Dither well below diff_threshold: enough to look live, not enough
        # to trigger contours. cv2.add saturates instead of wrapping and
        # returns a new array, so no clip pass and no mutation of the base.
        return cv2.add(
            frame, self._dither[offset : offset + self._height], dtype=cv2.CV_8U
        )

    def stop(self) -> None:
        pass


class SwitchableFrameSource(FrameSource):
    """Live-toggles between the synthetic test target and a real camera
    without restarting the app.

    The Z CAM connects lazily on the first switch to it, so a dev setup
    with no camera never tries at startup, and is then kept alive so
    toggling back and forth is instant.

    Switching feeds changes every pixel, so it invalidates the reference
    frame and calibration.
    """

    def __init__(self, synthetic: SyntheticFrameSource, zcam_factory):
        self._synthetic = synthetic
        self._zcam_factory = zcam_factory  # () -> (FrameSource, ZCamClient), raises on failure
        self._zcam_source: FrameSource | None = None
        self._zcam_client = None
        self._active = "synthetic"
        self._lock = threading.Lock()

    def __getattr__(self, name):
        # Falls through to whichever source is active, so synthetic-only
        # hooks like add_hole() can't reach a real camera.
        source = self._zcam_source if self._active == "zcam" else self._synthetic
        if source is None:
            raise AttributeError(name)
        return getattr(source, name)

    def start(self) -> None:
        # Synthetic has no background work, so switching to it is instant.
        # The Z CAM source is started in switch_to() on first connect.
        self._synthetic.start()

    def stop(self) -> None:
        self._synthetic.stop()
        if self._zcam_source is not None:
            self._zcam_source.stop()

    def get_active(self) -> str:
        return self._active

    def get_synthetic_mode(self) -> str:
        return self._synthetic.mode

    def set_synthetic_mode(self, mode: str) -> str:
        """Reaches the synthetic source directly rather than through the
        fall-through above, which follows whichever source is active -- the
        target can be reconfigured while the live feed is up."""
        return self._synthetic.set_mode(mode)

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
