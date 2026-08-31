"""Shot detection via frame differencing against a rolling reference frame.

Pipeline per sampled frame:
  1. absdiff against the reference frame, threshold, morphological close
  2. contour extraction, filtered by area + circularity
  3. require a candidate to persist across `debounce_frames` consecutive
     sampled frames before committing it (rejects wind-flutter / shadow
     flicker false positives on an outdoor range)

Burn-in: a committed hole is painted into the reference, so later diffs
only measure what's newly changed. Otherwise it keeps diffing against the
original clean target, a second shot beside it merges into one blob, and the
merged area outgrows `max_hole_area_px` -- tight groups stop registering
after a shot or two.

There is deliberately no "reject candidates near a committed shot" filter:
real tight groups can be a few pixels apart, so it would block the very case
burn-in exists to handle.

Re-alignment: a target on a stand sways in wind, and a naive diff reads that
as a wall of new holes. Each frame is matched against the anchor (the clean
image captured at `reset()`) by ORB/SIFT + RANSAC homography and warped into
its space. Always the fixed anchor, never the last frame, so error can't
accumulate; too few matches skips the frame rather than risk a bad warp.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import cv2
import numpy as np

from spots.config import DetectionConfig

logger = logging.getLogger(__name__)

_BLUR_KERNEL = (5, 5)
_MORPH_KERNEL = np.ones((5, 5), np.uint8)
_LOWE_RATIO = 0.75
_RANSAC_REPROJ_THRESHOLD_PX = 5.0
_CLAHE = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))


@dataclass
class Shot:
    seq: int
    x_px: float
    y_px: float


@dataclass
class _PendingCandidate:
    x_px: float
    y_px: float
    area_px: float
    streak: int = 1


def warp_point(point: tuple[float, float], homography: np.ndarray | None) -> tuple[float, float]:
    """Applies a homography to a single (x, y) point. Pass None for an
    identity transform -- used to move points between the detector's anchor
    coordinate space and a raw (possibly re-aligned-away-from) camera frame.
    """
    if homography is None:
        return point
    src = np.array([[point]], dtype=np.float32)
    dst = cv2.perspectiveTransform(src, homography)
    return float(dst[0, 0, 0]), float(dst[0, 0, 1])


def invert_homography(homography: np.ndarray | None) -> np.ndarray | None:
    if homography is None:
        return None
    try:
        return np.linalg.inv(homography)
    except np.linalg.LinAlgError:
        return None  # singular (shouldn't happen post-RANSAC); caller falls back to identity


def _preprocess(frame_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    # Local contrast normalisation before differencing: a hole on a dark
    # ring may differ from it by only ~30, against 200+ on white paper, so
    # one global threshold can't serve both. CLAHE makes a hole read as a
    # strong local change whatever shade it sits on.
    gray = _CLAHE.apply(gray)
    return cv2.GaussianBlur(gray, _BLUR_KERNEL, 0)


def _make_feature_detector(method: str):
    if method == "sift":
        return cv2.SIFT_create()
    if method == "orb":
        return cv2.ORB_create(nfeatures=500)
    raise ValueError(f"Unknown realignment_method: {method!r} (expected 'orb' or 'sift')")


class ShotDetector:
    def __init__(self, config: DetectionConfig):
        self._config = config
        self._reference: np.ndarray | None = None
        self._committed: list[tuple[float, float]] = []
        # Radius burned in for each committed shot, and the union of those
        # discs as a mask. Kept so the reference can be refreshed there on
        # every frame rather than only once, at commit.
        self._burn_radii: list[int] = []
        self._burn_mask: np.ndarray | None = None
        self._pending: list[_PendingCandidate] = []
        self._next_seq = 1

        # The anchor is fixed for the life of the target, so alignment
        # error can't accumulate the way it would against a drifting frame.
        self._anchor: np.ndarray | None = None
        self._anchor_kp = None
        self._anchor_desc = None
        self._last_homography: np.ndarray | None = None
        if config.realignment_enabled:
            self._feature_detector = _make_feature_detector(config.realignment_method)
            norm = cv2.NORM_HAMMING if config.realignment_method == "orb" else cv2.NORM_L2
            self._matcher = cv2.BFMatcher(norm, crossCheck=False)
        else:
            self._feature_detector = None
            self._matcher = None

    @property
    def has_reference(self) -> bool:
        return self._reference is not None

    @property
    def committed_shots_px(self) -> list[tuple[float, float]]:
        return list(self._committed)

    @property
    def last_homography(self) -> np.ndarray | None:
        """Current-frame -> anchor-frame transform from the most recent
        successful alignment, or None if realignment is disabled/unavailable.
        Used by the dashboard to draw shot overlays (stored in anchor space)
        back onto the raw, unwarped live feed.
        """
        return self._last_homography

    def reset(self, frame_bgr: np.ndarray, next_seq: int = 1) -> None:
        """Arms detection against `frame_bgr` as the new clean reference.

        `next_seq` continues an existing session's numbering, for a resume
        where the target is re-baselined but its shots keep their numbers.
        """
        self._reference = _preprocess(frame_bgr)
        self._anchor = self._reference.copy()
        self._committed.clear()
        self._burn_radii.clear()
        self._burn_mask = None
        self._pending.clear()
        self._next_seq = max(1, next_seq)
        self._last_homography = None
        if self._feature_detector is not None:
            self._anchor_kp, self._anchor_desc = self._feature_detector.detectAndCompute(
                self._anchor, None
            )
        else:
            self._anchor_kp, self._anchor_desc = None, None

    def undo_last(self) -> None:
        if self._committed:
            self._committed.pop()
            if self._burn_radii:
                self._burn_radii.pop()
            self._burn_mask = None
            self._next_seq = max(1, self._next_seq - 1)

    def reserve_seq(self) -> int:
        """Reserves the next sequence number for a shot recorded outside the
        normal detection pipeline (e.g. a manually placed test shot), so it
        stays unique and ordered alongside real detections without this
        detector ever knowing the test shot exists.
        """
        seq = self._next_seq
        self._next_seq += 1
        return seq

    def _align(self, gray: np.ndarray) -> np.ndarray | None:
        """Returns `gray` warped into anchor coordinates, or None if
        alignment couldn't be established this frame (caller should skip it).
        """
        if self._feature_detector is None:
            return gray
        if self._anchor_desc is None or len(self._anchor_desc) < 2:
            # Anchor itself has too little texture to ever align against;
            # degrade to "no realignment" rather than skip every frame.
            return gray

        kp, desc = self._feature_detector.detectAndCompute(gray, None)
        if desc is None or len(desc) < 2:
            self._last_homography = None
            return None

        matches = self._matcher.knnMatch(desc, self._anchor_desc, k=2)
        good = [m for m, n in matches if m.distance < _LOWE_RATIO * n.distance]
        if len(good) < self._config.realignment_min_matches:
            self._last_homography = None
            return None

        src_pts = np.float32([kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst_pts = np.float32([self._anchor_kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        homography, _ = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, _RANSAC_REPROJ_THRESHOLD_PX)
        if homography is None:
            self._last_homography = None
            return None

        self._last_homography = homography
        height, width = self._anchor.shape
        return cv2.warpPerspective(gray, homography, (width, height))

    def process_frame(
        self,
        frame_bgr: np.ndarray,
        zoom_level: float = 1.0,
        area_range: tuple[float, float] | None = None,
    ) -> list[Shot]:
        if self._reference is None:
            return []

        cfg = self._config
        gray_raw = _preprocess(frame_bgr)
        gray = self._align(gray_raw)
        if gray is None:
            logger.warning("Realignment failed (too few feature matches), skipping frame")
            return []

        # Keep the reference current wherever a hole has already been
        # counted. Burning a hole in once is not enough on a target that
        # moves: the hole drifts against its burned-in patch, the sliver
        # left over reads as a fresh change, and -- since a candidate next
        # to a committed shot is deliberately NOT rejected -- the same hole
        # gets counted again and again. Repainting those discs every frame
        # keeps the difference there at zero, without touching the rest of
        # the target, so genuinely new shots still register normally.
        self._refresh_burned(gray)

        diff = cv2.absdiff(gray, self._reference)
        _, thresh = cv2.threshold(diff, cfg.diff_threshold, 255, cv2.THRESH_BINARY)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, _MORPH_KERNEL)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if area_range is not None:
            # Already in current-view pixels (the calibration behind it was
            # measured zoomed), so must not be scaled by zoom again.
            min_area, max_area = area_range
        else:
            # A hole's footprint grows with the square of the zoom level,
            # and the configured figures are for 1x.
            area_scale = zoom_level**2
            min_area = cfg.min_hole_area_px * area_scale
            max_area = cfg.max_hole_area_px * area_scale

        candidates: list[tuple[float, float, float]] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if not (min_area <= area <= max_area):
                continue
            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            circularity = 4 * math.pi * area / (perimeter**2)
            if circularity < cfg.min_circularity:
                continue
            moments = cv2.moments(contour)
            if moments["m00"] == 0:
                continue
            cx = moments["m10"] / moments["m00"]
            cy = moments["m01"] / moments["m00"]
            candidates.append((cx, cy, area))

        return self._debounce(candidates, gray)

    def _debounce(self, candidates: list[tuple[float, float, float]], gray: np.ndarray) -> list[Shot]:
        spacing = self._config.min_shot_spacing_px
        remaining = list(candidates)
        next_pending: list[_PendingCandidate] = []
        committed_now: list[Shot] = []

        for pending in self._pending:
            match_idx = None
            best_dist = spacing
            for i, (cx, cy, _area) in enumerate(remaining):
                dist = math.hypot(cx - pending.x_px, cy - pending.y_px)
                if dist < best_dist:
                    best_dist = dist
                    match_idx = i
            if match_idx is not None:
                cx, cy, area = remaining.pop(match_idx)
                pending.x_px, pending.y_px, pending.area_px = cx, cy, area
                pending.streak += 1
                if pending.streak >= self._config.debounce_frames:
                    self._committed.append((pending.x_px, pending.y_px))
                    committed_now.append(Shot(self._next_seq, pending.x_px, pending.y_px))
                    self._next_seq += 1
                    self._burn_in(gray, pending.x_px, pending.y_px, pending.area_px)
                else:
                    next_pending.append(pending)
            # unmatched pending candidates are dropped (didn't persist)

        for cx, cy, area in remaining:
            next_pending.append(_PendingCandidate(cx, cy, area))

        self._pending = next_pending
        return committed_now

    def _refresh_burned(self, gray: np.ndarray) -> None:
        """Repaint the reference from this frame at every counted hole."""
        if not self._committed:
            return
        if self._burn_mask is None or self._burn_mask.shape != gray.shape:
            mask = np.zeros(gray.shape, dtype=np.uint8)
            for (cx, cy), radius in zip(self._committed, self._burn_radii):
                cv2.circle(mask, (int(cx), int(cy)), radius, 255, thickness=-1)
            # Cached because it only changes when a shot is added or undone,
            # and rebuilding it per frame would cost more than the repaint.
            self._burn_mask = mask.astype(bool)
        np.copyto(self._reference, gray, where=self._burn_mask)

    def _burn_in(self, gray: np.ndarray, cx: float, cy: float, area_px: float) -> None:
        """Paint a committed hole into the reference so future diffs are
        incremental, letting overlapping/adjacent shots register separately.
        """
        # Generous enough to cover the hole plus the drift left over after
        # re-alignment, which is what the refresh above has to swallow.
        radius = int(math.sqrt(area_px / math.pi)) + self._config.burn_in_margin_px
        self._burn_radii.append(radius)
        self._burn_mask = None
        mask = np.zeros_like(self._reference, dtype=np.uint8)
        cv2.circle(mask, (int(cx), int(cy)), radius, 255, thickness=-1)
        self._reference = np.where(mask == 255, gray, self._reference)
