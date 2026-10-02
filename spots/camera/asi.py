"""ZWO ASI cameras over USB, through ZWO's ASICamera2 SDK.

The cameras are not UVC devices, so OpenCV cannot open them. The `zwoasi`
package wraps ZWO's libASICamera2, which is installed separately with its
udev rule (see scripts/install-asi-sdk.sh).

Exposure is held by this module rather than the camera's own auto mode:
the SDK documents ASI_AUTO_MAX_EXP as microseconds in one place and
milliseconds in another, while ASI_EXPOSURE is microseconds throughout.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

from spots.camera.source import FrameSource

logger = logging.getLogger(__name__)

# Mean brightness, 0-255, that auto exposure holds the picture at.
TARGET_LEVEL = 110.0
# How far from the target it may drift before exposure changes. Wide, so a
# settled picture stays put: every change is a global shift the detector
# has to absorb.
DEADBAND = 14.0


class AsiError(RuntimeError):
    """The camera, the SDK or its Python binding could not be used."""


def load_sdk(library_path: str = ""):
    """Imports zwoasi and loads libASICamera2, from `library_path` if given.

    Raises AsiError with what to install when either is missing.
    """
    try:
        import zwoasi
    except ImportError as exc:
        raise AsiError(
            "The zwoasi package isn't installed -- run "
            "'pip install -r requirements.txt'") from exc
    try:
        zwoasi.init(library_path or None)
    except Exception as exc:
        raise AsiError(
            "Could not load ZWO's libASICamera2"
            + (f" from {library_path}" if library_path else "")
            + f" ({exc}). Install the SDK with scripts/install-asi-sdk.sh.") from exc
    return zwoasi


def next_exposure_us(current_us: float, level: float, floor_us: float,
                     ceiling_us: float, target: float = TARGET_LEVEL,
                     deadband: float = DEADBAND) -> int:
    """The exposure that moves the picture's mean brightness toward `target`.

    Holds still inside the deadband. Outside it, steps by at most a factor
    of two, damped so it settles rather than hunting, and never leaves
    [floor_us, ceiling_us].
    """
    if abs(level - target) <= deadband:
        return int(round(current_us))
    ratio = 2.0 if level < 1.0 else target / level
    ratio = min(2.0, max(0.5, ratio))
    proposed = current_us * ratio ** 0.6
    return int(round(min(ceiling_us, max(floor_us, proposed))))


class AsiFrameSource(FrameSource):
    """Reads frames from the first connected ZWO ASI camera in a background
    thread, as full-resolution BGR, reconnecting if the camera drops.
    """

    _RECONNECT_DELAY_S = 2.0
    # A frame read longer ago than this means the camera has gone quiet.
    _STALE_S = 3.0
    # Exposure is reviewed every few frames, since a change takes a frame or
    # two to show up.
    _ADJUST_EVERY = 3

    def __init__(self, library_path: str = "", auto_exposure: bool = True,
                 exposure_ms: float = 2.0, max_exposure_ms: float = 20.0,
                 gain: int = 50, fps: float = 15.0):
        self._library_path = library_path
        self._auto = auto_exposure
        self._exposure_us = max(1.0, exposure_ms * 1000.0)
        self._max_us = max(1.0, max_exposure_ms * 1000.0)
        self._gain = int(gain)
        self._interval = 1.0 / max(1.0, fps)

        self._sdk = None
        self._camera = None
        self._name = ""
        self._floor_us = 32.0
        self._ceiling_us = self._max_us

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._frame_at = 0.0

    # ---- connection ---------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def connected(self) -> bool:
        return self._camera is not None and time.monotonic() - self._frame_at < self._STALE_S

    def _open(self) -> None:
        """Opens and configures the camera, raising AsiError if it can't."""
        sdk = self._sdk or load_sdk(self._library_path)
        self._sdk = sdk
        try:
            found = sdk.get_num_cameras()
        except Exception as exc:
            raise AsiError(f"Could not list ZWO ASI cameras: {exc}") from exc
        if found < 1:
            raise AsiError("No ZWO ASI camera found on USB -- check the cable, "
                           "and that the SDK's udev rule is installed")
        try:
            camera = sdk.Camera(0)
        except Exception as exc:
            raise AsiError(f"Could not open the ZWO ASI camera: {exc}") from exc
        try:
            self._name = camera.get_camera_property().get("Name", "ZWO ASI")
            exposure = camera.get_controls().get("Exposure", {})
            self._floor_us = float(max(32, exposure.get("MinValue", 32)))
            self._ceiling_us = float(min(self._max_us, exposure.get("MaxValue", self._max_us)))
            self._exposure_us = min(self._ceiling_us, max(self._floor_us, self._exposure_us))

            # Full frame, unbinned, debayered by the SDK into BGR -- the
            # byte order OpenCV uses, so frames need no conversion.
            camera.set_roi(bins=1, image_type=sdk.ASI_IMG_RGB24)
            camera.set_control_value(sdk.ASI_GAIN, self._gain)
            camera.set_control_value(sdk.ASI_EXPOSURE, int(self._exposure_us))
            camera.start_video_capture()
        except Exception as exc:
            try:
                camera.close()
            except Exception:
                pass
            raise AsiError(f"Could not configure the ZWO ASI camera: {exc}") from exc
        self._camera = camera
        logger.info("Opened %s over USB", self._name)

    def _close(self) -> None:
        camera, self._camera = self._camera, None
        if camera is None:
            return
        try:
            camera.stop_video_capture()
        except Exception:                         # already gone with the cable
            pass
        try:
            camera.close()
        except Exception:
            pass

    # ---- capture ------------------------------------------------------

    def start(self) -> None:
        """Opens the camera in the caller's thread, so a failure reaches the
        caller, then starts reading."""
        self._open()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _timeout_ms(self) -> int:
        # ZWO's suggested wait: twice the exposure plus 500 ms.
        return int(2 * self._exposure_us / 1000.0 + 500)

    def _run(self) -> None:
        count = 0
        while not self._stop.is_set():
            if self._camera is None:
                try:
                    self._open()
                except Exception as exc:
                    logger.warning("ZWO ASI camera unavailable (%s), retrying...", exc)
                    self._stop.wait(self._RECONNECT_DELAY_S)
                    continue
            started = time.monotonic()
            try:
                frame = self._camera.capture_video_frame(timeout=self._timeout_ms())
            except Exception as exc:
                logger.warning("Lost the ZWO ASI camera (%s), reconnecting...", exc)
                self._close()
                self._stop.wait(self._RECONNECT_DELAY_S)
                continue
            with self._lock:
                self._frame = frame
                self._frame_at = time.monotonic()
            count += 1
            if self._auto and count % self._ADJUST_EVERY == 0:
                self._adjust_exposure(frame)
            # Older frames are discarded by the SDK, so reading slower than
            # the camera runs costs nothing but saves the Pi's CPU.
            self._stop.wait(max(0.0, self._interval - (time.monotonic() - started)))

    def _adjust_exposure(self, frame: np.ndarray) -> None:
        level = float(frame[::8, ::8].mean())
        exposure = next_exposure_us(self._exposure_us, level,
                                    self._floor_us, self._ceiling_us)
        if exposure == int(round(self._exposure_us)):
            return
        try:
            self._camera.set_control_value(self._sdk.ASI_EXPOSURE, exposure)
            self._exposure_us = float(exposure)
        except Exception as exc:
            logger.warning("Could not set ZWO ASI exposure: %s", exc)

    def get_latest_frame(self) -> np.ndarray | None:
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._close()
