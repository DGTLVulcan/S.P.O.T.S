"""Driving a Celestron NexStar mount from Live View.

No mount is needed: the hand controller is replaced by a fake that answers
as Celestron's NexStar protocol document says it does, including the extra
byte a pass-through command gets back when its motor doesn't answer.
"""
import os
import shutil
import subprocess
import tempfile
import threading
import time
import types
import unittest
from html.parser import HTMLParser
from unittest import mock

import numpy as np

from spots import mount as mount_module
from spots import worker as worker_module
from spots.config import DetectionConfig, Settings, TargetConfig
from spots.mount import (ALT, AZM, NEGATIVE, POSITIVE, MountController, MountError,
                         MountNotConnected, NexStar, candidate_ports, slew_command)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE_CONFIG = os.path.join(ROOT, "config.example.yaml")


def example_settings(**mount):
    """Settings from config.example.yaml; save() is a mock so nothing here
    can write the real config.yaml."""
    settings = Settings.load(EXAMPLE_CONFIG, env_path=None)
    settings.save = mock.Mock()
    for key, value in mount.items():
        setattr(settings.mount, key, value)
    return settings


# ---- a stand-in hand controller ----------------------------------------

class FakeHandControl:
    """Answers like a NexStar+ hand controller on a 4SE."""

    def __init__(self, model=11, version=(5, 33)):
        self.model = model
        self.version = version
        self.tracking = 1                 # tracking alt-az, as after an alignment
        self.motors_answer = True
        self.silent = False
        self.unplugged = False
        self.written = []
        # axis -> (direction, speed); speed 0 is stopped
        self.motion = {AZM: (POSITIVE, 0), ALT: (POSITIVE, 0)}

    def reply(self, cmd: bytes) -> bytes:
        if self.silent:
            return b""
        if cmd[:1] == b"K":
            return cmd[1:2] + b"#"
        if cmd == b"V":
            return bytes(self.version) + b"#"
        if cmd == b"m":
            return bytes([self.model]) + b"#"
        if cmd[:1] == b"T" and len(cmd) == 2:
            self.tracking = cmd[1]
            return b"#"
        if cmd[:1] == b"P" and len(cmd) == 8 and cmd[1] == 2:
            if not self.motors_answer:
                return b"\x01#"           # the protocol's "no answer" flag
            self.motion[cmd[2]] = (cmd[3], cmd[4])
            return b"#"
        return b"?#"

    def slews(self):
        return [c for c in self.written if c[:1] == b"P"]

    def moving(self, axis):
        return self.motion[axis][1] > 0


class FakeLink:
    """The parts of pyserial's Serial that NexStar uses."""

    def __init__(self, hand):
        self.hand = hand
        self.pending = bytearray()
        self.closed = False

    def reset_input_buffer(self):
        self.pending.clear()

    def write(self, data):
        if self.hand.unplugged:
            raise OSError("device disconnected")
        self.hand.written.append(bytes(data))
        self.pending += self.hand.reply(bytes(data))

    def read_until(self, expected=b"#"):
        end = self.pending.find(expected)
        if end < 0:                        # what a timeout looks like
            data = bytes(self.pending)
            self.pending.clear()
            return data
        data = bytes(self.pending[:end + 1])
        del self.pending[:end + 1]
        return data

    def close(self):
        self.closed = True


def opener_for(devices):
    """An opener that hands out a NexStar over each fake, by port name."""
    def open_(port):
        if port not in devices:
            raise MountError(f"Could not open {port} (no such file)")
        return NexStar(FakeLink(devices[port]), port)
    return open_


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def controller(hand=None, ports=("/dev/ttyUSB0",), clock=None, **mount):
    hand = hand or FakeHandControl()
    settings = example_settings(enabled=True, **mount)
    motion = []
    ctl = MountController(settings, on_motion=motion.append,
                          opener=opener_for({p: hand for p in ports}),
                          ports=lambda: list(ports), clock=clock or Clock(), watchdog=False)
    return ctl, hand, motion, settings


