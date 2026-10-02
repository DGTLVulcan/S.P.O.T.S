"""Flipping a mirrored camera picture left to right.

The flip happens where the live camera's frames come in, so detection,
calibration, zoom and the dashboard all see the target the right way round.
"""
import os
import unittest
from html.parser import HTMLParser
from unittest import mock

import cv2
import numpy as np

from spots.camera.source import SwitchableFrameSource, SyntheticFrameSource, ZoomFrameSource
from spots.config import DetectionConfig, Settings
from spots.mount import AZM, ALT, NEGATIVE, POSITIVE, MountController
from spots.vision.detection import ShotDetector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE_CONFIG = os.path.join(ROOT, "config.example.yaml")

WIDTH, HEIGHT = 240, 160
CENTRE_X = WIDTH // 2
HOLE_X = 180                     # right of centre on the real target


def example_settings():
    """Settings from config.example.yaml; save() is a mock so nothing here
    can write the real config.yaml."""
    settings = Settings.load(EXAMPLE_CONFIG, env_path=None)
    settings.save = mock.Mock()
    return settings


def target(hole=False):
    """The target as it really is: pale card, a mark at top left standing in
    for the printing, and maybe one hole right of centre."""
    image = np.full((HEIGHT, WIDTH, 3), 200, np.uint8)
    cv2.rectangle(image, (10, 10), (60, 30), (40, 40, 40), -1)
    if hole:
        cv2.circle(image, (HOLE_X, HEIGHT // 2), 6, (20, 20, 20), -1)
    return image


class MirroredCamera:
    """A live camera seeing the target through a mirror, as through a star
    diagonal."""

    connected = True

    def __init__(self):
        self.hole = False

    def start(self):
        pass

    def stop(self):
        pass

    def get_latest_frame(self):
        return cv2.flip(target(self.hole), 1)


def feed(flipped):
    camera = MirroredCamera()
    state = {"flip": flipped}
    switcher = SwitchableFrameSource(SyntheticFrameSource(width=WIDTH, height=HEIGHT),
                                     {"asi": lambda: (camera, None)},
                                     flip_live=lambda: state["flip"])
    switcher.switch_to("asi")
    return switcher, camera, state


def detect_hole_x(switcher, camera):
    detector = ShotDetector(DetectionConfig(realignment_enabled=False))
    detector.reset(switcher.get_latest_frame())
    camera.hole = True
    for _ in range(5):
        shots = detector.process_frame(switcher.get_latest_frame())
        if shots:
            return shots[0].x_px
    raise AssertionError("the hole was never detected")


class FlipTests(unittest.TestCase):
    def test_flipped_the_picture_reads_the_right_way_round(self):
        switcher, _, _ = feed(flipped=True)
        np.testing.assert_array_equal(switcher.get_latest_frame(), target())

    def test_a_hole_right_of_centre_is_scored_right_of_centre(self):
        # Unflipped, the mirror puts it on the wrong side: the problem.
        self.assertLess(detect_hole_x(*feed(flipped=False)[:2]), CENTRE_X)
        x = detect_hole_x(*feed(flipped=True)[:2])
        self.assertAlmostEqual(x, HOLE_X, delta=1.5)

    def test_it_takes_effect_without_a_restart(self):
        switcher, _, state = feed(flipped=False)
        mirrored = switcher.get_latest_frame()
        state["flip"] = True
        np.testing.assert_array_equal(switcher.get_latest_frame(), cv2.flip(mirrored, 1))

    def test_the_simulated_target_is_never_flipped(self):
        switcher, _, _ = feed(flipped=True)
        switcher.switch_to("synthetic")
        # A fixed picture, since the simulated target animates.
        with mock.patch.object(switcher._synthetic, "get_latest_frame", return_value=target()):
            np.testing.assert_array_equal(switcher.get_latest_frame(), target())

    def test_zoom_works_on_the_flipped_picture(self):
        # The zoomed view of the left quarter, once flipped, is the right
        # quarter's view mirrored: what the mirrored zoom centre shows.
        switcher, _, state = feed(flipped=False)
        zoom = ZoomFrameSource(switcher, 2.0, 0.25, 0.5)
        before = zoom.get_latest_frame()
        state["flip"] = True
        zoom.set_zoom(2.0, 0.75, 0.5)
        np.testing.assert_array_equal(zoom.get_latest_frame(), cv2.flip(before, 1))


class MountFollowsTheFlipTests(unittest.TestCase):
    def _hand(self, flip, reverse=False):
        settings = example_settings()
        settings.mount.enabled = True
        settings.mount.reverse_left_right = reverse
        settings.camera.flip_horizontal = flip
        ctl = MountController(settings, opener=mock.Mock(), ports=lambda: [], watchdog=False)
        return ctl

    def test_flipping_swaps_left_and_right_only(self):
        self.assertEqual(self._hand(False)._axis_for("right"), (AZM, POSITIVE))
        self.assertEqual(self._hand(True)._axis_for("right"), (AZM, NEGATIVE))
        self.assertEqual(self._hand(True)._axis_for("up"), (ALT, POSITIVE))

    def test_flip_and_reverse_cancel(self):
        self.assertEqual(self._hand(True, reverse=True)._axis_for("right"), (AZM, POSITIVE))


class _Fields(HTMLParser):
    """The values a browser would submit from the settings form."""

    def __init__(self):
        super().__init__()
        self.fields = []
        self._select = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("name"):
            if a.get("type") == "checkbox":
                if "checked" in a:
                    self.fields.append((a["name"], "on"))
            elif a.get("type") != "submit":
                self.fields.append((a["name"], a.get("value", "")))
        elif tag == "select" and a.get("name"):
            self._select = a["name"]
        elif tag == "option" and self._select and "selected" in a:
            self.fields.append((self._select, a.get("value", "")))

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None


class SettingsPageTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        from spots.web.routes import bp
        self.settings = example_settings()
        self.worker = mock.Mock()
        self.worker.get_active_feed.return_value = "asi"
        self.worker.get_zoom.return_value = (2.0, 0.3, 0.5)
        storage = mock.Mock()
        storage.get_range_state.return_value = "hot"
        app = Flask("spots.web.app", root_path=os.path.join(ROOT, "spots", "web"))
        app.register_blueprint(bp)
        app.config.update(SETTINGS=self.settings, WORKER=self.worker, STORAGE=storage,
                          MOUNT=mock.Mock())
        self.client = app.test_client()

    def _save(self, flip):
        from werkzeug.datastructures import MultiDict
        page = _Fields()
        page.feed(self.client.get("/settings").get_data(as_text=True))
        fields = [(k, v) for k, v in page.fields if k != "camera.flip_horizontal"]
        if flip:
            fields.append(("camera.flip_horizontal", "on"))
        return self.client.post("/settings", data=MultiDict(fields))

    def test_the_option_sits_above_the_restart_note(self):
        html = self.client.get("/settings").get_data(as_text=True)
        panel = html[html.index('data-panel="camera" hidden'):]
        self.assertIn('name="camera.flip_horizontal"', panel)
        self.assertLess(panel.index('name="camera.flip_horizontal"'), panel.index("restart-note"))

    def test_flipping_keeps_the_zoom_on_the_same_spot_and_rebaselines(self):
        self.assertIn("saved=1", self._save(flip=True).headers["Location"])
        self.assertTrue(self.settings.camera.flip_horizontal)
        level, center_x, center_y = self.worker.set_zoom.call_args.args
        self.assertEqual((level, center_y), (2.0, 0.5))
        self.assertAlmostEqual(center_x, 0.7)
        self.assertAlmostEqual(self.settings.camera.zoom_center_x, 0.7)
        self.worker.rebaseline.assert_called_once_with()

    def test_saving_without_changing_it_leaves_detection_alone(self):
        self._save(flip=False)
        self.worker.rebaseline.assert_not_called()
        self.worker.set_zoom.assert_not_called()

    def test_flipping_on_the_simulated_target_changes_nothing_else(self):
        self.worker.get_active_feed.return_value = "synthetic"
        self._save(flip=True)
        self.assertTrue(self.settings.camera.flip_horizontal)
        self.worker.rebaseline.assert_not_called()
        self.worker.set_zoom.assert_not_called()


if __name__ == "__main__":
    unittest.main()
