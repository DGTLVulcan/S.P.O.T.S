"""Drives ballistics.js under a stub DOM, through node.

The page has three async loaders that can land after the thing they would
overwrite has already been put on screen -- filling the DOPE card from a
solution hit exactly that, showing the rows and then wiping them when the
saved-card fetch returned. That class of bug is invisible to a Python test
and to `node --check`, so it gets driven for real.

Skipped rather than failed where node isn't installed: the Pi doesn't need
it to run S.P.O.T.S.
"""
import os
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tests", "js", "dope_fill.js")
TARGET = os.path.join(ROOT, "spots", "web", "static", "ballistics.js")


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


if __name__ == "__main__":
    unittest.main()
