"""Drives ballistics.js under a stub DOM, through node.

The page has three async loaders that can land after the thing they would
overwrite has already been put on screen -- filling the DOPE card from a
solution hit exactly that, showing the rows and then wiping them when the
saved-card fetch returned. That class of bug is invisible to a Python test
and to `node --check`, so it gets driven for real.

Skipped rather than failed where node isn't installed: the Pi doesn't need
it to run S.P.O.T.S.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from spots import ballistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tests", "js", "dope_fill.js")
SIM_SCRIPT = os.path.join(ROOT, "tests", "js", "sim_playback.js")
FRAME_SCRIPT = os.path.join(ROOT, "tests", "js", "sim_framing.js")
TARGET = os.path.join(ROOT, "spots", "web", "static", "ballistics.js")
SIM_TARGET = os.path.join(ROOT, "spots", "web", "static", "ballistics_sim.js")


@unittest.skipIf(shutil.which("node") is None, "node isn't installed")
class DopeFillTests(unittest.TestCase):
    def _run(self, path):
        return subprocess.run(["node", SCRIPT, path], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)

    def test_filling_the_card_from_a_solution_keeps_the_rows(self):
        result = self._run(TARGET)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_the_check_would_catch_the_bug_coming_back(self):
        # A regression test that cannot fail is worth nothing, so this
        # reintroduces the fault and insists the harness notices.
        with open(TARGET, encoding="utf-8") as handle:
            source = handle.read()
        broken = source.replace(
            'if (name === "dope" && !state.dopeLoaded) loadDope();',
            'if (name === "dope") loadDope();')
        self.assertNotEqual(broken, source, "the guard being tested has moved")

        path = os.path.join(ROOT, "tests", "js", "_broken_ballistics.js")
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(broken)
            result = self._run(path)
            self.assertNotEqual(result.returncode, 0,
                                "the stub DOM no longer reproduces the bug")
            self.assertIn("FAIL", result.stdout)
        finally:
            if os.path.exists(path):
                os.unlink(path)


@unittest.skipIf(shutil.which("node") is None, "node isn't installed")
class SimulationTests(unittest.TestCase):
    """Stopping has to show the completed flight rather than freezing the
    bullet where it happens to be -- looking at the trajectory is the whole
    reason for stopping."""

    def test_playback_stop_and_replay(self):
        result = subprocess.run(["node", SIM_SCRIPT, SIM_TARGET], cwd=ROOT,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)
        self.assertIn("Impact at 500 m", result.stdout)
        # The come-up rows above the stage pick which range gets flown.
        self.assertIn("table rows : 5", result.stdout)
        self.assertIn("clicked 300m -> requested 300 m", result.stdout)


@unittest.skipIf(shutil.which("node") is None, "node isn't installed")
class FramingTests(unittest.TestCase):
    """The view has to be fitted to the flight it is showing.

    Hard-coded camera constants framed a 500 m .308 and nothing else: the
    distance scale sat thousands of pixels below the canvas at any shorter
    range, and 50 m drew a flat line. The shape of a shot varies far too
    much for one setting -- this .223 drops 3.7 cm over 50 m and 279 m over
    2000 -- so this drives real solver output at a spread of ranges and at
    two canvas sizes, and insists everything drawn lands on screen.
    """

    #: Ranges either side of the ones the old constants happened to suit.
    RANGES = (50, 100, 300, 500, 1000, 2000)

    def _fixture(self):
        shot = ballistics.Shot(
            muzzle_velocity_fps=3240, ballistic_coefficient=0.202,
            drag_model="g1", bullet_grains=55,
            sight_height_mm=45, zero_distance_m=100)
        return [ballistics.trajectory(shot, d, samples=240) for d in self.RANGES]

    def _run(self, source):
        handle, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(self._fixture(), fh)
            return subprocess.run(["node", FRAME_SCRIPT, source, path], cwd=ROOT,
                                  capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(path)

    def test_every_range_frames_the_flight_and_its_distance_scale(self):
        result = self._run(SIM_TARGET)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)
        # The reported symptom was the scale showing up only past 2000 m,
        # so name the short ranges explicitly rather than trusting the pass.
        for distance in (50, 100, 300):
            self.assertIn(f"{distance} m @ 900x400", result.stdout)

    def _broken(self, edits, label):
        with open(SIM_TARGET, encoding="utf-8") as handle:
            source = handle.read()
        broken = source
        for old, new in edits:
            replaced = broken.replace(old, new)
            self.assertNotEqual(replaced, broken,
                                f"the {label} being tested has moved: {old!r}")
            broken = replaced

        path = os.path.join(ROOT, "tests", "js", "_broken_sim.js")
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(broken)
            result = self._run(path)
            self.assertNotEqual(result.returncode, 0,
                                f"the check no longer notices a {label}")
            self.assertIn("FAIL", result.stdout)
            return result.stdout
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_a_fixed_camera_would_be_caught(self):
        # The original camera, restored: parked at 0.85 of the range with a
        # focal length set from the canvas width. Those two only frame the
        # shot together, so reverting one alone proves nothing -- the fitted
        # stand-off quietly rescues the old focal length. Both, and the
        # muzzle and target sit outside the frame at every distance.
        out = self._broken([
            ("const dolly = Math.max(range * 1.15, lateral + 8);",
             "const dolly = range * 0.85;"),
            ("const focal = (across * near) / (range / 2);",
             "const focal = width * 0.95;"),
        ], "fixed camera")
        self.assertRegex(out, r"off the (side|left|right)")

    def test_a_fixed_height_scale_would_be_caught(self):
        # 40x suited 500 m and nothing else: flat at 50 m, off the canvas
        # entirely at 2000.
        self._broken([("const exaggeration = fitted * sim.stretch;",
                       "const exaggeration = 40;")], "fixed height scale")


if __name__ == "__main__":
    unittest.main()
