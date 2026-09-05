import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spots import ballistics as b


class DragModelTests(unittest.TestCase):
    def test_the_two_models_are_genuinely_different(self):
        # A G1 BC used as a G7 one is a large error, which is the whole
        # reason the model is stored beside the number.
        self.assertGreater(b.drag_coefficient(2.0, "g1"),
                           b.drag_coefficient(2.0, "g7") * 1.5)

    def test_interpolates_between_table_points(self):
        low = b.drag_coefficient(1.50, "g7")
        high = b.drag_coefficient(1.55, "g7")
        mid = b.drag_coefficient(1.525, "g7")
        self.assertTrue(high < mid < low, (low, mid, high))

    def test_beyond_the_table_the_ends_are_held(self):
        self.assertEqual(b.drag_coefficient(99.0, "g7"), b.G7_TABLE[-1][1])
        self.assertEqual(b.drag_coefficient(-1.0, "g1"), b.G1_TABLE[0][1])

    def test_both_models_peak_through_the_sound_barrier(self):
        for model in ("g1", "g7"):
            supersonic = b.drag_coefficient(1.05, model)
            subsonic = b.drag_coefficient(0.7, model)
            self.assertGreater(supersonic, subsonic * 2, model)


class AtmosphereTests(unittest.TestCase):
    def test_standard_atmosphere_matches_published_values(self):
        standard = b.Atmosphere()
        self.assertAlmostEqual(standard.density, 1.2250, places=3)
        self.assertAlmostEqual(standard.speed_of_sound, 340.3, delta=0.5)

    def test_humid_air_is_lighter_than_dry(self):
        # Counter-intuitive, and it matters: water vapour displaces heavier
        # nitrogen and oxygen.
        dry = b.Atmosphere(temperature_c=30, humidity_pct=0)
        humid = b.Atmosphere(temperature_c=30, humidity_pct=95)
        self.assertLess(humid.density, dry.density)

    def test_hot_thin_air_is_less_dense_than_cold(self):
        self.assertLess(b.Atmosphere(temperature_c=40).density,
                        b.Atmosphere(temperature_c=-5).density)

    def test_altitude_thins_the_air(self):
        sea_level = b.Atmosphere(pressure_hpa=1013.25)
        mountain = b.Atmosphere(pressure_hpa=800.0)
        self.assertLess(mountain.density, sea_level.density)


class IntegratorTests(unittest.TestCase):
    """The one part of this that can be checked exactly."""

    def test_matches_the_closed_form_parabola_in_a_vacuum(self):
        # With drag switched off the trajectory has an exact solution, so
        # any disagreement is the integrator's own arithmetic error.
        shot = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=1e9,
                      bullet_grains=175, sight_height_mm=0.0, zero_distance_m=100)
        angle = b._zero_angle(shot)
        samples = b._integrate(shot, angle, 600)
        speed = 2600 * b.FPS_TO_MS
        for distance in (50, 100, 200, 300, 400, 500):
            got = b._sample_at(samples, distance)[1]
            want = (distance * math.tan(angle)
                    - b.GRAVITY * distance ** 2 / (2 * speed ** 2 * math.cos(angle) ** 2))
            self.assertAlmostEqual(got, want, places=4, msg=f"{distance} m")

    def test_the_bullet_starts_one_sight_height_below_the_line_of_sight(self):
        shot = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=0.243,
                      bullet_grains=175, sight_height_mm=50, zero_distance_m=100)
        samples = b._integrate(shot, 0.0, 100)
        self.assertAlmostEqual(samples[0][1], -0.05, places=6)


class ZeroTests(unittest.TestCase):
    def setUp(self):
        self.shot = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=0.243,
                           bullet_grains=175, bullet_diameter_mm=7.82,
                           sight_height_mm=40, zero_distance_m=100)

    def test_the_bullet_is_on_the_sight_line_at_the_zero(self):
        point = b.solve(self.shot, [100])[0]
        self.assertAlmostEqual(point.drop_m, 0.0, places=3)

    def test_a_longer_zero_needs_less_come_up_further_out(self):
        near = b.solve(self.shot, [400])[0]
        far_shot = b.Shot(**{**self.shot.__dict__, "zero_distance_m": 300.0})
        far = b.solve(far_shot, [400])[0]
        self.assertGreater(far.drop_m, near.drop_m)

    def test_drop_grows_with_distance(self):
        points = b.solve(self.shot, [100, 200, 300, 400, 500])
        drops = [p.drop_m for p in points]
        self.assertEqual(drops, sorted(drops, reverse=True))

    def test_the_bullet_slows_down(self):
        points = b.solve(self.shot, [100, 200, 300, 400, 500])
        speeds = [p.velocity_ms for p in points]
        self.assertEqual(speeds, sorted(speeds, reverse=True))


