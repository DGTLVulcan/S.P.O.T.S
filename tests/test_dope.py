import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spots import dope


def equipment(rifle=None, scope=None, ammo=None):
    return {
        "rifle": {"id": 1, "name": "Rifle", "specs": rifle or {}},
        "scope": {"id": 2, "name": "Scope", "click_value": 0.1, "click_unit": "mrad",
                  "specs": scope or {}},
        "ammo": {"id": 3, "name": "Ammo", "specs": ammo or {}},
    }


FULL = equipment(
    rifle={"sight_height_mm": "45", "twist_rate": "1:10"},
    scope={"zero_distance_m": "100"},
    ammo={"muzzle_velocity_fps": "2650", "ballistic_coefficient": "0.243",
          "drag_model": "g7", "bullet_grains": "175", "bullet_diameter_mm": "7.82"},
)


class TwistParsingTests(unittest.TestCase):
    def test_the_forms_people_actually_write(self):
        for text, expected in [("1:10", 10.0), ("1 in 12", 12.0), ("1:7.5", 7.5),
                               ("11", 11.0), ("1:8 twist", 8.0)]:
            self.assertEqual(dope.parse_twist(text), expected, text)

    def test_nonsense_is_none_rather_than_zero(self):
        for bad in ("", None, "fast", "1:", "-10"):
            self.assertIsNone(dope.parse_twist(bad), bad)


class EquipmentBridgeTests(unittest.TestCase):
    def test_a_complete_setup_needs_nothing_more(self):
        shot, missing, used = dope.shot_from_equipment(FULL)
        self.assertEqual(missing, [])
        self.assertEqual(shot.muzzle_velocity_fps, 2650.0)
        self.assertEqual(shot.ballistic_coefficient, 0.243)
        self.assertEqual(shot.sight_height_mm, 45.0)
        self.assertEqual(shot.zero_distance_m, 100.0)
        self.assertEqual(shot.twist_rate_in, 10.0)
        self.assertEqual(used["unit"], "mrad")

    def test_missing_inputs_are_named_not_defaulted(self):
        # A ballistic answer built on a substituted default still looks
        # authoritative, which is worse than refusing to give one.
        _shot, missing, _used = dope.shot_from_equipment(equipment())
        self.assertEqual(len(missing), 4)
        self.assertTrue(any("Muzzle velocity" in m for m in missing))
        self.assertTrue(any("Ballistic coefficient" in m for m in missing))
        self.assertTrue(any("Sight height" in m for m in missing))
        self.assertTrue(any("Zero distance" in m for m in missing))

    def test_overrides_beat_the_equipment(self):
        shot, missing, _ = dope.shot_from_equipment(
            FULL, overrides={"muzzle_velocity_fps": "2700"})
        self.assertEqual(shot.muzzle_velocity_fps, 2700.0)
        self.assertEqual(missing, [])

    def test_an_override_can_supply_what_the_equipment_lacks(self):
        _shot, missing, _ = dope.shot_from_equipment(
            equipment(), overrides={"muzzle_velocity_fps": "2600",
                                    "ballistic_coefficient": "0.243",
                                    "sight_height_mm": "40",
                                    "zero_distance_m": "100"})
        self.assertEqual(missing, [])

    def test_conditions_supply_the_air(self):
        shot, _, _ = dope.shot_from_equipment(FULL, conditions={
            "temperature_c": "32", "pressure_hpa": "995", "humidity_pct": "70"})
        self.assertEqual(shot.atmosphere.temperature_c, 32.0)
        self.assertEqual(shot.atmosphere.pressure_hpa, 995.0)
        self.assertEqual(shot.atmosphere.humidity_pct, 70.0)

    def test_missing_conditions_fall_back_to_the_standard_atmosphere(self):
        shot, _, _ = dope.shot_from_equipment(FULL, conditions={})
        self.assertAlmostEqual(shot.atmosphere.density, 1.225, places=3)

    def test_wind_direction_becomes_a_clock_position(self):
        for direction, clock in [("head", 12.0), ("tail", 6.0), ("right", 3.0),
                                 ("left", 9.0), ("half_right", 1.5), ("half_left", 10.5)]:
            shot, _, _ = dope.shot_from_equipment(
                FULL, conditions={"wind_direction": direction, "wind_speed": "10"})
            self.assertEqual(shot.wind_clock, clock, direction)

    def test_an_unknown_drag_model_falls_back_to_g7(self):
        setup = equipment(rifle={"sight_height_mm": "45"}, scope={"zero_distance_m": "100"},
                          ammo={"muzzle_velocity_fps": "2650", "ballistic_coefficient": "0.5",
                                "drag_model": "g5"})
        shot, _, _ = dope.shot_from_equipment(setup)
        self.assertEqual(shot.drag_model, "g7")

    def test_blank_spec_strings_do_not_read_as_zero(self):
        setup = equipment(rifle={"sight_height_mm": ""}, scope={"zero_distance_m": "100"},
                          ammo={"muzzle_velocity_fps": "2650", "ballistic_coefficient": "0.243"})
        _shot, missing, _ = dope.shot_from_equipment(setup)
        self.assertTrue(any("Sight height" in m for m in missing))


