"""The site menu, and the system box at the foot of it.

The box is driven for real under a stub DOM, since what matters is the
polling lifecycle and what each reading renders as.
"""
import os
import re
import shutil
import subprocess
import unittest

from spots import health

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tests", "js", "menu_stats.js")
TARGET = os.path.join(ROOT, "spots", "web", "static", "menu.js")
MENU = os.path.join(ROOT, "spots", "web", "templates", "_menu.html")


class MenuStatsMarkupTests(unittest.TestCase):
    def _menu(self):
        with open(MENU, encoding="utf-8") as fh:
            return fh.read()

    def test_the_menu_carries_the_system_box(self):
        menu = self._menu()
        self.assertIn('id="menu-stats"', menu)
        self.assertIn('id="menu-stats-rows"', menu)

    def test_the_box_is_not_one_of_the_menu_items(self):
        # menu.js walks `.menu-item` for arrow-key navigation and focuses
        # the first one on open. A readings panel in that rotation would be
        # a keyboard trap on the way to the links.
        box = re.search(r'<div class="menu-stats".*?</div>\s*</div>',
                        self._menu(), re.S)
        self.assertIsNotNone(box, "the system box has gone")
        self.assertNotIn("menu-item", box.group(0))
        self.assertNotIn("<a ", box.group(0))


class HealthLevelTests(unittest.TestCase):
    """Each row's colour comes from the server.

    The thresholds live only in health.py, so the browser never decides
    whether 72 C is warm.
    """

    def _collect(self, temp=45.0, free_mb=50000.0, throttle="0x0"):
        reads = {
            "/sys/class/thermal/thermal_zone0/temp": str(int(temp * 1000)),
            "/sys/devices/platform/soc/soc:firmware/get_throttled": throttle,
        }
        original = health._read_first_line
        health._read_first_line = lambda path: reads.get(path)
        original_disk = health.disk_free_mb
        health.disk_free_mb = lambda path: {
            "free_mb": free_mb, "total_mb": 60000.0,
            "used_percent": 100.0 * (1 - free_mb / 60000.0)}
        try:
            return health.collect(".", "synthetic", False)
        finally:
            health._read_first_line = original
            health.disk_free_mb = original_disk

    def test_a_healthy_pi_is_ok_everywhere(self):
        levels = self._collect()["levels"]
        self.assertEqual(levels, {"cpu": "ok", "disk": "ok",
                                  "power": "ok", "camera": "ok"})

    def test_each_subsystem_is_reported_separately(self):
        # A hot CPU must not colour the disk row, and vice versa.
        hot = self._collect(temp=83.0)["levels"]
        self.assertEqual(hot["cpu"], "critical")
        self.assertEqual(hot["disk"], "ok")
        self.assertEqual(hot["power"], "ok")

        full = self._collect(free_mb=100.0)["levels"]
        self.assertEqual(full["disk"], "critical")
        self.assertEqual(full["cpu"], "ok")

    def test_under_voltage_lands_on_the_power_row(self):
        now = self._collect(throttle="0x1")["levels"]
        self.assertEqual(now["power"], "critical")
        # A dip at boot is a different problem from one happening now.
        since = self._collect(throttle="0x10000")["levels"]
        self.assertEqual(since["power"], "warn")

    def test_the_rolled_up_status_still_works(self):
        # The badge elsewhere reads `status`; adding `levels` must not have
        # changed what it says.
        self.assertEqual(self._collect()["status"], "ok")
        self.assertEqual(self._collect(temp=72.0)["status"], "warn")
        self.assertEqual(self._collect(temp=83.0)["status"], "critical")


@unittest.skipIf(shutil.which("node") is None, "node isn't installed")
class MenuStatsBehaviourTests(unittest.TestCase):
    def test_the_box_reports_the_pi_and_stops_when_closed(self):
        result = subprocess.run(["node", SCRIPT, TARGET], cwd=ROOT,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)
        self.assertIn("closed     : polling stopped", result.stdout)
        # The four readings that were asked for.
        for reading in ("CPU 48", "Uptime 1d 2h", "Disk 21.6 GB", "Power OK"):
            self.assertIn(reading, result.stdout)

    def test_a_menu_that_kept_polling_would_be_caught(self):
        with open(TARGET, encoding="utf-8") as fh:
            source = fh.read()
        broken = source.replace("    stopStats();\n    if (returnFocus) button.focus();",
                                "    if (returnFocus) button.focus();")
        self.assertNotEqual(broken, source, "the close handler has moved")

        path = os.path.join(ROOT, "tests", "js", "_broken_menu.js")
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(broken)
            result = subprocess.run(["node", SCRIPT, path], cwd=ROOT,
                                    capture_output=True, text=True, timeout=60)
            self.assertNotEqual(result.returncode, 0,
                                "the check no longer notices a menu that keeps polling")
            self.assertIn("FAIL", result.stdout)
        finally:
            if os.path.exists(path):
                os.unlink(path)


if __name__ == "__main__":
    unittest.main()
