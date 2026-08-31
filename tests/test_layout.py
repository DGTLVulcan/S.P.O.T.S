import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spots import layout
from spots.storage import Storage


def tiles_in(cleaned):
    return [tile for column in cleaned["columns"] for tile in column["tiles"]]


class CleanLayoutTests(unittest.TestCase):
    def test_default_holds_every_tile_exactly_once(self):
        placed = tiles_in(layout.DEFAULT_LAYOUT)
        self.assertCountEqual(placed, list(layout.TILES))

    def test_default_matches_the_original_hand_written_page(self):
        columns = layout.DEFAULT_LAYOUT["columns"]
        self.assertEqual(len(columns), 2)
        self.assertEqual(columns[0]["tiles"], ["range", "feed", "score", "scope"])
        self.assertEqual(columns[1]["tiles"], ["group-stats", "shots", "subgroups"])
        self.assertEqual(columns[0]["flow"], "stack")
        self.assertEqual(columns[1]["flow"], "wrap")

    def test_default_layout_returns_an_independent_copy(self):
        first = layout.default_layout()
        first["columns"][0]["tiles"].append("shots")
        self.assertNotIn("shots", layout.DEFAULT_LAYOUT["columns"][0]["tiles"])

    def test_rearrangement_is_kept(self):
        cleaned = layout.clean_layout({"columns": [
            {"weight": 4, "flow": "wrap", "tiles": ["shots", "feed", "range"]},
            {"weight": 1, "flow": "stack", "tiles": ["score", "scope", "group-stats", "subgroups"]},
        ]})
        self.assertEqual(cleaned["columns"][0]["tiles"], ["shots", "feed", "range"])
        self.assertEqual(cleaned["columns"][0]["weight"], 4)
        self.assertEqual(cleaned["columns"][0]["flow"], "wrap")

    def test_unknown_tiles_are_dropped(self):
        cleaned = layout.clean_layout({"columns": [
            {"tiles": ["feed", "not-a-real-card", "shots"]},
        ]})
        self.assertNotIn("not-a-real-card", tiles_in(cleaned))

    def test_a_tile_the_layout_never_mentions_comes_back(self):
        # A layout saved before a card existed must not make it vanish.
        cleaned = layout.clean_layout({"columns": [{"tiles": ["feed"]}]})
        self.assertCountEqual(tiles_in(cleaned), list(layout.TILES))

    def test_duplicates_collapse_to_the_first_position(self):
        cleaned = layout.clean_layout({"columns": [
            {"tiles": ["feed", "shots"]},
            {"tiles": ["shots", "scope"]},
        ]})
        placed = tiles_in(cleaned)
        self.assertEqual(placed.count("shots"), 1)
        self.assertEqual(cleaned["columns"][0]["tiles"][:2], ["feed", "shots"])

    def test_weights_are_clamped(self):
        cleaned = layout.clean_layout({"columns": [
            {"weight": 99, "tiles": ["feed"]},
            {"weight": -4, "tiles": ["shots"]},
        ]})
        self.assertEqual(cleaned["columns"][0]["weight"], layout.MAX_WEIGHT)
        self.assertEqual(cleaned["columns"][1]["weight"], layout.MIN_WEIGHT)

    def test_unknown_flow_falls_back_to_stack(self):
        cleaned = layout.clean_layout({"columns": [{"flow": "diagonal", "tiles": ["feed"]}]})
        self.assertEqual(cleaned["columns"][0]["flow"], "stack")

    def test_column_count_is_capped(self):
        cleaned = layout.clean_layout({"columns": [{"tiles": [t]} for t in layout.TILES]})
        self.assertLessEqual(len(cleaned["columns"]), layout.MAX_COLUMNS)
        self.assertCountEqual(tiles_in(cleaned), list(layout.TILES))

    def test_empty_columns_are_dropped(self):
        cleaned = layout.clean_layout({"columns": [
            {"tiles": []},
            {"tiles": ["range", "feed", "score", "scope", "group-stats", "shots", "subgroups"]},
        ]})
        self.assertEqual(len(cleaned["columns"]), 1)

    def test_rubbish_falls_back_to_the_default(self):
        for bad in (None, [], "columns", 7, {}, {"columns": None}, {"columns": []}):
            self.assertEqual(layout.clean_layout(bad), layout.DEFAULT_LAYOUT)

    def test_loads_survives_broken_json(self):
        self.assertEqual(layout.loads("{not json"), layout.DEFAULT_LAYOUT)
        self.assertEqual(layout.loads(""), layout.DEFAULT_LAYOUT)
        self.assertEqual(layout.loads(None), layout.DEFAULT_LAYOUT)

    def test_dumps_then_loads_round_trips(self):
        wanted = {"columns": [
            {"weight": 5, "flow": "wrap", "tiles": ["group-stats", "feed"]},
            {"weight": 2, "flow": "stack", "tiles": ["shots", "score", "scope", "subgroups"]},
        ]}
        self.assertEqual(layout.loads(layout.dumps(wanted)), layout.clean_layout(wanted))


