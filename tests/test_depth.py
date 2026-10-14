"""The Distance Estimate: how far away the target is, from its calibrated
size, the telescope's focal length and the ZWO camera's pixel size.

Targets are drawn as the 4SE and ASI290MC would see them at a known
distance, then put through calibration and the estimate.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from unittest import mock

import cv2
import numpy as np

from spots.camera.source import ZoomFrameSource
from spots.config import OpticsConfig, Settings
from spots.storage import Storage
from spots.vision import depth
from spots.vision.calibration import Calibration

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE_CONFIG = os.path.join(ROOT, "config.example.yaml")

# NexStar 4SE (1325 mm) behind a 0.5x reducer, on an ASI290MC.
FOCAL_MM = 662.5
PIXEL_UM = 2.9
WIDTH, HEIGHT = 1936, 1096


def example_settings():
    """Settings from config.example.yaml; save() is a mock so nothing here
    can write the real config.yaml."""
    settings = Settings.load(EXAMPLE_CONFIG, env_path=None)
    settings.save = mock.Mock()
    return settings


def px_per_metre(distance_m, focal_mm=FOCAL_MM):
    """Sensor pixels a metre of target spans at this distance (thin lens)."""
    m = (focal_mm / 1000.0) / (distance_m - focal_mm / 1000.0)
    return m / (PIXEL_UM * 1e-6)


def picture(distance_m, mark_gap_cm=40.0, focal_mm=FOCAL_MM):
    """A pale target with two dots `mark_gap_cm` apart, centred, as seen at
    `distance_m` through `focal_mm`. Returns the frame and the dots' true
    pixel positions."""
    frame = np.full((HEIGHT, WIDTH, 3), 210, np.uint8)
    gap_px = mark_gap_cm / 100.0 * px_per_metre(distance_m, focal_mm)
    cx, cy = WIDTH / 2.0, HEIGHT / 2.0
    marks = [(cx - gap_px / 2, cy), (cx + gap_px / 2, cy)]
    for x, y in marks:
        # Drawn with sub-pixel precision, as a lens would put them.
        cv2.circle(frame, (int(round(x * 16)), int(round(y * 16))), 6 * 16, (20, 20, 20), -1,
                   lineType=cv2.LINE_AA, shift=4)
    return frame, marks


def find_marks(frame):
    """The two dots' centres, found in the picture as a click would."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, dark = cv2.threshold(gray, 120, 255, cv2.THRESH_BINARY_INV)
    count, _, _, centroids = cv2.connectedComponentsWithStats(dark)
    found = sorted((tuple(c) for c in centroids[1:]), key=lambda p: p[0])
    assert len(found) == 2, f"found {count - 1} marks"
    return found


def calibrate(frame, zoom_level=1.0, feed="asi", gap_cm=40.0):
    p1, p2 = find_marks(frame)
    return Calibration.from_two_points(p1, p2, gap_cm, "cm", origin_px=p1,
                                       zoom_level=zoom_level, feed=feed)


def estimate(calibration, pixel_size_um=PIXEL_UM, optics=None, feed="asi", connected=True):
    return depth.estimate(feed, connected, calibration, pixel_size_um, optics or OpticsConfig())


