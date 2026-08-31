"""Shot detection against the synthetic target: the cases that have actually
broken before -- overlapping groups, holes on dark printed rings, and false
positives from frame noise."""

import os
import sys

# Import the app regardless of where the runner sets its working directory
# (Visual Studio Test Explorer, `python -m unittest`, and pytest all differ).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest

import cv2
import numpy as np

from spots.camera.source import SyntheticFrameSource, ZoomFrameSource
from spots.config import DetectionConfig
from spots.vision.detection import ShotDetector


def detect_holes(source, detector, placements, frames_each=4):
    """Places each hole and lets the detector see enough frames to satisfy
    the debounce, returning every shot it committed."""
    found = []
    for x, y in placements:
        source.add_hole(x, y)
        for _ in range(frames_each):
            found.extend(detector.process_frame(source.get_latest_frame()))
    return found


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.source = SyntheticFrameSource()
        self.detector = ShotDetector(DetectionConfig())
        self.detector.reset(self.source.get_latest_frame())

    def test_well_separated_shots(self):
        found = detect_holes(self.source, self.detector,
                             [(700, 400), (800, 450), (900, 500), (1000, 550)])
        self.assertEqual(len(found), 4)

    def test_overlapping_group_registers_shot_by_shot(self):
        """Burn-in paints each committed hole into the reference, so a tight
        group keeps registering instead of merging into one growing blob."""
        found = detect_holes(self.source, self.detector,
                             [(800, 400), (810, 410), (820, 420), (830, 430)])
        self.assertEqual(len(found), 4)

    def test_holes_on_dark_rings(self):
        """A hole on a dark printed ring differs from it by far less than on
        white paper -- the threshold has to be low enough to catch it."""
        cx, cy = self.source._center
        radius = self.source._target_radius
        found = detect_holes(self.source, self.detector, [
            (cx + int(radius * 0.85), cy),
            (cx, cy + int(radius * 0.85)),
            (cx - int(radius * 0.55), cy),
        ])
        self.assertEqual(len(found), 3)

    def test_no_false_positives_from_frame_noise(self):
        noise_hits = sum(
            len(self.detector.process_frame(self.source.get_latest_frame())) for _ in range(60)
        )
        self.assertEqual(noise_hits, 0)

    def test_single_frame_blip_is_rejected(self):
        """Debounce: a candidate must persist across frames, so a bit of
        flutter or a shadow doesn't count as a shot."""
        clean = self.source.get_latest_frame()
        blip = clean.copy()
        cv2.circle(blip, (600, 600), 8, (10, 10, 10), thickness=-1)
        committed = (
            self.detector.process_frame(blip)
            + self.detector.process_frame(clean)
            + self.detector.process_frame(clean)
        )
        self.assertEqual(committed, [])

    def test_shifted_frame_does_not_produce_phantom_shots(self):
        """Wind sway is re-aligned away rather than read as a wall of holes."""
        frame = self.source.get_latest_frame()
        shifted = cv2.warpAffine(
            frame, np.float32([[1, 0, 7], [0, 1, 4]]), (frame.shape[1], frame.shape[0])
        )
        self.assertEqual(self.detector.process_frame(shifted), [])
        self.assertIsNotNone(self.detector.last_homography)

    def test_reset_can_continue_an_existing_sequence(self):
        """Resuming a session re-baselines the detector but must keep
        numbering where the stored shots left off."""
        self.detector.reset(self.source.get_latest_frame(), next_seq=12)
        found = detect_holes(self.source, self.detector, [(700, 300)])
        self.assertEqual([shot.seq for shot in found], [12])