class HiddenCardTests(unittest.TestCase):
    def test_a_hidden_card_is_not_put_back(self):
        # Without this, hiding a card would be undone by the same rule that
        # restores a card the layout was written before.
        cleaned = layout.clean_layout({
            "columns": [{"tiles": ["feed"]}],
            "hidden": ["shots"],
        })
        self.assertEqual(cleaned["hidden"], ["shots"])
        self.assertNotIn("shots", tiles_in(cleaned))

    def test_a_card_cannot_be_hidden_and_placed_at_once(self):
        cleaned = layout.clean_layout({
            "columns": [{"tiles": ["feed", "shots"]}],
            "hidden": ["shots"],
        })
        self.assertNotIn("shots", tiles_in(cleaned))
        self.assertEqual(cleaned["hidden"], ["shots"])

    def test_unknown_hidden_ids_are_dropped(self):
        cleaned = layout.clean_layout({"columns": [{"tiles": ["feed"]}],
                                       "hidden": ["nope", "shots"]})
        self.assertEqual(cleaned["hidden"], ["shots"])

    def test_every_card_can_be_hidden_without_losing_the_layout(self):
        cleaned = layout.clean_layout({"columns": [{"tiles": []}],
                                       "hidden": list(layout.TILES)})
        self.assertEqual(sorted(cleaned["hidden"]), sorted(layout.TILES))
        self.assertEqual(tiles_in(cleaned), [])

    def test_the_default_hides_nothing(self):
        self.assertEqual(layout.DEFAULT_LAYOUT["hidden"], [])


class TileSizeTests(unittest.TestCase):
    def test_the_default_sizes_nothing(self):
        self.assertEqual(layout.DEFAULT_LAYOUT["sizes"], {})

    def test_a_size_is_kept(self):
        cleaned = layout.clean_layout({
            "columns": [{"tiles": ["shots"]}],
            "sizes": {"shots": {"w": 3, "h": 320}},
        })
        self.assertEqual(cleaned["sizes"]["shots"], {"w": 3, "h": 320})

    def test_defaults_are_not_stored(self):
        # Otherwise every card ends up in the file for no reason.
        cleaned = layout.clean_layout({
            "columns": [{"tiles": ["shots", "feed"]}],
            "sizes": {"shots": {"w": 1, "h": 0}, "feed": {"w": 2, "h": 0}},
        })
        self.assertNotIn("shots", cleaned["sizes"])
        self.assertEqual(cleaned["sizes"]["feed"], {"w": 2, "h": 0})

    def test_sizes_are_clamped(self):
        cleaned = layout.clean_layout({
            "columns": [{"tiles": ["shots", "feed"]}],
            "sizes": {"shots": {"w": 99, "h": 99999}, "feed": {"w": -3, "h": -50}},
        })
        self.assertEqual(cleaned["sizes"]["shots"],
                         {"w": layout.MAX_TILE_WIDTH, "h": layout.MAX_TILE_HEIGHT})
        self.assertNotIn("feed", cleaned["sizes"])

    def test_rubbish_sizes_are_dropped(self):
        cleaned = layout.clean_layout({
            "columns": [{"tiles": ["shots"]}],
            "sizes": {"nope": {"w": 3}, "shots": "big", "feed": None},
        })
        self.assertEqual(cleaned["sizes"], {})

    def test_a_size_survives_a_round_trip(self):
        wanted = {"columns": [{"tiles": ["feed", "shots"]}],
                  "sizes": {"shots": {"w": 4, "h": 240}}}
        self.assertEqual(layout.loads(layout.dumps(wanted))["sizes"],
                         {"shots": {"w": 4, "h": 240}})

    def test_a_hidden_card_keeps_its_size(self):
        cleaned = layout.clean_layout({
            "columns": [{"tiles": ["feed"]}],
            "hidden": ["shots"],
            "sizes": {"shots": {"w": 3, "h": 160}},
        })
        self.assertEqual(cleaned["sizes"]["shots"], {"w": 3, "h": 160})


class LayoutStorageTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.storage = Storage(self.path)

    def tearDown(self):
        self.storage.close()
        os.unlink(self.path)

    def test_a_fresh_install_gets_the_default(self):
        self.assertEqual(self.storage.get_layout(), layout.DEFAULT_LAYOUT)

    def test_layout_survives_reopening_the_database(self):
        wanted = {"columns": [
            {"weight": 3, "flow": "stack", "tiles": ["shots", "feed", "score"]},
            {"weight": 2, "flow": "wrap", "tiles": ["scope", "group-stats", "subgroups"]},
        ]}
        self.storage.set_layout(wanted)
        self.storage.close()

        # Reopening is what a restart or a Pi reboot actually does.
        reopened = Storage(self.path)
        try:
            self.assertEqual(reopened.get_layout(), layout.clean_layout(wanted))
        finally:
            reopened.close()
            self.storage = Storage(self.path)

    def test_set_layout_returns_what_was_kept(self):
        kept = self.storage.set_layout({"columns": [{"tiles": ["feed", "bogus"]}]})
        self.assertNotIn("bogus", tiles_in(kept))
        self.assertEqual(kept, self.storage.get_layout())

    def test_reset_goes_back_to_the_default(self):
        self.storage.set_layout({"columns": [{"weight": 6, "tiles": ["shots"]}]})
        self.assertEqual(self.storage.reset_layout(), layout.DEFAULT_LAYOUT)
        self.assertEqual(self.storage.get_layout(), layout.DEFAULT_LAYOUT)

    def test_a_corrupt_stored_value_does_not_break_the_page(self):
        self.storage.set_state("dashboard_layout", "}{ not json")
        self.assertEqual(self.storage.get_layout(), layout.DEFAULT_LAYOUT)


if __name__ == "__main__":
    unittest.main()