class MathsTests(unittest.TestCase):
    def test_the_specs_give_662_5_mm(self):
        self.assertEqual(OpticsConfig().focal_length_mm(), FOCAL_MM)

    def test_distance_and_focal_length_invert_each_other(self):
        for distance in (25.0, 100.0, 300.0, 914.4):
            m = (FOCAL_MM / 1000.0) / (distance - FOCAL_MM / 1000.0)
            self.assertAlmostEqual(depth.distance_m(FOCAL_MM, m), distance, places=9)
            self.assertAlmostEqual(depth.focal_length_for(distance, m), FOCAL_MM, places=9)

    def test_the_unit_doesnt_matter(self):
        per_px_cm = 0.04
        m_cm = depth.magnification(per_px_cm, "cm", 1.0, PIXEL_UM)
        self.assertAlmostEqual(depth.magnification(per_px_cm * 10, "mm", 1.0, PIXEL_UM), m_cm)
        self.assertAlmostEqual(depth.magnification(per_px_cm / 2.54, "in", 1.0, PIXEL_UM), m_cm)
        self.assertIsNone(depth.magnification(per_px_cm, "furlongs", 1.0, PIXEL_UM))

    def test_corrections_far_from_the_specs_are_refused(self):
        self.assertTrue(depth.correction_allowed(700.0, FOCAL_MM))
        self.assertFalse(depth.correction_allowed(6625.0, FOCAL_MM))   # 1000 typed for 100
        self.assertFalse(depth.correction_allowed(66.0, FOCAL_MM))


class PictureTests(unittest.TestCase):
    """From pixels in a picture to a distance."""

    def test_recovers_the_distance_of_a_drawn_target(self):
        for distance, gap in ((50.0, 40.0), (100.0, 40.0), (300.0, 59.0)):
            frame, _ = picture(distance, mark_gap_cm=gap)
            result = estimate(calibrate(frame, gap_cm=gap))
            self.assertTrue(result["available"], result)
            self.assertAlmostEqual(result["distance_m"], distance, delta=distance * 0.005,
                                   msg=f"at {distance} m")

    def test_digital_zoom_is_allowed_for(self):
        # Calibrated through the app's own zoom, which magnifies the pixels.
        frame, _ = picture(150.0, mark_gap_cm=20.0)
        source = mock.Mock()
        source.get_latest_frame.return_value = frame
        zoomed = ZoomFrameSource(source, 2.0, 0.5, 0.5).get_latest_frame()
        calibration = calibrate(zoomed, zoom_level=2.0, gap_cm=20.0)
        result = estimate(calibration)
        self.assertAlmostEqual(result["distance_m"], 150.0, delta=0.75)
        # Ignoring the zoom would put it at half the distance.
        calibration.zoom_level = 1.0
        self.assertAlmostEqual(estimate(calibration)["distance_m"], 75.0, delta=1.0)

    def test_a_known_distance_corrects_the_focal_length(self):
        # The real optics are 690 mm, not the specs' 662.5: the estimate is
        # off until it's corrected once at a measured distance.
        calibration = calibrate(picture(120.0, focal_mm=690.0)[0])
        optics = OpticsConfig()
        off = estimate(calibration, optics=optics)
        self.assertGreater(abs(off["distance_m"] - 120.0), 3.0)
        optics.measured_focal_length_mm = depth.focal_length_for(120.0, off["magnification"])
        optics.measured_at_m = 120.0
        self.assertAlmostEqual(optics.measured_focal_length_mm, 690.0, delta=3.5)
        later = estimate(calibrate(picture(250.0, focal_mm=690.0)[0]), optics=optics)
        self.assertEqual(later["focal_from"], "measured")
        self.assertAlmostEqual(later["distance_m"], 250.0, delta=2.5)