class SyntheticSourceTests(unittest.TestCase):
    def test_frames_differ_but_stay_within_the_detection_threshold(self):
        """The dither has to look live without tripping the differ."""
        source = SyntheticFrameSource()
        first, second = source.get_latest_frame(), source.get_latest_frame()
        self.assertFalse(np.array_equal(first, second))
        delta = np.abs(first.astype(int) - second.astype(int)).max()
        self.assertLess(delta, DetectionConfig().diff_threshold)

    def test_returned_frames_are_private_buffers(self):
        """The MJPEG path draws overlays straight into the returned frame, so
        it must not be a view of anything reused."""
        source = SyntheticFrameSource()
        frame = source.get_latest_frame()
        frame[:] = 0
        self.assertGreater(source.get_latest_frame().mean(), 100)

    def test_reset_target_clears_holes(self):
        source = SyntheticFrameSource()
        source.add_hole(700, 400)
        with_hole = source.get_latest_frame()
        source.reset_target()
        cleared = source.get_latest_frame()
        self.assertLess(cleared[400, 700].mean(), 256)
        self.assertGreater(cleared[400, 700].mean(), with_hole[400, 700].mean())

    def test_zoom_passthrough_and_crop_keep_frame_size(self):
        source = SyntheticFrameSource()
        passthrough = ZoomFrameSource(source, 1.0, 0.5, 0.5).get_latest_frame()
        zoomed = ZoomFrameSource(source, 2.0, 0.5, 0.5).get_latest_frame()
        self.assertEqual(passthrough.shape, zoomed.shape)


class SyntheticModeTests(unittest.TestCase):
    """The realistic target is what the detector will be judged on, so it
    has to actually behave like paper in front of a berm."""

    def setUp(self):
        from spots.camera.source import SyntheticFrameSource
        self.Source = SyntheticFrameSource

    def test_modes_are_whitelisted(self):
        source = self.Source(320, 240)
        with self.assertRaises(ValueError):
            source.set_mode("photoreal")
        self.assertEqual(source.set_mode("realistic"), "realistic")
        self.assertEqual(source.mode, "realistic")

    def test_an_unknown_mode_at_construction_falls_back(self):
        self.assertEqual(self.Source(320, 240, mode="nonsense").mode, "simple")

    def test_the_simple_target_does_not_move(self):
        source = self.Source(320, 240, mode="simple")
        first, second = source.get_latest_frame(), source.get_latest_frame()
        # Only the dither differs, which is small by design.
        self.assertLess(float(np.abs(first.astype(int) - second.astype(int)).mean()), 4)

    def test_the_realistic_target_moves_in_the_wind(self):
        source = self.Source(640, 480, mode="realistic")
        frames = [source.get_latest_frame() for _ in range(20)]
        spread = max(float(np.abs(frames[0].astype(int) - f.astype(int)).mean())
                     for f in frames[1:])
        # Sway has to be big enough that re-alignment is actually exercised.
        self.assertGreater(spread, 4)

    def test_a_realistic_hole_shows_the_ground_not_black(self):
        source = self.Source(640, 480, mode="realistic")
        centre = (320, 240)
        source.add_hole(*centre)
        frame = source.get_latest_frame()
        patch = frame[centre[1] - 2:centre[1] + 3, centre[0] - 2:centre[0] + 3]
        # The berm is mid-grey earth; a black disc would be far darker, and
        # a hole that reads as "black" is what made the old target easy.
        self.assertGreater(float(patch.mean()), 30)
        self.assertLess(float(patch.mean()), 190)

    def test_a_simple_hole_is_still_the_old_black_disc(self):
        source = self.Source(640, 480, mode="simple")
        centre = (320, 240)
        source.add_hole(*centre)
        frame = source.get_latest_frame()
        patch = frame[centre[1] - 2:centre[1] + 3, centre[0] - 2:centre[0] + 3]
        self.assertLess(float(patch.mean()), 30)

    def test_switching_mode_does_not_lose_the_holes(self):
        source = self.Source(640, 480, mode="simple")
        source.add_hole(320, 240)
        source.set_mode("realistic")
        frame = source.get_latest_frame()
        patch = frame[238:243, 318:323]
        self.assertLess(float(patch.mean()), 190)


if __name__ == "__main__":
    unittest.main()