# ---- the protocol ------------------------------------------------------

class ProtocolTests(unittest.TestCase):
    def test_slew_bytes_match_celestrons_table(self):
        # "P" & chr(2) & chr(16 or 17) & chr(36 or 37) & chr(rate) & chr(0) x3
        self.assertEqual(list(slew_command(AZM, POSITIVE, 5)), [80, 2, 16, 36, 5, 0, 0, 0])
        self.assertEqual(list(slew_command(AZM, NEGATIVE, 9)), [80, 2, 16, 37, 9, 0, 0, 0])
        self.assertEqual(list(slew_command(ALT, POSITIVE, 1)), [80, 2, 17, 36, 1, 0, 0, 0])
        self.assertEqual(list(slew_command(ALT, NEGATIVE, 0)), [80, 2, 17, 37, 0, 0, 0, 0])

    def test_identifies_the_4se(self):
        hand = FakeHandControl()
        nexstar = NexStar(FakeLink(hand), "/dev/ttyUSB0")
        self.assertTrue(nexstar.echo())
        self.assertEqual(nexstar.model(), "NexStar 4/5 SE")
        self.assertEqual(nexstar.version(), "5.33")

    def test_a_motor_that_doesnt_answer_is_an_error(self):
        hand = FakeHandControl()
        hand.motors_answer = False
        with self.assertRaises(MountError) as caught:
            NexStar(FakeLink(hand)).slew(ALT, POSITIVE, 5)
        self.assertIn("altitude motor", str(caught.exception))

    def test_silence_and_a_lost_link_are_errors(self):
        hand = FakeHandControl()
        hand.silent = True
        with self.assertRaises(MountError):
            NexStar(FakeLink(hand)).slew(AZM, POSITIVE, 5)
        self.assertFalse(NexStar(FakeLink(hand)).echo())
        hand.silent, hand.unplugged = False, True
        with self.assertRaises(MountError) as caught:
            NexStar(FakeLink(hand)).stop(AZM)
        self.assertIn("Lost the hand controller", str(caught.exception))

    def test_ports_prefer_prolific_and_skip_built_in_uarts(self):
        port = lambda device, vid: types.SimpleNamespace(device=device, vid=vid)
        fake = types.SimpleNamespace(tools=types.SimpleNamespace(list_ports=types.SimpleNamespace(
            comports=lambda: [port("/dev/ttyAMA0", None), port("/dev/ttyACM0", 0x2341),
                              port("/dev/ttyUSB0", 0x067B)])))
        self.assertEqual(candidate_ports(fake), ["/dev/ttyUSB0", "/dev/ttyACM0"])

    def test_a_permission_error_says_how_to_fix_it(self):
        fake = types.SimpleNamespace(Serial=mock.Mock(
            side_effect=PermissionError(13, "Permission denied: '/dev/ttyUSB0'")))
        with self.assertRaises(MountError) as caught:
            NexStar.open("/dev/ttyUSB0", serial_module=fake)
        self.assertIn("dialout", str(caught.exception))


# ---- connecting ----------------------------------------------------------

