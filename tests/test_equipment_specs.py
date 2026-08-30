"""Per-kind equipment specifications: the three kinds record different
things, and submitted values are validated against that schema."""

import os
import sys

# Import the app regardless of where the runner sets its working directory
# (Visual Studio Test Explorer, `python -m unittest`, and pytest all differ).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest

from spots.equipment_specs import (
    calibre_of,
    calibres_match,
    clean_specs,
    fields_for,
    normalise_calibre,
    schema_payload,
    summarise,
)


class SchemaTests(unittest.TestCase):
    def test_each_kind_has_its_own_fields(self):
        rifle = {f.key for f in fields_for("rifle")}
        scope = {f.key for f in fields_for("scope")}
        ammo = {f.key for f in fields_for("ammo")}
        self.assertIn("twist_rate", rifle)
        self.assertIn("click_value", scope)
        self.assertIn("bullet_grains", ammo)
        # The point of the change: they are genuinely different lists.
        self.assertNotEqual(rifle, ammo)
        self.assertNotIn("twist_rate", ammo)
        self.assertNotIn("bullet_grains", rifle)
        self.assertNotIn("click_value", rifle)

    def test_only_the_scope_click_fields_are_columns(self):
        columns = {
            (kind, f.key) for kind in ("rifle", "scope", "ammo") for f in fields_for(kind) if f.column
        }
        self.assertEqual(columns, {("scope", "click_value"), ("scope", "click_unit")})

    def test_payload_shape(self):
        payload = schema_payload()
        self.assertEqual(set(payload), {"rifle", "scope", "ammo"})
        for kind, meta in payload.items():
            self.assertTrue(meta["title"] and meta["singular"])
            for field in meta["fields"]:
                self.assertIn(field["type"], ("text", "number", "select"))
                if field["type"] == "select":
                    self.assertTrue(field["options"], f"{kind}.{field['key']}")

    def test_unknown_kind_has_no_fields(self):
        self.assertEqual(fields_for("scope_mount"), ())


class CleanSpecsTests(unittest.TestCase):
    def test_numbers_are_parsed(self):
        cleaned, errors = clean_specs("rifle", {"barrel_length_in": "20.5"})
        self.assertEqual(errors, [])
        self.assertEqual(cleaned["barrel_length_in"], 20.5)

    def test_bad_number_is_reported(self):
        _, errors = clean_specs("ammo", {"bullet_grains": "heavy"})
        self.assertEqual(len(errors), 1)
        self.assertIn("Bullet weight", errors[0])

    def test_select_must_be_a_known_option(self):
        _, errors = clean_specs("rifle", {"action": "trebuchet"})
        self.assertEqual(len(errors), 1)
        cleaned, errors = clean_specs("rifle", {"action": "bolt"})
        self.assertEqual((cleaned["action"], errors), ("bolt", []))

    def test_unknown_keys_are_dropped(self):
        cleaned, errors = clean_specs("rifle", {"calibre": ".308", "colour": "black"})
        self.assertEqual(cleaned, {"calibre": ".308"})
        self.assertEqual(errors, [])

    def test_blanks_are_omitted_rather_than_stored_empty(self):
        cleaned, _ = clean_specs("rifle", {"calibre": "   ", "twist_rate": ""})
        self.assertEqual(cleaned, {})

    def test_column_fields_are_left_to_the_caller(self):
        """click_value/click_unit are real columns, so clean_specs must not
        also copy them into the specs blob."""
        cleaned, _ = clean_specs("scope", {"click_value": "0.25", "click_unit": "moa",
                                           "reticle": "EBR-7C"})
        self.assertEqual(cleaned, {"reticle": "EBR-7C"})

    def test_non_dict_input(self):
        cleaned, errors = clean_specs("rifle", "nope")
        self.assertEqual(cleaned, {})
        self.assertTrue(errors)


class SummaryTests(unittest.TestCase):
    def test_rifle_summary(self):
        self.assertEqual(
            summarise({"kind": "rifle",
                       "specs": {"calibre": ".308 Win", "barrel_length_in": 20.0}}),
            '.308 Win 20"',
        )

    def test_ammo_summary(self):
        self.assertEqual(
            summarise({"kind": "ammo",
                       "specs": {"calibre": ".308 Win", "bullet_grains": 168.0}}),
            ".308 Win 168 gr",
        )

    def test_scope_falls_back_to_its_turret(self):
        """A scope with no magnification recorded is still identifiable by
        its click value, which is the thing that changes the maths."""
        self.assertEqual(
            summarise({"kind": "scope", "specs": {}, "click_value": 0.25, "click_unit": "moa"}),
            "0.25 moa/click",
        )
        self.assertEqual(
            summarise({"kind": "scope", "specs": {"magnification": "5-25x56"},
                       "click_value": 0.1, "click_unit": "mrad"}),
            "5-25x56 0.1 mrad/click",
        )

    def test_missing_specs_summarise_to_empty(self):
        self.assertEqual(summarise({"kind": "rifle", "specs": {}}), "")
        self.assertEqual(summarise({"kind": "ammo", "specs": None}), "")

    def test_whole_numbers_are_not_shown_as_floats(self):
        self.assertNotIn(".0", summarise({"kind": "ammo", "specs": {"bullet_grains": 168.0}}))


if __name__ == "__main__":
    unittest.main()


class CalibreMatchingTests(unittest.TestCase):
    """A rifle and its ammo have to agree on chambering, but people write
    the same cartridge several ways and often don't record it at all."""

    def test_same_cartridge_written_differently(self):
        for left, right in [
            (".308 Winchester", ".308 Win"),
            (".308 Winchester", "308win"),
            (".223 Remington", ".223 Rem"),
            ("  .308  Win ", ".308win"),
            ("6.5 Creedmoor", "6.5 creedmoor"),
        ]:
            self.assertTrue(calibres_match(left, right), f"{left} vs {right}")

    def test_different_cartridges_do_not_match(self):
        for left, right in [
            (".308 Winchester", ".223 Remington"),
            (".308 Win", ".300 Win Mag"),
            (".22 LR", ".22-250"),
            ("6.5 Creedmoor", ".308 Winchester"),
        ]:
            self.assertFalse(calibres_match(left, right), f"{left} vs {right}")

    def test_an_unrecorded_calibre_matches_anything(self):
        """Refusing to pair kit because a field is blank would be worse than
        letting it through -- there's no mismatch to prove."""
        self.assertTrue(calibres_match(None, ".308 Win"))
        self.assertTrue(calibres_match(".308 Win", None))
        self.assertTrue(calibres_match("", ""))
        self.assertTrue(calibres_match("   ", ".223"))

    def test_normalisation(self):
        self.assertEqual(normalise_calibre(".308 Winchester"), "308winchester")
        self.assertEqual(normalise_calibre("  6.5 Creedmoor "), "65creedmoor")
        self.assertEqual(normalise_calibre(None), "")

    def test_calibre_of_reads_the_specs(self):
        self.assertEqual(calibre_of({"specs": {"calibre": ".308 Win"}}), ".308 Win")
        self.assertIsNone(calibre_of({"specs": {}}))
        self.assertIsNone(calibre_of(None))
