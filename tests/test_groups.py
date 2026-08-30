"""Group statistics, best-subgroup search and scope correction."""

import os
import sys

# Import the app regardless of where the runner sets its working directory
# (Visual Studio Test Explorer, `python -m unittest`, and pytest all differ).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import math
import random
import statistics
import unittest
from itertools import combinations

from spots.vision.groups import (
    GroupStats,
    best_subgroup,
    compute_group_stats,
    scope_correction,
    to_moa,
    to_mrad,
)


def _brute_force_best_subgroup(points, n, unit_name):
    """The original exhaustive implementation, kept as the reference the
    fast branch-and-bound version is checked against.
    """
    if len(points) < n:
        return None
    best = None
    for combo in combinations(points, n):
        pts = list(combo)
        cx = statistics.fmean(p[0] for p in pts)
        cy = statistics.fmean(p[1] for p in pts)
        radii = [math.hypot(x - cx, y - cy) for x, y in pts]
        spread = (
            max(math.hypot(a - c, b - d) for (a, b), (c, d) in combinations(pts, 2))
            if len(pts) > 1
            else 0.0
        )
        stats = GroupStats(
            len(pts), (cx, cy), spread, statistics.fmean(radii),
            statistics.pstdev(radii) if len(pts) > 1 else 0.0, unit_name,
        )
        if best is None or stats.extreme_spread < best.extreme_spread:
            best = stats
    return best


class ComputeGroupStatsTests(unittest.TestCase):
    def test_extreme_spread_is_the_widest_pair(self):
        stats = compute_group_stats([(0.0, 0.0), (3.0, 4.0), (6.0, 8.0)], "cm")
        self.assertAlmostEqual(stats.extreme_spread, 10.0)
        self.assertEqual(stats.shot_count, 3)

    def test_single_shot_has_no_spread(self):
        stats = compute_group_stats([(1.0, 1.0)], "cm")
        self.assertEqual(stats.extreme_spread, 0.0)
        self.assertEqual(stats.std_dev, 0.0)

    def test_empty_is_none(self):
        self.assertIsNone(compute_group_stats([], "cm"))

    def test_center_is_the_mean(self):
        stats = compute_group_stats([(0.0, 0.0), (2.0, 4.0)], "cm")
        self.assertAlmostEqual(stats.center[0], 1.0)
        self.assertAlmostEqual(stats.center[1], 2.0)


class BestSubgroupTests(unittest.TestCase):
    def test_matches_brute_force_across_many_shapes(self):
        rng = random.Random(20260830)

        def make(kind, count):
            if kind == "normal":
                return [(rng.gauss(0, 5), rng.gauss(0, 5)) for _ in range(count)]
            if kind == "tight":
                return [(rng.gauss(0, 0.3), rng.gauss(0, 0.3)) for _ in range(count)]
            if kind == "collinear":
                return [(float(i), 0.0) for i in range(count)]
            if kind == "grid":
                return [(float(i % 4), float(i // 4)) for i in range(count)]
            duplicates = [(rng.gauss(0, 3), rng.gauss(0, 3)) for _ in range(max(1, count // 2))]
            return [duplicates[i % len(duplicates)] for i in range(count)]

        checked = 0
        for kind in ("normal", "tight", "collinear", "grid", "dupes"):
            for count in range(2, 10):
                for n in range(2, 6):
                    for _ in range(3):
                        points = make(kind, count)
                        fast = best_subgroup(points, n, "cm")
                        slow = _brute_force_best_subgroup(points, n, "cm")
                        self.assertEqual(fast is None, slow is None, (kind, count, n))
                        if fast is None:
                            continue
                        checked += 1
                        # Ties are common (one widest pair fixes the diameter)
                        # so the chosen subset may differ; the optimum must not.
                        self.assertAlmostEqual(
                            fast.extreme_spread, slow.extreme_spread, places=12,
                            msg=f"{kind} count={count} n={n} points={points}",
                        )
        self.assertGreater(checked, 300)

    def test_picks_the_tight_cluster_over_the_flyer(self):
        points = [(0.0, 0.0), (0.5, 0.0), (0.0, 0.5), (50.0, 50.0)]
        best = best_subgroup(points, 3, "cm")
        self.assertLess(best.extreme_spread, 1.0)

    def test_not_enough_shots_is_none(self):
        self.assertIsNone(best_subgroup([(0.0, 0.0)], 3, "cm"))

    def test_single_shot_subgroup(self):
        best = best_subgroup([(2.0, 3.0), (9.0, 9.0)], 1, "cm")
        self.assertEqual(best.extreme_spread, 0.0)


class AngularConversionTests(unittest.TestCase):
    def test_one_moa_is_about_2_9cm_at_100m(self):
        self.assertAlmostEqual(to_moa(2.908, "cm", 100.0), 1.0, places=3)

    def test_one_mrad_is_10cm_at_100m(self):
        self.assertAlmostEqual(to_mrad(10.0, "cm", 100.0), 1.0, places=4)

    def test_no_distance_or_unknown_unit_is_none(self):
        self.assertIsNone(to_moa(10.0, "cm", 0))
        self.assertIsNone(to_moa(10.0, "furlongs", 100.0))
        self.assertIsNone(to_mrad(10.0, "cm", None))


class ScopeCorrectionTests(unittest.TestCase):
    """A group printing high and right needs the turret dialled down and
    left -- the correction is the opposite of the error."""

    def test_high_and_right_dials_down_and_left(self):
        # 5.816 cm high = 2 MOA, 2.908 cm right = 1 MOA, at 100 m.
        correction = scope_correction((2.908, 5.816), "cm", 100.0, 0.25, "moa")
        self.assertEqual(correction["vertical_dir"], "down")
        self.assertEqual(correction["vertical_clicks"], 8)
        self.assertEqual(correction["horizontal_dir"], "left")
        self.assertEqual(correction["horizontal_clicks"], 4)

    def test_low_and_left_dials_up_and_right(self):
        correction = scope_correction((-2.908, -5.816), "cm", 100.0, 0.25, "moa")
        self.assertEqual(correction["vertical_dir"], "up")
        self.assertEqual(correction["horizontal_dir"], "right")

    def test_mrad_turret(self):
        correction = scope_correction((10.0, 0.0), "cm", 100.0, 0.1, "mrad")
        self.assertEqual(correction["horizontal_clicks"], 10)
        self.assertEqual(correction["horizontal_dir"], "left")

    def test_unavailable_without_distance_unit_or_click_value(self):
        self.assertIsNone(scope_correction((1.0, 1.0), "cm", 0, 0.25, "moa"))
        self.assertIsNone(scope_correction((1.0, 1.0), "clicks", 100.0, 0.25, "moa"))
        self.assertIsNone(scope_correction((1.0, 1.0), "cm", 100.0, 0.0, "moa"))


if __name__ == "__main__":
    unittest.main()