class ConnectTests(unittest.TestCase):
    def test_connecting_stops_the_motors_and_turns_tracking_off(self):
        ctl, hand, _, _ = controller()
        hand.motion[AZM] = (POSITIVE, 4)          # left moving by someone
        status = ctl.connect()
        self.assertTrue(status["connected"])
        self.assertEqual(status["model"], "NexStar 4/5 SE")
        self.assertFalse(hand.moving(AZM) or hand.moving(ALT))
        self.assertEqual(hand.tracking, 0)

    def test_finds_the_port_that_answers(self):
        quiet, hand = FakeHandControl(), FakeHandControl()
        quiet.silent = True
        settings = example_settings(enabled=True)
        ctl = MountController(settings, opener=opener_for({"/dev/ttyUSB0": quiet,
                                                           "/dev/ttyUSB1": hand}),
                              ports=lambda: ["/dev/ttyUSB0", "/dev/ttyUSB1"], watchdog=False)
        self.assertEqual(ctl.connect()["port"], "/dev/ttyUSB1")

    def test_a_configured_port_is_the_only_one_tried(self):
        ctl, hand, _, _ = controller(ports=("/dev/ttyUSB0", "/dev/ttyUSB1"), port="/dev/ttyUSB1")
        ctl._ports = mock.Mock(side_effect=AssertionError("searched anyway"))
        self.assertEqual(ctl.connect()["port"], "/dev/ttyUSB1")

    def test_failures_say_what_to_check(self):
        ctl, _, _, _ = controller(ports=())
        with self.assertRaises(MountError) as caught:
            ctl.connect()
        self.assertIn("plug the hand controller", str(caught.exception))

        hand = FakeHandControl()
        hand.silent = True
        ctl, _, _, _ = controller(hand)
        with self.assertRaises(MountError) as caught:
            ctl.connect()
        self.assertIn("switched on", str(caught.exception))
        self.assertFalse(ctl.status()["connected"])
        self.assertIn("switched on", ctl.status()["error"])

    def test_turning_the_setting_on_connects_and_off_disconnects(self):
        ctl, _, _, settings = controller()
        ctl.apply_settings()
        self.assertTrue(wait_for(lambda: ctl.connected))
        settings.mount.enabled = False
        ctl.apply_settings()
        self.assertFalse(ctl.connected)


# ---- moving ------------------------------------------------------------------

class MoveTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.ctl, self.hand, self.motion, self.settings = controller(clock=self.clock)
        self.ctl.connect()
        self.hand.written.clear()

    def test_up_is_positive_altitude_and_right_positive_azimuth(self):
        self.ctl.hold("up", 5)
        self.ctl.hold("right", 5)
        self.assertEqual(self.hand.motion[ALT], (POSITIVE, 5))
        self.assertEqual(self.hand.motion[AZM], (POSITIVE, 5))
        self.ctl.stop_all()
        self.ctl.hold("down", 3)
        self.ctl.hold("left", 3)
        self.assertEqual(self.hand.motion[ALT], (NEGATIVE, 3))
        self.assertEqual(self.hand.motion[AZM], (NEGATIVE, 3))

    def test_the_reverse_settings_swap_each_axis(self):
        self.settings.mount.reverse_up_down = True
        self.ctl.hold("up", 5)
        self.ctl.hold("right", 5)
        self.assertEqual(self.hand.motion[ALT], (NEGATIVE, 5))
        self.assertEqual(self.hand.motion[AZM], (POSITIVE, 5))
        self.settings.mount.reverse_left_right = True
        self.ctl.hold("right", 5)
        self.assertEqual(self.hand.motion[AZM], (NEGATIVE, 5))

    def test_a_held_move_isnt_resent_each_heartbeat(self):
        for _ in range(5):
            self.ctl.hold("up", 5)
        self.assertEqual(len(self.hand.slews()), 1)
        self.ctl.hold("up", 7)                       # the speed changed
        self.assertEqual(len(self.hand.slews()), 2)
        self.assertEqual(self.hand.motion[ALT], (POSITIVE, 7))

    def test_letting_go_stops_only_that_move(self):
        self.ctl.hold("up", 5)
        self.ctl.hold("left", 5)
        self.ctl.release("down")                     # not what the axis is doing
        self.assertTrue(self.hand.moving(ALT))
        self.ctl.release("up")
        self.assertFalse(self.hand.moving(ALT))
        self.assertTrue(self.hand.moving(AZM))
        self.assertEqual(self.ctl.status()["moving"], {"azimuth": "left"})

    def test_detection_is_told_when_the_view_starts_and_stops_moving(self):
        self.ctl.hold("up", 5)
        self.ctl.hold("left", 5)
        self.ctl.release("up")
        self.assertEqual(self.motion, [True])
        self.ctl.release("left")
        self.assertEqual(self.motion, [True, False])

    def test_stop_sends_both_motors_a_stop(self):
        self.ctl.stop_all()
        stops = [c for c in self.hand.slews() if c[4] == 0]
        self.assertEqual(sorted(c[2] for c in stops), [AZM, ALT])

    def test_bad_requests_are_refused(self):
        with self.assertRaises(ValueError):
            self.ctl.hold("sideways", 5)
        with self.assertRaises(ValueError):
            self.ctl.hold("up", 10)
        self.ctl.close()
        with self.assertRaises(MountNotConnected):
            self.ctl.hold("up", 5)

    def test_a_lost_link_disconnects_and_stops_the_view_moving(self):
        self.ctl.hold("up", 5)
        self.hand.unplugged = True
        with self.assertRaises(MountError):
            self.ctl.hold("up", 8)
        status = self.ctl.status()
        self.assertFalse(status["connected"])
        self.assertIn("Lost the hand controller", status["error"])
        self.assertEqual(self.motion, [True, False])

    def test_closing_stops_the_motors(self):
        self.ctl.hold("up", 9)
        self.ctl.close()
        self.assertFalse(self.hand.moving(ALT))
        self.assertEqual(self.motion, [True, False])


