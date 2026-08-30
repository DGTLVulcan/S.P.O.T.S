"""Deriving the expected hole size from the bullet diameter and the
calibrated scale, instead of fixed pixel figures that only suit one framing."""

import os
import sys

# Import the app regardless of where the runner sets its working directory
# (Visual Studio Test Explorer, `python -m unittest`, and pytest all differ).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import math
import shutil
import tempfile
import unittest

import cv2
import numpy as np

from spots.camera.source import SyntheticFrameSource
from spots.config import DetectionConfig, TargetConfig
from spots.storage import Storage
from spots.vision.calibration import Calibration
from spots.vision.detection import ShotDetector
from spots.worker import DetectionWorker


class HoleAreaDerivationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.storage = Storage(os.path.join(self.dir, "t.db"))
        self.target = TargetConfig(unit_name="cm")
        self.detection = DetectionConfig()
        self.worker = DetectionWorker(
            SyntheticFrameSource(), self.storage, self.target, self.detection, self.dir
        )

    def tearDown(self):
        self.storage.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def calibrate(self, units_per_px):
        self.worker.state.reset(1, Calibration(units_per_px, "cm", (0.0, 0.0)), 100.0)

    def test_matches_the_arithmetic(self):
        """A 1920 px frame spanning 60 cm gives 32 px/cm, so a 7.82 mm bullet
        makes a hole about 25 px across."""
        self.calibrate(60.0 / 1920.0)
        self.worker.set_bullet_diameter_mm(7.82)
        result = self.worker.hole_area_range()
        expected_px = 0.782 / (60.0 / 1920.0)
        self.assertAlmostEqual(result["diameter_px"], expected_px, places=6)
        self.assertAlmostEqual(result["area_px"], math.pi * (expected_px / 2) ** 2, places=4)

    def test_window_brackets_the_expected_area(self):
        self.calibrate(60.0 / 1920.0)
        self.worker.set_bullet_diameter_mm(7.82)
        result = self.worker.hole_area_range()
        self.assertLess(result["min_area_px"], result["area_px"])
        self.assertGreater(result["max_area_px"], result["area_px"])

    def test_scales_with_framing(self):
        """The same rifle on a target filling less of the frame makes a
        smaller hole in pixels -- which is exactly why a fixed figure fails."""
        self.worker.set_bullet_diameter_mm(7.82)
        self.calibrate(60.0 / 1920.0)
        tight = self.worker.hole_area_range()["diameter_px"]
        self.calibrate(120.0 / 1920.0)
        wide = self.worker.hole_area_range()["diameter_px"]
        self.assertAlmostEqual(tight / wide, 2.0, places=6)

    def test_unavailable_without_what_it_needs(self):
        self.worker.set_bullet_diameter_mm(7.82)
        self.assertIsNone(self.worker.hole_area_range(), "no calibration yet")

        self.calibrate(60.0 / 1920.0)
        self.worker.set_bullet_diameter_mm(None)
        self.assertIsNone(self.worker.hole_area_range(), "no bullet diameter recorded")

        self.worker.set_bullet_diameter_mm(7.82)
        self.detection.auto_hole_area = False
        self.assertIsNone(self.worker.hole_area_range(), "auto-sizing turned off")

    def test_unconvertible_unit_falls_back(self):
        """A made-up unit can't be turned into millimetres, so there is
        nothing to derive from and the manual figures stand."""
        self.calibrate(60.0 / 1920.0)
        self.worker.set_bullet_diameter_mm(7.82)
        self.target.unit_name = "clicks"
        self.assertIsNone(self.worker.hole_area_range())


class DetectorAreaRangeTests(unittest.TestCase):
    """The derived window is passed straight to the detector, and must not be
    scaled by zoom again -- the calibration it came from was measured in the
    already-zoomed view."""

    def setUp(self):
        self.source = SyntheticFrameSource()
        self.detector = ShotDetector(DetectionConfig())
        self.detector.reset(self.source.get_latest_frame())

    # (700, 400) sits on a dark printed ring, so the hole is punched bright:
    # a real hole shows whatever is behind the paper, and what matters is the
    # contrast, not the direction. Punching dark here would be invisible.
    HOLE_AT = (700, 400)
    HOLE_SHADE = (200, 200, 200)

    def punch(self, radius):
        frame = self.source.get_latest_frame()
        cv2.circle(frame, self.HOLE_AT, radius, self.HOLE_SHADE, thickness=-1)
        return frame

    def detect(self, radius, area_range):
        detector = ShotDetector(DetectionConfig())
        detector.reset(self.source.get_latest_frame())
        found = 0
        for _ in range(4):
            found += len(detector.process_frame(self.punch(radius), area_range=area_range))
        return found

    def test_a_big_hole_passes_with_a_matching_window(self):
        radius = 13  # ~530 px^2, over the 400 default
        self.assertEqual(self.detect(radius, None), 0, "rejected by the fixed default")
        self.assertEqual(self.detect(radius, (130.0, 2100.0)), 1, "accepted by the derived window")

    def test_zoom_is_not_applied_to_an_explicit_window(self):
        frame = self.punch(13)
        detector = ShotDetector(DetectionConfig())
        detector.reset(self.source.get_latest_frame())
        # A window that fits at 1x must still fit when zoom is reported,
        # because the calibration behind it already accounts for zoom.
        found = 0
        for _ in range(4):
            found += len(detector.process_frame(frame, zoom_level=3.0, area_range=(130.0, 2100.0)))
        self.assertEqual(found, 1)


if __name__ == "__main__":
    unittest.main()
