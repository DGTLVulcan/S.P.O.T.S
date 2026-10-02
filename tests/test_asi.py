"""The ZWO ASI camera, the live-feed switcher, and the settings behind them.

No camera is needed: ZWO's SDK is replaced by a fake that mirrors the
zwoasi binding's real interface (its constants, Camera methods and frame
shape), so the frame source is exercised end to end.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import numpy as np

from spots.camera import asi
from spots.camera.asi import (AsiError, AsiFrameSource, meter, next_exposure_us,
                              next_settings)
from spots.camera.source import SwitchableFrameSource, SyntheticFrameSource
from spots.config import LIVE_CAMERAS, CameraConfig, Settings

EXAMPLE_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "config.example.yaml")


def example_settings():
    """Settings from config.example.yaml whose save() is a mock: the settings
    form saves on success, and a plain save() writes the real config.yaml."""
    settings = Settings.load(EXAMPLE_CONFIG, env_path=None)
    settings.save = mock.Mock()
    return settings


# ---- a stand-in for the zwoasi binding ---------------------------------

class FakeCamera:
    """Records what it is told, and returns frames whose brightness follows
    exposure and gain the way a sensor's would: in proportion to exposure,
    and by ZWO's 0.1 dB gain units."""

    WIDTH, HEIGHT = 64, 32

    def __init__(self, sdk, id_):
        self.sdk = sdk
        self.calls = []
        self.controls = {}
        self.capturing = False
        self.closed = False
        self.timeouts = []

    def get_camera_property(self):
        return {"Name": "ZWO ASI290MC", "MaxWidth": 1936, "MaxHeight": 1096}

    def get_controls(self):
        return {"Exposure": {"MinValue": 32, "MaxValue": 2_000_000_000},
                "Gain": {"MinValue": 0, "MaxValue": 600}}

    def set_roi(self, start_x=None, start_y=None, width=None, height=None,
                bins=None, image_type=None):
        self.calls.append(("set_roi", bins, image_type))

    def set_control_value(self, control_type, value, auto=False):
        self.controls[control_type] = value
        self.calls.append(("set_control_value", control_type, value, auto))

    def start_video_capture(self):
        self.capturing = True

    def stop_video_capture(self):
        self.capturing = False

    def capture_video_frame(self, buffer_=None, filename=None, timeout=None):
        self.timeouts.append(timeout)
        if self.sdk.fail_next_capture:
            self.sdk.fail_next_capture -= 1
            raise RuntimeError("ASI_ERROR_TIMEOUT")
        exposure = self.controls.get(self.sdk.ASI_EXPOSURE, 1000)
        gain = self.controls.get(self.sdk.ASI_GAIN, 0)
        level = min(255.0, exposure * self.sdk.brightness_per_us * 10 ** (gain / 200))
        return np.full((self.HEIGHT, self.WIDTH, 3), level, dtype=np.uint8)

    def close(self):
        self.closed = True


def fake_sdk(cameras=1, brightness_per_us=0.02):
    sdk = types.SimpleNamespace(
        ASI_IMG_RAW8=0, ASI_IMG_RGB24=1, ASI_GAIN=0, ASI_EXPOSURE=1,
        brightness_per_us=brightness_per_us, fail_next_capture=0, opened=[])
    sdk.get_num_cameras = lambda: cameras

    def camera(id_):
        cam = FakeCamera(sdk, id_)
        sdk.opened.append(cam)
        return cam

    sdk.Camera = camera
    return sdk


