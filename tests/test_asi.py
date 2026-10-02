"""The ZWO ASI camera, the live-feed switcher, and the settings behind them.

No camera is needed: ZWO's SDK is replaced by a fake that mirrors the
zwoasi binding's real interface (its constants, Camera methods and frame
shape), so the frame source is exercised end to end.
"""
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import numpy as np

from spots.camera import asi
from spots.camera.asi import AsiError, AsiFrameSource, next_exposure_us
from spots.camera.source import SwitchableFrameSource, SyntheticFrameSource
from spots.config import LIVE_CAMERAS, CameraConfig, Settings


# ---- a stand-in for the zwoasi binding ---------------------------------

class FakeCamera:
    """Records what it is told, and returns frames whose brightness follows
    the exposure the way a sensor's would."""

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
        level = min(255.0, exposure * self.sdk.brightness_per_us)
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
        source = self._source(sdk, exposure_ms=2.0, max_exposure_ms=50.0)
        source.start()
        camera = sdk.opened[0]
        self.assertTrue(wait_for(
            lambda: abs(camera.controls[sdk.ASI_EXPOSURE] * 0.01 - asi.TARGET_LEVEL)
            <= asi.DEADBAND, timeout=5.0), camera.controls)

    def test_auto_exposure_never_passes_the_longest_allowed(self):
        sdk = fake_sdk(brightness_per_us=0.0005)        # far too dark to ever reach
        source = self._source(sdk, exposure_ms=2.0, max_exposure_ms=8.0)
        source.start()
        camera = sdk.opened[0]
        self.assertTrue(wait_for(lambda: camera.controls[sdk.ASI_EXPOSURE] == 8000))
        time.sleep(0.1)
        self.assertEqual(camera.controls[sdk.ASI_EXPOSURE], 8000)

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
            settings = Settings.load(env_path=None)
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
    settings = Settings.load(env_path=None)
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
        "camera.asi_gain": "50", "camera.asi_auto_exposure": "on",
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
        self.settings = Settings.load(env_path=None)
        self.settings.save = mock.Mock()          # never touch the real config.yaml
        # Whatever this machine's config starts on, a live source would take
        # precedence over the camera these tests choose.
        self.settings.camera.source = "synthetic"
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

    def test_the_page_is_told_which_camera_is_live(self):
        self.settings.camera.live_camera = "asi"
        self.assertEqual(self.client.get("/api/feed").get_json()["live_camera"], "asi")


if __name__ == "__main__":
    unittest.main()