class SanityTests(unittest.TestCase):
    """A .308 175gr match load is well enough documented to sanity-check
    against, though the numbers below are deliberately loose bands: this
    guards against a broken solver, not against being a click out."""

    def setUp(self):
        self.shot = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=0.243,
                           drag_model="g7", bullet_grains=175,
                           bullet_diameter_mm=7.82, sight_height_mm=40,
                           zero_distance_m=100)

    def test_retained_velocity_is_in_the_published_range(self):
        point = b.solve(self.shot, [500])[0]
        self.assertTrue(1550 < point.velocity_ms / b.FPS_TO_MS < 1800,
                        point.velocity_ms / b.FPS_TO_MS)

    def test_time_of_flight_is_in_the_published_range(self):
        point = b.solve(self.shot, [500])[0]
        self.assertTrue(0.70 < point.time_s < 0.86, point.time_s)

    def test_come_up_at_500m_is_in_the_published_range(self):
        rows = b.card(self.shot, [500], unit="moa")["rows"]
        self.assertTrue(12.0 < rows[0]["elevation"] < 16.0, rows[0]["elevation"])

    def test_muzzle_energy_matches_the_hand_calculation(self):
        point = b.solve(self.shot, [1])[0]
        mass = 175 * b.GRAINS_TO_KG
        speed = 2600 * b.FPS_TO_MS
        self.assertAlmostEqual(point.energy_j, 0.5 * mass * speed ** 2, delta=40)


class WindTests(unittest.TestCase):
    def setUp(self):
        self.shot = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=0.243,
                           bullet_grains=175, bullet_diameter_mm=7.82,
                           sight_height_mm=40, zero_distance_m=100)

    def _with(self, **kw):
        return b.Shot(**{**self.shot.__dict__, **kw})

    def test_no_wind_means_no_drift(self):
        self.assertAlmostEqual(b.solve(self.shot, [500])[0].windage_m, 0.0, places=4)

    def test_a_wind_from_the_right_pushes_the_bullet_left(self):
        point = b.solve(self._with(wind_speed_kph=16, wind_clock=3), [500])[0]
        self.assertLess(point.windage_m, -0.05)

    def test_a_wind_from_the_left_pushes_it_right(self):
        point = b.solve(self._with(wind_speed_kph=16, wind_clock=9), [500])[0]
        self.assertGreater(point.windage_m, 0.05)

    def test_a_head_wind_barely_deflects_but_does_slow_the_bullet(self):
        head = b.solve(self._with(wind_speed_kph=30, wind_clock=12), [500])[0]
        calm = b.solve(self.shot, [500])[0]
        self.assertAlmostEqual(head.windage_m, 0.0, places=2)
        self.assertLess(head.velocity_ms, calm.velocity_ms)

    def test_drift_grows_faster_than_distance(self):
        # Wind drift goes with lag time, not range, so doubling the range
        # more than doubles the drift.
        windy = self._with(wind_speed_kph=16, wind_clock=3)
        near = abs(b.solve(windy, [250])[0].windage_m)
        far = abs(b.solve(windy, [500])[0].windage_m)
        self.assertGreater(far, near * 2.5)