class WatchdogTests(unittest.TestCase):
    def test_a_move_the_page_stops_asking_for_is_stopped(self):
        clock = Clock()
        ctl, hand, motion, _ = controller(clock=clock)
        ctl.connect()
        ctl.hold("up", 5)
        clock.now += 0.6
        ctl.tick()
        ctl.hold("up", 5)                            # a heartbeat
        clock.now += 0.9
        ctl.tick()
        self.assertTrue(hand.moving(ALT), "stopped while the page was still asking")
        clock.now += 0.2                             # 1.1 s since the last heartbeat
        ctl.tick()
        self.assertFalse(hand.moving(ALT))
        self.assertEqual(motion, [True, False])

    def test_the_watchdog_runs_on_its_own(self):
        hand = FakeHandControl()
        ctl = MountController(example_settings(enabled=True),
                              opener=opener_for({"/dev/ttyUSB0": hand}),
                              ports=lambda: ["/dev/ttyUSB0"])
        ctl.HOLD_S = 0.2
        self.addCleanup(ctl.close)
        ctl.connect()
        ctl.hold("left", 5)
        self.assertTrue(hand.moving(AZM))
        self.assertTrue(wait_for(lambda: not hand.moving(AZM), timeout=2.0),
                        "nothing stopped a move nobody was holding")

    def test_reconnecting_restarts_the_watchdog(self):
        hand = FakeHandControl()
        settings = example_settings(enabled=True)
        ctl = MountController(settings, opener=opener_for({"/dev/ttyUSB0": hand}),
                              ports=lambda: ["/dev/ttyUSB0"])
        ctl.HOLD_S = 0.2
        self.addCleanup(ctl.close)
        ctl.connect()
        ctl.close()
        ctl.connect()
        ctl.hold("up", 5)
        self.assertTrue(wait_for(lambda: not hand.moving(ALT), timeout=2.0))