class WhenItCantTests(unittest.TestCase):
    def setUp(self):
        self.calibration = calibrate(picture(100.0)[0])

    def test_needs_the_zwo_as_the_live_feed(self):
        for feed in ("synthetic", "zcam"):
            result = estimate(self.calibration, feed=feed)
            self.assertFalse(result["available"])
            self.assertIn("ZWO ASI camera as the live feed", result["message"])

    def test_needs_the_zwo_to_be_sending(self):
        result = estimate(self.calibration, connected=False)
        self.assertEqual(result["reason"], "camera")

    def test_needs_a_calibration(self):
        self.assertEqual(estimate(None)["reason"], "calibrate")

    def test_a_scale_from_another_camera_wont_do(self):
        self.calibration.feed = "synthetic"
        result = estimate(self.calibration)
        self.assertEqual(result["reason"], "recalibrate")
        self.assertIn("simulated target", result["message"])
        self.calibration.feed = ""                      # a session from before this
        self.assertEqual(estimate(self.calibration)["reason"], "recalibrate")

    def test_an_unconvertible_unit_says_so(self):
        self.calibration.unit_name = "rings"
        self.assertIn("'rings'", estimate(self.calibration)["message"])

    def test_the_cameras_own_pixel_size_wins(self):
        result = estimate(self.calibration, pixel_size_um=PIXEL_UM)
        self.assertEqual(result["pixel_from"], "camera")
        optics = OpticsConfig(pixel_size_um=5.8)
        result = estimate(self.calibration, pixel_size_um=None, optics=optics)
        self.assertEqual(result["pixel_from"], "settings")
        self.assertEqual(result["pixel_size_um"], 5.8)