class AngleTests(unittest.TestCase):
    def test_one_mrad_is_ten_centimetres_at_100m(self):
        self.assertAlmostEqual(b.to_angle(0.10, 100, "mrad"), 1.0, places=3)

    def test_one_moa_is_about_29mm_at_100m(self):
        self.assertAlmostEqual(b.to_angle(0.0291, 100, "moa"), 1.0, places=2)

    def test_clicks_divide_the_angle_by_the_click_value(self):
        self.assertAlmostEqual(b.to_clicks(1.0, 0.1), 10.0)
        self.assertAlmostEqual(b.to_clicks(1.5, 0.25), 6.0)
        self.assertIsNone(b.to_clicks(1.0, 0))

    def test_a_low_bullet_needs_the_turret_dialled_up(self):
        point = b.TrajectoryPoint(distance_m=300, drop_m=-0.5, windage_m=0.0,
                                  velocity_ms=600, time_s=0.4, energy_j=2000, mach=1.8)
        row = b.dope_row(point, "mrad", 0.1)
        self.assertGreater(row["elevation"], 0)
        self.assertEqual(row["elevation_clicks"], 17)


class TransonicTests(unittest.TestCase):
    def test_a_slow_heavy_bullet_is_flagged_before_500m(self):
        shot = b.Shot(muzzle_velocity_fps=1500, ballistic_coefficient=0.2,
                      bullet_grains=230, bullet_diameter_mm=11.5,
                      sight_height_mm=40, zero_distance_m=100)
        result = b.card(shot, list(range(100, 501, 100)))
        self.assertIsNotNone(result["transonic_from_m"])

    def test_a_fast_match_load_stays_supersonic_to_500m(self):
        shot = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=0.243,
                      bullet_grains=175, bullet_diameter_mm=7.82,
                      sight_height_mm=40, zero_distance_m=100)
        self.assertIsNone(b.card(shot, list(range(100, 501, 100)))["transonic_from_m"])


class StabilityTests(unittest.TestCase):
    def test_stability_needs_the_bullet_length(self):
        shot = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=0.243,
                      bullet_grains=175, bullet_diameter_mm=7.82, twist_rate_in=11)
        self.assertIsNone(b.gyroscopic_stability(shot))

    def test_a_normal_match_load_is_stable(self):
        shot = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=0.243,
                      bullet_grains=175, bullet_diameter_mm=7.82,
                      bullet_length_mm=32.0, twist_rate_in=11)
        stability = b.gyroscopic_stability(shot)
        self.assertTrue(1.2 < stability < 3.0, stability)

    def test_a_slower_twist_is_less_stable(self):
        fast = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=0.243,
                      bullet_grains=175, bullet_diameter_mm=7.82,
                      bullet_length_mm=32.0, twist_rate_in=10)
        slow = b.Shot(**{**fast.__dict__, "twist_rate_in": 14.0})
        self.assertGreater(b.gyroscopic_stability(fast), b.gyroscopic_stability(slow))

    def test_spin_drift_is_left_out_when_it_cannot_be_worked_out(self):
        shot = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=0.243,
                      bullet_grains=175, bullet_diameter_mm=7.82,
                      sight_height_mm=40, zero_distance_m=100)
        self.assertFalse(b.card(shot, [500])["spin_drift_included"])

    def test_spin_drift_pushes_right_for_a_right_hand_twist(self):
        shot = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=0.243,
                      bullet_grains=175, bullet_diameter_mm=7.82,
                      bullet_length_mm=32.0, twist_rate_in=11,
                      sight_height_mm=40, zero_distance_m=100)
        result = b.card(shot, [500])
        self.assertTrue(result["spin_drift_included"])
        self.assertGreater(b.solve(shot, [500])[0].windage_m, 0)


class ValidationTests(unittest.TestCase):
    def test_rubbish_inputs_are_refused_rather_than_guessed_at(self):
        for kwargs in (
            {"muzzle_velocity_fps": 0, "ballistic_coefficient": 0.243},
            {"muzzle_velocity_fps": 2600, "ballistic_coefficient": 0},
            {"muzzle_velocity_fps": -100, "ballistic_coefficient": 0.243},
        ):
            with self.assertRaises(b.BallisticsError):
                b.solve(b.Shot(**kwargs), [100])

    def test_an_unknown_drag_model_is_refused(self):
        shot = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=0.243,
                      drag_model="g5")
        with self.assertRaises(b.BallisticsError):
            b.solve(shot, [100])

    def test_distances_are_sorted_and_deduplicated(self):
        shot = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=0.243,
                      bullet_grains=175, sight_height_mm=40, zero_distance_m=100)
        points = b.solve(shot, [300, 100, 300, 200, -5, 0])
        self.assertEqual([p.distance_m for p in points], [100.0, 200.0, 300.0])


