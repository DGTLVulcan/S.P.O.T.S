import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spots import ranges


class RangeLoadingTests(unittest.TestCase):
    def test_eagle_park_is_built_in(self):
        item = ranges.get_range("eagle-park")
        self.assertIsNotNone(item)
        self.assertEqual(item["name"], "Eagle Park Shooting Complex")
        self.assertIn("Little River", item["address"])

    def test_listing_carries_no_rules(self):
        listed = ranges.list_ranges()
        self.assertTrue(listed)
        for entry in listed:
            self.assertNotIn("sections", entry)
            self.assertIn("section_count", entry)

    def test_default_range_exists(self):
        self.assertIsNotNone(ranges.get_range(ranges.default_range_id()))

    def test_unknown_range_is_none(self):
        self.assertIsNone(ranges.get_range("no-such-range"))
        self.assertIsNone(ranges.get_range(""))
        self.assertIsNone(ranges.get_range(None))

    def test_ids_that_could_escape_the_directory_are_refused(self):
        for bad in ("../secrets", "..", "a/b", "C:/windows", "eagle park", "Eagle-Park"):
            self.assertIsNone(ranges.get_range(bad), bad)


class EagleParkContentTests(unittest.TestCase):
    """The rules are a safety document; check the transcription is intact."""

    @classmethod
    def setUpClass(cls):
        cls.item = ranges.get_range("eagle-park")

    def test_it_records_where_it_came_from(self):
        source = self.item["source"]
        for field in ("title", "version", "date", "publisher", "url"):
            self.assertTrue(source.get(field), field)
        self.assertEqual(source["version"], "5.6")

    def test_every_numbered_section_is_present_and_in_order(self):
        numbered = [s["number"] for s in self.item["sections"] if (s["number"] or "").isdigit()]
        # 1..19 in the document; 20 is the map, which is held separately.
        self.assertEqual(numbered, [str(n) for n in range(1, 20)])

    def test_the_attachments_are_present(self):
        titles = {s["title"] for s in self.item["sections"]}
        self.assertIn("Stuck Live Round Policy", titles)
        self.assertIn("Range Approvals", titles)

    def test_rule_numbers_run_in_sequence_within_each_section(self):
        for section in self.item["sections"]:
            if not (section["number"] or "").isdigit():
                continue
            top = [b["number"] for b in section["blocks"]
                   if b["kind"] == "rule" and b["number"].count(".") == 1]
            expected = [f"{section['number']}.{i}" for i in range(1, len(top) + 1)]
            self.assertEqual(top, expected, f"section {section['number']}")

    def test_known_rules_are_transcribed_intact(self):
        text = json.dumps(self.item)
        for phrase in [
            "THE RANGE OFFICER MUST BE OBEYED AT ALL TIMES",
            "Centrefire targets",
            "minimum hardness of 360 Brinell",
            "High-visibility upper body clothing must be worn to move beyond 100m",
            "Spring mount polymer targets are prohibited",
        ]:
            self.assertIn(phrase, text, phrase)

    def test_no_page_furniture_leaked_into_the_rules(self):
        for section in self.item["sections"]:
            for block in section["blocks"]:
                body = block.get("text", "") + " ".join(block.get("items", []))
                self.assertNotIn("Range Rules (Version", body)
                self.assertNotIn("....", body)

    def test_both_map_versions_are_present_and_on_disk(self):
        views = self.item["map"]["views"]
        self.assertEqual([v["id"] for v in views], ["scan", "vector"])
        static = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "spots", "web", "static")
        for view in views:
            for field in ("label", "kind", "file", "width", "height", "note"):
                self.assertTrue(view.get(field), f"{view['id']}.{field}")
            self.assertTrue(os.path.isfile(os.path.join(static, view["file"])), view["file"])

    def test_the_default_view_is_one_that_exists(self):
        map_data = self.item["map"]
        self.assertIn(map_data["default_view"], [v["id"] for v in map_data["views"]])

    def test_each_version_says_what_it_is(self):
        # Both are copies of a safety document, so neither may pass itself
        # off as the original without qualification.
        notes = {v["id"]: v["note"] for v in self.item["map"]["views"]}
        self.assertIn("authoritative", notes["scan"])
        self.assertIn("check anything critical against the scan", notes["vector"])

    def test_the_vector_map_is_well_formed_svg_covering_the_whole_plan(self):
        import xml.etree.ElementTree as ET
        view = next(v for v in self.item["map"]["views"] if v["id"] == "vector")
        static = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "spots", "web", "static", view["file"])
        root = ET.parse(static).getroot()
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertEqual(root.get("viewBox"), f"0 0 {view['width']} {view['height']}")
        paths = root.findall(".//{http://www.w3.org/2000/svg}path")
        self.assertGreaterEqual(len(paths), 5)
        # A trace with only a handful of outlines would mean the linework
        # was dropped somewhere between the scan and the file.
        self.assertGreater(sum(p.get("d").count("M") for p in paths), 3000)

    def test_it_is_a_substantial_transcription_not_a_stub(self):
        self.assertGreater(ranges.rule_count(self.item), 250)


if __name__ == "__main__":
    unittest.main()