def wait_for(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


# ---- the exposure control law -----------------------------------------

class ExposureTests(unittest.TestCase):
    def test_holds_still_inside_the_deadband(self):
        self.assertEqual(next_exposure_us(2000, asi.TARGET_LEVEL + 5, 32, 20000), 2000)

    def test_too_dark_lengthens_and_too_bright_shortens(self):
        self.assertGreater(next_exposure_us(2000, 40, 32, 20000), 2000)
        self.assertLess(next_exposure_us(2000, 220, 32, 20000), 2000)

    def test_steps_are_bounded(self):
        # Never more than a factor of two either way in one step.
        self.assertLessEqual(next_exposure_us(1000, 0.0, 32, 10**9), 2000)
        self.assertGreaterEqual(next_exposure_us(1000, 255.0, 32, 10**9), 500)

    def test_never_leaves_its_limits(self):
        self.assertEqual(next_exposure_us(19000, 5, 32, 20000), 20000)
        self.assertEqual(next_exposure_us(40, 250, 32, 20000), 32)

    def test_settles_on_a_linear_sensor_without_hunting(self):
        # Brightness proportional to exposure, as a sensor below saturation.
        exposure, history = 200.0, []
        for _ in range(40):
            level = min(255.0, exposure * 0.05)
            history.append(level)
            exposure = next_exposure_us(exposure, level, 32, 20000)
        settled = history[-10:]
        self.assertTrue(all(abs(l - asi.TARGET_LEVEL) <= asi.DEADBAND for l in settled),
                        settled)
        self.assertEqual(len(set(round(l) for l in settled)), 1, "it kept moving once settled")


class GainTests(unittest.TestCase):
    """Exposure first, gain only once exposure can go no longer."""

    LIMITS = dict(floor_us=32, ceiling_us=20000, min_gain=50, max_gain=300)

    def test_a_dark_picture_lengthens_the_exposure_before_adding_gain(self):
        exposure, gain = next_settings(5000, 50, 30, **self.LIMITS)
        self.assertGreater(exposure, 5000)
        self.assertEqual(gain, 50)

    def test_gain_rises_once_the_exposure_is_at_its_longest(self):
        exposure, gain = next_settings(20000, 50, 30, **self.LIMITS)
        self.assertEqual(exposure, 20000)
        self.assertGreater(gain, 50)

    def test_a_bright_picture_takes_gain_off_before_shortening(self):
        exposure, gain = next_settings(20000, 200, 220, **self.LIMITS)
        self.assertEqual(exposure, 20000)
        self.assertLess(gain, 200)
        exposure, gain = next_settings(20000, 50, 220, **self.LIMITS)
        self.assertLess(exposure, 20000)
        self.assertEqual(gain, 50)

    def test_nothing_moves_past_its_limits(self):
        self.assertEqual(next_settings(20000, 300, 5, **self.LIMITS), (20000, 300))
        self.assertEqual(next_settings(32, 50, 250, **self.LIMITS), (32, 50))

    def test_holds_still_inside_the_deadband(self):
        self.assertEqual(next_settings(8000, 120, asi.TARGET_LEVEL, **self.LIMITS),
                         (8000, 120))

    def _settle(self, exposure, gain, light, steps=80):
        for _ in range(steps):
            level = min(255.0, exposure * light * 10 ** (gain / 200))
            exposure, gain = next_settings(exposure, gain, level, **self.LIMITS)
        return exposure, gain, min(255.0, exposure * light * 10 ** (gain / 200))

    def test_settles_in_poor_light_using_gain(self):
        # Too dark to reach the target on exposure alone.
        exposure, gain, level = self._settle(2000, 50, light=0.0012)
        self.assertEqual(exposure, 20000)
        self.assertGreater(gain, 50)
        self.assertLessEqual(abs(level - asi.TARGET_LEVEL), asi.DEADBAND)

    def test_hands_gain_back_when_the_light_returns(self):
        exposure, gain, _ = self._settle(2000, 50, light=0.0012)
        exposure, gain, level = self._settle(exposure, gain, light=0.05)
        self.assertEqual(gain, 50, "gain should go before the exposure shortens")
        self.assertLess(exposure, 20000)
        self.assertLessEqual(abs(level - asi.TARGET_LEVEL), asi.DEADBAND)

    def test_meters_the_middle_not_a_bright_edge(self):
        frame = np.full((100, 200, 3), 250, np.uint8)      # bright sky all round
        frame[25:75, 50:150] = 40                           # the target, centred
        self.assertAlmostEqual(meter(frame), 40.0, delta=0.5)


# ---- loading the SDK ---------------------------------------------------

class LoadSdkTests(unittest.TestCase):
    def test_a_missing_binding_says_what_to_install(self):
        with mock.patch.dict(sys.modules, {"zwoasi": None}):
            with self.assertRaises(AsiError) as caught:
                asi.load_sdk()
        self.assertIn("requirements.txt", str(caught.exception))

    def test_a_missing_library_points_at_the_installer(self):
        module = types.SimpleNamespace(init=mock.Mock(side_effect=OSError("not found")))
        with mock.patch.dict(sys.modules, {"zwoasi": module}):
            with self.assertRaises(AsiError) as caught:
                asi.load_sdk("/opt/zwo/libASICamera2.so")
        self.assertIn("install-asi-sdk.sh", str(caught.exception))
        self.assertIn("/opt/zwo/libASICamera2.so", str(caught.exception))
        module.init.assert_called_once_with("/opt/zwo/libASICamera2.so")

    def test_a_blank_path_lets_the_binding_search(self):
        module = types.SimpleNamespace(init=mock.Mock())
        with mock.patch.dict(sys.modules, {"zwoasi": module}):
            asi.load_sdk("")
        module.init.assert_called_once_with(None)


# ---- the frame source --------------------------------------------------

class AsiFrameSourceTests(unittest.TestCase):
    def _source(self, sdk, **kwargs):
        source = AsiFrameSource(fps=200, **kwargs)
        patcher = mock.patch.object(asi, "load_sdk", return_value=sdk)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(source.stop)
        return source

    def test_opens_the_camera_full_frame_in_bgr(self):
        sdk = fake_sdk()
        source = self._source(sdk, exposure_ms=2.0, gain=60)
        source.start()
        camera = sdk.opened[0]
        self.assertIn(("set_roi", 1, sdk.ASI_IMG_RGB24), camera.calls)
        self.assertEqual(camera.controls[sdk.ASI_GAIN], 60)
        # The SDK takes exposure in microseconds.
        self.assertEqual(camera.controls[sdk.ASI_EXPOSURE], 2000)
        self.assertTrue(camera.capturing)
        self.assertEqual(source.name, "ZWO ASI290MC")

    def test_frames_arrive_as_copies(self):
        sdk = fake_sdk()
        source = self._source(sdk, auto_exposure=False)
        self.assertFalse(source.connected)
        source.start()
        self.assertTrue(wait_for(lambda: source.get_latest_frame() is not None))
        frame = source.get_latest_frame()
        self.assertEqual(frame.shape, (FakeCamera.HEIGHT, FakeCamera.WIDTH, 3))
        frame[:] = 0                                  # the caller draws on it
        self.assertTrue(source.get_latest_frame().any(), "the stored frame was shared")
        self.assertTrue(source.connected)

    def test_every_read_has_a_timeout(self):
        # The binding waits forever without one, which an unplugged cable
        # would turn into a hang.
        sdk = fake_sdk()
        source = self._source(sdk, auto_exposure=False, exposure_ms=4.0)
        source.start()
        camera = sdk.opened[0]
        self.assertTrue(wait_for(lambda: len(camera.timeouts) >= 2))
        # ZWO's suggestion: twice the exposure plus 500 ms.
        self.assertEqual(camera.timeouts[0], 508)

    def test_no_camera_fails_where_it_can_be_reported(self):
        source = self._source(fake_sdk(cameras=0))
        with self.assertRaises(AsiError) as caught:
            source.start()
        self.assertIn("No ZWO ASI camera", str(caught.exception))

    def test_a_camera_that_wont_open_is_an_asi_error(self):
        sdk = fake_sdk()
        sdk.Camera = mock.Mock(side_effect=RuntimeError("ASI_ERROR_CAMERA_REMOVED"))
        with self.assertRaises(AsiError):
            self._source(sdk).start()

    def test_reconnects_after_losing_the_camera(self):
        sdk = fake_sdk()
        source = self._source(sdk, auto_exposure=False)
        source._RECONNECT_DELAY_S = 0.01
        source.start()
        self.assertTrue(wait_for(lambda: source.get_latest_frame() is not None))
        sdk.fail_next_capture = 1
        self.assertTrue(wait_for(lambda: len(sdk.opened) >= 2), "it never reopened")
        self.assertTrue(sdk.opened[0].closed)
        self.assertTrue(wait_for(lambda: source.connected))

    def test_auto_exposure_brings_a_dark_picture_up(self):
        sdk = fake_sdk(brightness_per_us=0.01)          # 2 ms reads as 20/255
        source = self._source(sdk, exposure_ms=2.0, max_exposure_ms=50.0, gain=0)
        source.start()
        camera = sdk.opened[0]
        self.assertTrue(wait_for(
            lambda: abs(camera.controls[sdk.ASI_EXPOSURE] * 0.01 - asi.TARGET_LEVEL)
            <= asi.DEADBAND, timeout=5.0), camera.controls)

    def test_auto_exposure_never_passes_the_longest_allowed(self):
        sdk = fake_sdk(brightness_per_us=0.0005)        # far too dark to ever reach
        source = self._source(sdk, exposure_ms=2.0, max_exposure_ms=8.0, gain=0, max_gain=0)
        source.start()
        camera = sdk.opened[0]
        self.assertTrue(wait_for(lambda: camera.controls[sdk.ASI_EXPOSURE] == 8000))
        time.sleep(0.1)
        self.assertEqual(camera.controls[sdk.ASI_EXPOSURE], 8000)

    def test_poor_light_raises_the_gain(self):
        sdk = fake_sdk(brightness_per_us=0.0012)         # dark even at 20 ms
        source = self._source(sdk, exposure_ms=2.0, max_exposure_ms=20.0,
                              gain=50, max_gain=300)
        source.start()
        camera = sdk.opened[0]
        self.assertTrue(wait_for(lambda: camera.controls[sdk.ASI_GAIN] > 50, timeout=5.0))
        self.assertEqual(camera.controls[sdk.ASI_EXPOSURE], 20000)
        self.assertTrue(wait_for(
            lambda: source.status()["level"] is not None
            and abs(source.status()["level"] - asi.TARGET_LEVEL) <= asi.DEADBAND,
            timeout=5.0), source.status())

    def test_status_reports_exposure_gain_and_brightness(self):
        sdk = fake_sdk(brightness_per_us=0.02)
        source = self._source(sdk, auto_exposure=False, exposure_ms=3.0, gain=0)
        source.start()
        self.assertTrue(wait_for(lambda: source.status()["level"] is not None))
        status = source.status()
        self.assertEqual(status["exposure_ms"], 3.0)
        self.assertEqual(status["gain"], 0)
        self.assertAlmostEqual(status["level"], 60.0, delta=1.0)   # 3000 us x 0.02
        self.assertTrue(status["connected"])
        self.assertFalse(status["auto"])

    def test_gain_limits_stay_inside_the_cameras(self):
        sdk = fake_sdk()
        source = self._source(sdk, gain=50, max_gain=5000)  # past the camera's 600
        source.start()
        self.assertEqual(source.status()["max_gain"], 600)

    def test_a_fixed_exposure_stays_fixed(self):
        sdk = fake_sdk(brightness_per_us=0.01)
        source = self._source(sdk, auto_exposure=False, exposure_ms=2.0)
        source.start()
        camera = sdk.opened[0]
        self.assertTrue(wait_for(lambda: len(camera.timeouts) >= 10))
        exposures = [c[2] for c in camera.calls
                     if c[0] == "set_control_value" and c[1] == sdk.ASI_EXPOSURE]
        self.assertEqual(exposures, [2000])

    def test_stop_releases_the_camera(self):
        sdk = fake_sdk()
        source = self._source(sdk)
        source.start()
        source.stop()
        self.assertFalse(sdk.opened[0].capturing)
        self.assertTrue(sdk.opened[0].closed)


# ---- switching between feeds ------------------------------------------

class FakeLive:
    def __init__(self, fail=False):
        self.started = False
        self.connected = True
        self.fail = fail

    def start(self):
        if self.fail:
            raise AsiError("No ZWO ASI camera found on USB")
        self.started = True

    def stop(self):
        pass

    def get_latest_frame(self):
        return np.zeros((4, 4, 3), np.uint8)


class SwitcherTests(unittest.TestCase):
    def _switcher(self, asi_factory=None, zcam_factory=None):
        return SwitchableFrameSource(
            SyntheticFrameSource(width=64, height=48),
            {"zcam": zcam_factory or (lambda: (FakeLive(), "client")),
             "asi": asi_factory or (lambda: (FakeLive(), None))})

    def test_switching_to_the_asi_builds_and_starts_it(self):
        switcher = self._switcher()
        self.assertFalse(switcher.is_live_connected())
        switcher.switch_to("asi")
        self.assertEqual(switcher.get_active(), "asi")
        self.assertTrue(switcher.is_live_connected())
        self.assertIsNone(switcher.get_zcam_client())

    def test_a_camera_that_fails_is_tried_again_next_time(self):
        calls = []

        def factory():
            calls.append(1)
            return FakeLive(fail=len(calls) == 1), None

        switcher = self._switcher(asi_factory=factory)
        with self.assertRaises(AsiError):
            switcher.switch_to("asi")
        self.assertEqual(switcher.get_active(), "synthetic")
        switcher.switch_to("asi")
        self.assertEqual(len(calls), 2)
        self.assertEqual(switcher.get_active(), "asi")

    def test_an_unknown_feed_is_refused(self):
        with self.assertRaises(ValueError):
            self._switcher().switch_to("webcam")

    def test_the_zcam_still_reports_by_its_client(self):
        switcher = self._switcher(zcam_factory=lambda: (object.__new__(SyntheticFrameSource), "c"))
        with mock.patch.object(SyntheticFrameSource, "start"):
            switcher.switch_to("zcam")
        self.assertEqual(switcher.get_zcam_client(), "c")
        self.assertTrue(switcher.is_live_connected())


# ---- configuration -----------------------------------------------------

class CameraConfigTests(unittest.TestCase):
    def test_a_live_source_names_the_live_camera(self):
        self.assertEqual(CameraConfig(source="asi", live_camera="zcam").live, "asi")
        self.assertEqual(CameraConfig(source="synthetic", live_camera="asi").live, "asi")
        self.assertEqual(CameraConfig().live, "zcam")
        self.assertEqual(LIVE_CAMERAS, ("zcam", "asi"))

    def test_asi_settings_survive_a_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            settings = Settings.load(EXAMPLE_CONFIG, env_path=None)
            settings.camera.live_camera = "asi"
            settings.camera.asi_gain = 120
            settings.camera.asi_max_exposure_ms = 12.5
            settings.save(path)
            again = Settings.load(path, env_path=None)
        self.assertEqual(again.camera.live_camera, "asi")
        self.assertEqual(again.camera.asi_gain, 120)
        self.assertEqual(again.camera.asi_max_exposure_ms, 12.5)


# ---- the settings form and the feed route ------------------------------

def _form(**overrides):
    from werkzeug.datastructures import MultiDict
    settings = example_settings()
    fields = {
        "target.unit_name": "cm", "target.width_units": "59",
        "target.best_subgroup_sizes": "3, 5", "target.best_subgroup_max_shots": "30",
        "detection.diff_threshold": "20", "detection.min_hole_area_px": "20",
        "detection.max_hole_area_px": "400", "detection.min_circularity": "0.5",
        "detection.min_shot_spacing_px": "12", "detection.burn_in_margin_px": "3",
        "detection.debounce_frames": "2", "detection.sample_fps": "3",
        "detection.realignment_min_matches": "15", "detection.realignment_method": "orb",
        "camera.source": "synthetic", "camera.live_camera": "zcam", "camera.ip": "",
        "camera.stream_width": "1920", "camera.stream_height": "1080",
        "camera.stream_bitrate": "8000000", "camera.asi_sdk_path": "",
        "camera.asi_exposure_ms": "2", "camera.asi_max_exposure_ms": "20",
        "camera.asi_gain": "50", "camera.asi_max_gain": "300",
        "camera.asi_auto_exposure": "on",
    }
    fields.update(overrides)
    return settings, MultiDict({k: v for k, v in fields.items() if v is not None})


class SettingsFormTests(unittest.TestCase):
    def _apply(self, **overrides):
        from spots.web.routes import _apply_settings_form
        settings, form = _form(**overrides)
        return settings, _apply_settings_form(settings, form)

    def test_starting_on_the_live_camera_records_which(self):
        settings, errors = self._apply(**{"camera.source": "live",
                                          "camera.live_camera": "asi"})
        self.assertEqual(errors, [])
        settings.save.assert_called_once_with()          # the mock, not config.yaml
        self.assertEqual(settings.camera.source, "asi")
        self.assertEqual(settings.camera.live_camera, "asi")

    def test_the_asi_fields_are_saved(self):
        settings, errors = self._apply(**{"camera.asi_gain": "90",
                                          "camera.asi_exposure_ms": "0.5",
                                          "camera.asi_auto_exposure": None})
        self.assertEqual(errors, [])
        self.assertEqual(settings.camera.asi_gain, 90)
        self.assertEqual(settings.camera.asi_exposure_ms, 0.5)
        self.assertFalse(settings.camera.asi_auto_exposure)

    def test_the_highest_gain_is_saved_and_checked(self):
        settings, errors = self._apply(**{"camera.asi_max_gain": "250"})
        self.assertEqual(errors, [])
        self.assertEqual(settings.camera.asi_max_gain, 250)
        _, errors = self._apply(**{"camera.asi_gain": "100", "camera.asi_max_gain": "40"})
        self.assertTrue(any("highest gain" in e for e in errors), errors)

    def test_impossible_values_are_refused(self):
        _, errors = self._apply(**{"camera.asi_exposure_ms": "0",
                                   "camera.live_camera": "webcam"})
        self.assertTrue(any("exposure" in e for e in errors), errors)
        self.assertTrue(any("Live camera" in e for e in errors), errors)


class FeedRouteTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        from spots.web.routes import bp
        app = Flask("test")
        app.register_blueprint(bp)
        self.settings = example_settings()
        self.worker = mock.Mock()
        self.worker.get_active_feed.return_value = "synthetic"
        app.config.update(SETTINGS=self.settings, WORKER=self.worker, STORAGE=mock.Mock())
        self.client = app.test_client()

    def test_live_means_the_fitted_camera(self):
        self.settings.camera.live_camera = "asi"
        reply = self.client.post("/api/feed", json={"target": "live"})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.get_json()["active"], "asi")
        self.worker.switch_feed.assert_called_once_with("asi")

    def test_a_missing_camera_is_reported_by_name(self):
        self.settings.camera.live_camera = "asi"
        self.worker.switch_feed.side_effect = AsiError("No ZWO ASI camera found on USB")
        reply = self.client.post("/api/feed", json={"target": "live"})
        self.assertEqual(reply.status_code, 502)
        self.assertIn("ZWO ASI camera", reply.get_json()["error"])

    def test_an_unknown_target_is_refused(self):
        self.assertEqual(self.client.post("/api/feed", json={"target": "webcam"}).status_code,
                         400)
        self.worker.switch_feed.assert_not_called()

    def test_camera_status_comes_from_the_live_camera(self):
        self.worker.get_camera_status.return_value = {"exposure_ms": 20.0, "gain": 120}
        reply = self.client.get("/api/camera/status").get_json()
        self.assertTrue(reply["available"])
        self.assertEqual(reply["gain"], 120)
        self.worker.get_camera_status.return_value = None
        self.assertFalse(self.client.get("/api/camera/status").get_json()["available"])

    def test_the_page_is_told_which_camera_is_live(self):
        self.settings.camera.live_camera = "asi"
        self.assertEqual(self.client.get("/api/feed").get_json()["live_camera"], "asi")



ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipIf(shutil.which("node") is None, "node isn't installed")
class ReadoutTests(unittest.TestCase):
    """What the settings page says about the picture's brightness."""

    def setUp(self):
        result = subprocess.run(
            ["node", os.path.join(ROOT, "tests", "js", "camera_status.js"),
             os.path.join(ROOT, "spots", "web", "static", "camera_status.js")],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.views = json.loads(result.stdout)

    def test_a_settled_picture_is_fine(self):
        self.assertEqual(self.views["settled"]["level"], "ok")
        self.assertIn("6.40 ms", self.views["settled"]["text"])
        self.assertIn("gain 50", self.views["settled"]["text"])

    def test_says_why_a_picture_is_dark(self):
        self.assertIn("gain is rising", self.views["gaining"]["text"])
        self.assertIn("Auto exposure is off", self.views["fixed_dark"]["text"])

    def test_out_of_light_says_what_to_change(self):
        view = self.views["out_of_light"]
        self.assertEqual(view["level"], "bad")
        self.assertIn("Highest gain", view["text"])
        self.assertIn("(longest)", view["text"])
        self.assertIn("(highest)", view["text"])

    def test_without_the_camera_it_says_so(self):
        self.assertIn("ZWO ASI camera is the live feed", self.views["none"]["text"])


class SettingsMarkupTests(unittest.TestCase):
    def test_the_camera_panel_carries_the_readout_and_gain_ceiling(self):
        with io.open(os.path.join(ROOT, "spots", "web", "templates", "settings.html"),
                     encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('id="asi-status"', html)
        self.assertIn('name="camera.asi_max_gain"', html)
        self.assertIn("camera_status.js", html)

if __name__ == "__main__":
    unittest.main()