class LookAngleTests(unittest.TestCase):
    def test_shooting_uphill_needs_less_come_up(self):
        level = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=0.243,
                       bullet_grains=175, sight_height_mm=40, zero_distance_m=100)
        uphill = b.Shot(**{**level.__dict__, "look_angle_deg": 30.0})
        downhill = b.Shot(**{**level.__dict__, "look_angle_deg": -30.0})
        flat_drop = b.solve(level, [400])[0].drop_m
        self.assertGreater(b.solve(uphill, [400])[0].drop_m, flat_drop)
        self.assertGreater(b.solve(downhill, [400])[0].drop_m, flat_drop)


class TruingTests(unittest.TestCase):
    """Truing is the point of doing this inside SPOTS: the impacts are
    already recorded, so the solution can be bent to fit them."""

    def _shot(self, velocity):
        return b.Shot(muzzle_velocity_fps=velocity, ballistic_coefficient=0.243,
                      bullet_grains=175, bullet_diameter_mm=7.82,
                      sight_height_mm=40, zero_distance_m=100)

    def test_it_recovers_a_velocity_it_was_not_told(self):
        # Generate come-ups from a known velocity, then see whether truing
        # finds it starting from a wrong one.
        truth = self._shot(2680)
        observed = [(d, b.dope_row(p, "mrad")["elevation"])
                    for d, p in zip([300, 400, 500], b.solve(truth, [300, 400, 500]))]
        result = b.true_muzzle_velocity(self._shot(2500), observed, unit="mrad")
        self.assertAlmostEqual(result["muzzle_velocity_fps"], 2680, delta=15)
        self.assertLess(result["rms_error"], 0.02)

    def test_it_reports_what_it_changed(self):
        truth = self._shot(2700)
        observed = [(500, b.dope_row(b.solve(truth, [500])[0], "mrad")["elevation"])]
        result = b.true_muzzle_velocity(self._shot(2600), observed)
        self.assertEqual(result["was_fps"], 2600.0)
        self.assertGreater(result["change_fps"], 0)
        self.assertEqual(result["observations"], 1)

    def test_truing_needs_something_to_true_against(self):
        with self.assertRaises(b.BallisticsError):
            b.true_muzzle_velocity(self._shot(2600), [])

    def test_it_works_in_moa_too(self):
        truth = self._shot(2650)
        observed = [(d, b.dope_row(p, "moa")["elevation"])
                    for d, p in zip([300, 500], b.solve(truth, [300, 500]))]
        result = b.true_muzzle_velocity(self._shot(2450), observed, unit="moa")
        self.assertAlmostEqual(result["muzzle_velocity_fps"], 2650, delta=20)


class CardTests(unittest.TestCase):
    def setUp(self):
        self.shot = b.Shot(muzzle_velocity_fps=2600, ballistic_coefficient=0.243,
                           bullet_grains=175, bullet_diameter_mm=7.82,
                           sight_height_mm=40, zero_distance_m=100)

    def test_a_card_covers_every_distance_asked_for(self):
        distances = list(range(50, 501, 50))
        result = b.card(self.shot, distances, unit="mrad", click_value=0.1)
        self.assertEqual([r["distance_m"] for r in result["rows"]],
                         [float(d) for d in distances])

    def test_clicks_are_omitted_when_the_scope_has_no_click_value(self):
        rows = b.card(self.shot, [300], unit="mrad")["rows"]
        self.assertIsNone(rows[0]["elevation_clicks"])

    def test_moa_needs_more_clicks_than_mrad_for_the_same_drop(self):
        moa = b.card(self.shot, [500], unit="moa", click_value=0.25)["rows"][0]
        mrad = b.card(self.shot, [500], unit="mrad", click_value=0.1)["rows"][0]
        self.assertGreater(moa["elevation_clicks"], mrad["elevation_clicks"])

    def test_the_card_reports_the_air_it_was_solved_for(self):
        result = b.card(self.shot, [300])
        self.assertAlmostEqual(result["density_ratio"], 1.0, places=2)
        self.assertGreater(result["speed_of_sound_ms"], 300)


if __name__ == "__main__":
    unittest.main()