class UnitTests(unittest.TestCase):
    def test_the_scope_decides_the_unit(self):
        moa = equipment()
        moa["scope"]["click_unit"] = "moa"
        self.assertEqual(dope.unit_for(moa), "moa")
        self.assertEqual(dope.unit_for(equipment()), "mrad")

    def test_an_explicit_choice_wins(self):
        self.assertEqual(dope.unit_for(equipment(), "moa"), "moa")

    def test_rubbish_falls_back_to_the_scope(self):
        self.assertEqual(dope.unit_for(equipment(), "furlongs"), "mrad")


class DistanceTests(unittest.TestCase):
    def test_the_default_card_runs_to_500m(self):
        self.assertEqual(dope.default_distances()[-1], 500)
        self.assertEqual(dope.default_distances()[0], 50)

    def test_the_step_is_respected(self):
        self.assertEqual(dope.default_distances(300, 100), [100, 200, 300])


class ObservationTests(unittest.TestCase):
    """Truing rows come out of sessions already recorded."""

    def _session(self, **kw):
        base = {"id": 1, "name": "Session #1", "distance_m": 300, "unit_name": "cm",
                "group_center": (0.0, -5.0), "shot_count": 5,
                "conditions": {"dialled_elevation": "1.70"}}
        base.update(kw)
        return base

    def test_a_complete_session_gives_what_was_actually_needed(self):
        rows = dope.observations_from_sessions([self._session()], "mrad")
        self.assertTrue(rows[0]["usable"])
        # 5 cm low at 300 m is 0.167 mrad, so 1.70 dialled was 0.17 short.
        self.assertAlmostEqual(rows[0]["group_offset"], 0.17, places=2)
        self.assertAlmostEqual(rows[0]["measured"], 1.87, places=2)

    def test_a_group_landing_high_means_less_was_needed(self):
        rows = dope.observations_from_sessions(
            [self._session(group_center=(0.0, 5.0))], "mrad")
        self.assertLess(rows[0]["measured"], 1.70)

    def test_each_unusable_session_says_why(self):
        cases = [
            (self._session(distance_m=0), "no distance"),
            (self._session(group_center=None), "no group centre"),
            (self._session(conditions={}), "dialled"),
            (self._session(unit_name="widgets"), "convert"),
        ]
        for session, expected in cases:
            row = dope.observations_from_sessions([session], "mrad")[0]
            self.assertFalse(row["usable"])
            self.assertIn(expected, row["why"])

    def test_it_works_in_moa(self):
        rows = dope.observations_from_sessions([self._session()], "moa")
        self.assertAlmostEqual(rows[0]["group_offset"], 0.57, places=2)

    def test_no_sessions_is_not_an_error(self):
        self.assertEqual(dope.observations_from_sessions([], "mrad"), [])
        self.assertEqual(dope.observations_from_sessions(None, "mrad"), [])


class CardTests(unittest.TestCase):
    def test_a_card_belongs_to_a_rifle_and_a_load(self):
        self.assertEqual(dope.card_key(FULL), "1:3")
        self.assertEqual(dope.card_key({}), "0:0")

    def test_rows_are_sorted_and_rubbish_dropped(self):
        card = dope.clean_card({"unit": "moa", "rows": [
            {"distance_m": 300, "elevation": 6.2},
            {"distance_m": "nonsense"},
            {"distance_m": -5},
            {"distance_m": 100, "elevation": 0},
            "not a row",
        ]})
        self.assertEqual([r["distance_m"] for r in card["rows"]], [100.0, 300.0])

    def test_an_unknown_unit_falls_back(self):
        self.assertEqual(dope.clean_card({"unit": "clicks", "rows": []})["unit"], "mrad")

    def test_notes_are_capped(self):
        card = dope.clean_card({"rows": [{"distance_m": 100, "note": "x" * 200}]})
        self.assertLessEqual(len(card["rows"][0]["note"]), 80)

    def test_rubbish_becomes_a_blank_card(self):
        for bad in (None, [], "card", 7):
            self.assertEqual(dope.clean_card(bad)["rows"][0]["distance_m"], 50)

    def test_a_blank_card_covers_the_default_distances(self):
        card = dope.blank_card()
        self.assertEqual([r["distance_m"] for r in card["rows"]], dope.default_distances())
        self.assertTrue(all(r["elevation"] is None for r in card["rows"]))


if __name__ == "__main__":
    unittest.main()
