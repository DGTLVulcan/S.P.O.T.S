"""Pi health readings: parsing the real sysfs/proc formats, and the
thresholds that decide whether the dashboard shows a warning."""

import os
import sys

# Import the app regardless of where the runner sets its working directory
# (Visual Studio Test Explorer, `python -m unittest`, and pytest all differ).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from unittest.mock import patch

from spots import health


class ParsingTests(unittest.TestCase):
    def test_temperature_is_millidegrees(self):
        with patch.object(health, "_read_first_line", return_value="62237"):
            self.assertAlmostEqual(health.cpu_temperature_c(), 62.237)

    def test_throttle_bitmask(self):
        with patch.object(health, "_read_first_line", return_value="0x50005"):
            flags = health.throttled_flags()
        self.assertTrue(flags["under_voltage_now"])
        self.assertTrue(flags["throttled_now"])
        self.assertTrue(flags["under_voltage_since_boot"])
        self.assertTrue(flags["throttled_since_boot"])

    def test_throttle_all_clear(self):
        with patch.object(health, "_read_first_line", return_value="0x0"):
            self.assertFalse(any(health.throttled_flags().values()))

    def test_uptime_takes_the_first_field(self):
        with patch.object(health, "_read_first_line", return_value="123456.78 987654.32"):
            self.assertAlmostEqual(health.uptime_s(), 123456.78)

    def test_unreadable_and_malformed_yield_none(self):
        for value in (None, "garbage", ""):
            with patch.object(health, "_read_first_line", return_value=value):
                self.assertIsNone(health.cpu_temperature_c())
                self.assertIsNone(health.throttled_flags())
                self.assertIsNone(health.uptime_s())

    def test_disk_reading_works_on_any_platform(self):
        disk = health.disk_free_mb(".")
        self.assertIsNotNone(disk)
        self.assertGreater(disk["total_mb"], 0)


class StatusEscalationTests(unittest.TestCase):
    def collect(self, temp=45.0, free_mb=50000.0, throttle="0x0", feed="synthetic", camera=False):
        disk = {"free_mb": free_mb, "total_mb": 60000.0, "used_percent": 50.0}
        with patch.object(health, "cpu_temperature_c", return_value=temp), \
                patch.object(health, "disk_free_mb", return_value=disk), \
                patch.object(health, "_read_first_line", return_value=throttle), \
                patch.object(health, "memory_mb", return_value=None), \
                patch.object(health, "load_average", return_value=None), \
                patch.object(health, "uptime_s", return_value=None):
            return health.collect(".", feed, camera)

    def test_healthy(self):
        result = self.collect()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["warnings"], [])

    def test_warm_cpu_warns(self):
        self.assertEqual(self.collect(temp=72.0)["status"], "warn")

    def test_hot_cpu_is_critical(self):
        self.assertEqual(self.collect(temp=83.0)["status"], "critical")

    def test_low_disk_warns_then_goes_critical(self):
        self.assertEqual(self.collect(free_mb=800.0)["status"], "warn")
        self.assertEqual(self.collect(free_mb=100.0)["status"], "critical")

    def test_under_voltage_now_is_critical(self):
        result = self.collect(throttle="0x1")
        self.assertEqual(result["status"], "critical")
        self.assertIn("power supply", result["warnings"][0])

    def test_throttling_since_boot_only_warns(self):
        self.assertEqual(self.collect(throttle="0x40000")["status"], "warn")

    def test_live_feed_without_a_camera_warns(self):
        result = self.collect(feed="zcam", camera=False)
        self.assertEqual(result["status"], "warn")

    def test_live_feed_with_a_camera_is_fine(self):
        self.assertEqual(self.collect(feed="zcam", camera=True)["status"], "ok")

    def test_worst_condition_wins(self):
        self.assertEqual(self.collect(temp=72.0, free_mb=100.0)["status"], "critical")


class CollectShapeTests(unittest.TestCase):
    def test_collect_never_raises_off_pi(self):
        """Off a Pi the sysfs nodes simply aren't there; that must degrade to
        None rather than taking the endpoint down."""
        result = health.collect(".", "synthetic", False)
        for key in ("status", "warnings", "cpu_temp_c", "disk", "uptime_s", "feed_active"):
            self.assertIn(key, result)
        self.assertIn(result["status"], ("ok", "warn", "critical"))


if __name__ == "__main__":
    unittest.main()