def wait_for(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


# ---- detection while the view moves --------------------------------------

class ViewMovingTests(unittest.TestCase):
    def _worker(self, realign):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        source = mock.Mock()
        source.get_latest_frame.return_value = np.zeros((48, 64, 3), np.uint8)
        source.get_zoom.return_value = (1.0, 0.5, 0.5)
        worker = worker_module.DetectionWorker(
            source, mock.Mock(), TargetConfig(),
            DetectionConfig(realignment_enabled=realign, sample_fps=100), tmp)
        worker._detector.process_frame = mock.Mock(return_value=[])
        worker.rebaseline = mock.Mock()
        return worker

    def test_detection_waits_while_moving_and_while_settling(self):
        worker = self._worker(realign=False)
        with mock.patch.object(worker_module, "MOUNT_SETTLE_S", 0.15):
            worker.start()
            self.addCleanup(worker.stop)
            self.assertTrue(wait_for(lambda: worker._detector.process_frame.called))
            worker.set_view_moving(True)
            time.sleep(0.05)
            worker._detector.process_frame.reset_mock()
            time.sleep(0.15)
            self.assertFalse(worker._detector.process_frame.called, "diffed a moving view")
            worker.set_view_moving(False)
            time.sleep(0.08)
            self.assertFalse(worker._detector.process_frame.called, "diffed before it settled")
            self.assertTrue(wait_for(lambda: worker._detector.process_frame.called))

    def test_without_realignment_the_settled_view_becomes_the_reference(self):
        worker = self._worker(realign=False)
        clock = [10.0]
        with mock.patch.object(worker_module.time, "monotonic", lambda: clock[0]):
            worker.set_view_moving(True)
            worker.set_view_moving(False)
            self.assertTrue(worker._view_settling())
            clock[0] += worker_module.MOUNT_SETTLE_S
            self.assertFalse(worker._view_settling())
            self.assertFalse(worker._view_settling())
        worker.rebaseline.assert_called_once_with()

    def test_with_realignment_the_reference_is_kept(self):
        worker = self._worker(realign=True)
        clock = [10.0]
        with mock.patch.object(worker_module.time, "monotonic", lambda: clock[0]):
            worker.set_view_moving(True)
            worker.set_view_moving(False)
            clock[0] += worker_module.MOUNT_SETTLE_S
            self.assertFalse(worker._view_settling())
        worker.rebaseline.assert_not_called()


# ---- the web side ----------------------------------------------------------

class _Fields(HTMLParser):
    """The values a browser would submit from a form."""

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


class WebTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        from spots.web.routes import bp
        self.hand = FakeHandControl()
        self.settings = example_settings(enabled=True)
        self.mount = MountController(self.settings, opener=opener_for({"/dev/ttyUSB0": self.hand}),
                                     ports=lambda: ["/dev/ttyUSB0"], watchdog=False)
        storage = mock.Mock()
        storage.get_range_state.return_value = "hot"
        storage.get_layout.return_value = {"columns": [{"weight": 1, "flow": "stack",
                                                        "tiles": ["feed", "mount"]}],
                                           "hidden": [], "sizes": {}}
        worker = mock.Mock()
        worker.get_active_feed.return_value = "synthetic"
        app = Flask("spots.web.app", root_path=os.path.join(ROOT, "spots", "web"))
        app.register_blueprint(bp)
        app.config.update(SETTINGS=self.settings, WORKER=worker, STORAGE=storage,
                          MOUNT=self.mount)
        self.client = app.test_client()

    def test_connect_move_and_stop(self):
        self.assertEqual(self.client.post("/api/mount/connect").status_code, 200)
        reply = self.client.post("/api/mount/move", json={"direction": "up", "speed": 6})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.get_json()["moving"], {"altitude": "up"})
        self.assertEqual(self.hand.motion[ALT], (POSITIVE, 6))
        # As sendBeacon sends it: no JSON content type.
        reply = self.client.post("/api/mount/stop", data="{}", content_type="text/plain")
        self.assertEqual(reply.status_code, 200)
        self.assertFalse(self.hand.moving(ALT))

    def test_bad_moves_are_refused(self):
        self.client.post("/api/mount/connect")
        for body in ({"direction": "sideways", "speed": 5}, {"direction": "up", "speed": 0},
                     {"direction": "up", "speed": "fast"}, {"direction": "up"}):
            self.assertEqual(self.client.post("/api/mount/move", json=body).status_code, 400, body)
        self.assertEqual(self.hand.slews()[-1][4], 0, "something moved")

    def test_moving_with_nothing_connected_says_so(self):
        reply = self.client.post("/api/mount/move", json={"direction": "up", "speed": 5})
        self.assertEqual(reply.status_code, 409)
        self.assertIn("isn't connected", reply.get_json()["error"])

    def test_a_failed_connect_reports_why(self):
        self.hand.silent = True
        reply = self.client.post("/api/mount/connect")
        self.assertEqual(reply.status_code, 502)
        self.assertIn("No NexStar hand controller answered", reply.get_json()["error"])

    def test_live_view_has_the_card_only_when_enabled(self):
        self.assertIn('id="mount-card"', self.client.get("/").get_data(as_text=True))
        self.settings.mount.enabled = False
        self.assertNotIn('id="mount-card"', self.client.get("/").get_data(as_text=True))

    def test_the_settings_page_round_trips(self):
        from werkzeug.datastructures import MultiDict
        from spots.web.routes import _apply_settings_form
        self.settings.mount.port = "/dev/ttyUSB3"
        self.settings.mount.reverse_up_down = True
        page = _Fields()
        page.feed(self.client.get("/settings").get_data(as_text=True))
        form = MultiDict(page.fields)
        self.assertEqual(form.get("mount.port"), "/dev/ttyUSB3")

        # Saved untouched, nothing changes.
        before = Settings.load(EXAMPLE_CONFIG, env_path=None)
        self.assertEqual(_apply_settings_form(self.settings, form), [])
        self.assertEqual(self.settings.mount.port, "/dev/ttyUSB3")
        self.assertTrue(self.settings.mount.reverse_up_down)
        self.assertTrue(self.settings.mount.enabled)
        self.assertEqual(self.settings.detection, before.detection)

        # Then the mount switched off with the arrows put back.
        form.pop("mount.enabled")
        form.pop("mount.reverse_up_down")
        form["mount.port"] = "  "
        self.assertEqual(_apply_settings_form(self.settings, form), [])
        self.assertFalse(self.settings.mount.enabled)
        self.assertFalse(self.settings.mount.reverse_up_down)
        self.assertEqual(self.settings.mount.port, "")
        self.settings.save.assert_called()               # the mock, never config.yaml