class PersistenceTests(unittest.TestCase):
    def test_zoom_and_feed_survive_a_restart(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        storage = Storage(os.path.join(tmp, "spots.db"))
        self.addCleanup(storage.close)
        old = storage.new_session("cm", 100.0)
        new = storage.new_session("cm", 100.0)
        storage.save_calibration(new, 0.04, (10.0, 20.0), True, zoom_level=2.0, feed="asi")
        storage.save_calibration(old, 0.04, (10.0, 20.0), True)
        self.assertEqual((storage.get_session(new)["calib_zoom"],
                          storage.get_session(new)["calib_feed"]), (2.0, "asi"))
        self.assertIsNone(storage.get_session(old)["calib_feed"])

    def test_a_resumed_session_keeps_them(self):
        from spots import worker as worker_module
        from spots.config import DetectionConfig, TargetConfig
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        storage = Storage(os.path.join(tmp, "spots.db"))
        self.addCleanup(storage.close)
        session = storage.new_session("cm", 100.0)
        storage.save_calibration(session, 0.04, (10.0, 20.0), True, zoom_level=1.5, feed="asi")
        worker = worker_module.DetectionWorker(mock.Mock(), storage, TargetConfig(),
                                               DetectionConfig(), tmp)
        self.assertTrue(worker.resume_last_session())
        calibration = worker.state.snapshot().calibration
        self.assertEqual((calibration.zoom_level, calibration.feed), (1.5, "asi"))


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


class FakeWorker:
    """Just enough of the detection worker for the routes involved."""

    def __init__(self, frame):
        self.frame = frame
        self.calibration = None
        self.feed = "asi"

    def get_latest_frame(self):
        return self.frame

    def get_zoom(self):
        return (1.0, 0.5, 0.5)

    def get_active_feed(self):
        return self.feed

    def set_calibration(self, calibration):
        self.calibration = calibration

    def distance_estimate(self, optics):
        return depth.estimate(self.feed, True, self.calibration, PIXEL_UM, optics)

    def __getattr__(self, name):            # anything else the routes touch
        return mock.Mock()


class RouteTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        from spots.web.routes import bp
        self.frame, self.marks = picture(100.0)
        self.settings = example_settings()
        self.worker = FakeWorker(self.frame)
        storage = mock.Mock()
        storage.get_range_state.return_value = "hot"
        app = Flask("spots.web.app", root_path=os.path.join(ROOT, "spots", "web"))
        app.register_blueprint(bp)
        app.config.update(SETTINGS=self.settings, WORKER=self.worker, STORAGE=storage,
                          MOUNT=mock.Mock())
        self.client = app.test_client()

    def calibrate_by_clicking(self):
        # Clicks land on the dashboard's downscaled stream, not the frame.
        scale = self.settings.web.stream_max_width / WIDTH
        p1, p2 = ([x * scale, y * scale] for x, y in self.marks)
        reply = self.client.post("/api/calibration", json={"p1": p1, "p2": p2, "distance": 40.0})
        self.assertEqual(reply.status_code, 200, reply.get_json())

    def test_clicking_the_stream_gives_the_right_distance(self):
        self.calibrate_by_clicking()
        self.assertEqual(self.worker.calibration.feed, "asi")
        reply = self.client.get("/api/depth").get_json()
        self.assertTrue(reply["available"], reply)
        self.assertAlmostEqual(reply["distance_m"], 100.0, delta=1.0)

    def test_correcting_saves_the_focal_length_and_resetting_clears_it(self):
        self.calibrate_by_clicking()
        reply = self.client.post("/api/depth/correct", json={"distance_m": 104.0})
        self.assertEqual(reply.status_code, 200, reply.get_json())
        self.assertAlmostEqual(reply.get_json()["distance_m"], 104.0, places=6)
        self.assertGreater(self.settings.optics.measured_focal_length_mm, FOCAL_MM)
        self.assertEqual(self.settings.optics.measured_at_m, 104.0)
        self.settings.save.assert_called()
        reply = self.client.post("/api/depth/reset").get_json()
        self.assertEqual(reply["focal_from"], "specs")
        self.assertEqual(self.settings.optics.measured_focal_length_mm, 0.0)

    def test_a_mistyped_distance_is_refused(self):
        self.calibrate_by_clicking()
        reply = self.client.post("/api/depth/correct", json={"distance_m": 1000.0})
        self.assertEqual(reply.status_code, 400)
        self.assertIn("against 662 mm", reply.get_json()["error"])
        self.assertEqual(self.settings.optics.measured_focal_length_mm, 0.0)
        for bad in ({"distance_m": "far"}, {"distance_m": 0}, {}):
            self.assertEqual(self.client.post("/api/depth/correct", json=bad).status_code, 400)

    def test_correcting_without_the_zwo_says_why(self):
        self.calibrate_by_clicking()
        self.worker.feed = "synthetic"
        reply = self.client.post("/api/depth/correct", json={"distance_m": 100.0})
        self.assertEqual(reply.status_code, 409)
        self.assertIn("ZWO", reply.get_json()["error"])

    def _save_settings(self, **changes):
        from werkzeug.datastructures import MultiDict
        page = _Fields()
        page.feed(self.client.get("/settings").get_data(as_text=True))
        form = MultiDict(page.fields)
        for key, value in changes.items():
            form[key] = value
        return self.client.post("/settings", data=form)

    def test_changing_the_optics_drops_a_measured_focal_length(self):
        self.settings.optics.measured_focal_length_mm = 690.0
        self.settings.optics.measured_at_m = 120.0
        self._save_settings()
        self.assertEqual(self.settings.optics.measured_focal_length_mm, 690.0)
        self._save_settings(**{"optics.reducer": "0.63"})
        self.assertEqual(self.settings.optics.reducer, 0.63)
        self.assertEqual(self.settings.optics.measured_focal_length_mm, 0.0)

    def test_impossible_optics_are_refused(self):
        reply = self._save_settings(**{"optics.reducer": "0"})
        self.assertIn("error=", reply.headers["Location"])
        self.assertEqual(self.settings.optics.reducer, 0.5)


SCRIPT = os.path.join(ROOT, "tests", "js", "depth_card.js")
TARGET = os.path.join(ROOT, "spots", "web", "static", "depth.js")


@unittest.skipIf(shutil.which("node") is None, "node isn't installed")
class CardTests(unittest.TestCase):
    def test_the_card(self):
        result = subprocess.run(["node", SCRIPT, TARGET], cwd=ROOT, capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)


class LayoutTests(unittest.TestCase):
    def test_the_card_is_on_live_view_by_default(self):
        from spots import layout
        self.assertIn("depth", layout.TILES)
        self.assertIn("depth", layout.DEFAULT_LAYOUT["columns"][0]["tiles"])


if __name__ == "__main__":
    unittest.main()