# ---- the page's controls ---------------------------------------------------

SCRIPT = os.path.join(ROOT, "tests", "js", "mount_pad.js")
TARGET = os.path.join(ROOT, "spots", "web", "static", "mount.js")


@unittest.skipIf(shutil.which("node") is None, "node isn't installed")
class PadTests(unittest.TestCase):
    def _run(self, path):
        return subprocess.run(["node", SCRIPT, path], cwd=ROOT, capture_output=True,
                              text=True, encoding="utf-8", timeout=60)

    def test_holding_and_letting_go(self):
        result = self._run(TARGET)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def _broken(self, old, new):
        with open(TARGET, encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(source.count(old), 1, "mount.js has changed shape: " + old)
        path = os.path.join(ROOT, "tests", "js", "_broken_mount.js")
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(source.replace(old, new))
            return self._run(path)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_a_pad_without_the_heartbeat_would_be_caught(self):
        result = self._broken("held.set(direction, { timer: setInterval(() => send(direction), HEARTBEAT_MS), button });",
                              "held.set(direction, { timer: 0, button });")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("re-send", result.stdout)

    def test_a_pad_that_ignored_losing_focus_would_be_caught(self):
        result = self._broken('window.addEventListener("blur", () => {\n    keysHeld.clear();\n'
                              '    releaseAll(false);\n  });', "")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("window blurred", result.stdout)

    def test_arrow_keys_that_ignored_the_toggle_would_be_caught(self):
        result = self._broken("if (!direction || !keysToggle.checked) return;",
                              "if (!direction) return;")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("toggle off", result.stdout)

    def test_arrow_keys_that_took_over_fields_would_be_caught(self):
        result = self._broken(" || arrowsBusy()) return;", ") return;")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the arrow key moved the mount", result.stdout)

class LayoutTests(unittest.TestCase):
    def test_the_card_sits_under_the_feed_by_default(self):
        from spots import layout
        self.assertIn("mount", layout.TILES)
        tiles = layout.DEFAULT_LAYOUT["columns"][0]["tiles"]
        self.assertEqual(tiles.index("mount"), tiles.index("feed") + 1)


if __name__ == "__main__":
    unittest.main()
