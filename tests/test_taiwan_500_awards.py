"""Official-source discovery and complete Taiwan-folder inventory regressions."""

import unittest
from pathlib import Path
from xml.etree import ElementTree

from michelin_scraper.sources.taiwan_500_awards import (
    TAIWAN_500_AWARD_DEFINITIONS,
    _discover_award,
    _filter_award_folder,
    _fingerprint_roster,
)

FIXTURES = Path(__file__).parent / "fixtures" / "500_awards"


class Taiwan500AwardsTests(unittest.TestCase):
    def fetch(self, url: str) -> str:
        routes = {
            "https://udn.com/500bowlsaward/": "bowls-home.html",
            "https://udn.com/500bowlsaward/js/index.js?DA51fVkVF1": "bowls-index-excerpt.js",
            "https://udn.com/500bowlsaward/api/get_story.json?hash=DA51fVkVF1": "bowls-story.json",
            "https://reurl.cc/kpRv1q": "bowls-shortener.html",
            "https://www.google.com/maps/d/kml?mid=19E5ausxMMacpaiOhadZFvns_UOlgFNo&forcekml=1": "bowls-2026.kml",
            "https://udn.com/500dishesaward/": "dishes-home.html",
            "https://udn.com/500dishesaward/js/index.js?HHzlxNP": "dishes-index-excerpt.js",
            "https://udn.com/500dishesaward/api/get_story.json?hash=HHzlxNP": "dishes-story.json",
            "https://500times.udn.com/wtimes/story/122412/9754110": "dishes-article-excerpt.html",
            "https://www.google.com/maps/d/kml?mid=1dXq2uIcOlq5uQFJn919qudIFehIMWSw&forcekml=1": "dishes-2026.kml",
        }
        return (FIXTURES / routes[url]).read_text(encoding="utf-8")

    def test_recorded_official_sources_select_complete_current_taiwan_folders(self) -> None:
        for definition, expected_count in zip(TAIWAN_500_AWARD_DEFINITIONS, (472, 319), strict=True):
            with self.subTest(award=definition.award):
                roster = _discover_award(definition, self.fetch)
                self.assertEqual(roster.edition, 2026)
                self.assertEqual(roster.source_point_count, expected_count)
                self.assertEqual(len(roster.parsed.rows), expected_count)
                self.assertIn("{得獎菜色}", roster.note_template)
                self.assertNotIn("{edition}", roster.note_template)
                self.assertIn("2026", roster.note_template)
                self.assertTrue(all(row["Address"] for row in roster.parsed.rows))
                if definition.award == "dishes":
                    self.assertEqual(roster.total_kml_point_count, 513)
                    self.assertEqual(dict(roster.excluded_folder_counts), {"亞洲100盤2026": 89, "麥卡倫Macallan": 105})
                    names = [row["Name"] for row in roster.parsed.rows]
                    self.assertEqual(names.count("Petit Petit"), 1)
                    self.assertIn("小小樹食 0km山物所", names)
                    self.assertIn("小小樹食 忠泰店", names)
                    self.assertIn("小小樹食 敦南店", names)

    def test_fingerprint_ignores_row_order_but_tracks_award_fields(self) -> None:
        roster = _discover_award(TAIWAN_500_AWARD_DEFINITIONS[0], self.fetch)
        kwargs = dict(award=roster.award, edition=roster.edition,
                      document_name=roster.document_name, folder_name=roster.folder_name)
        self.assertEqual(roster.source_fingerprint, _fingerprint_roster(rows=tuple(reversed(roster.parsed.rows)), **kwargs))
        changed = [dict(row) for row in roster.parsed.rows]
        changed[0]["DescriptionFields"] = dict(changed[0]["DescriptionFields"], 電話="")
        self.assertNotEqual(roster.source_fingerprint, _fingerprint_roster(rows=tuple(changed), **kwargs))

    def test_wrong_edition_or_missing_authorized_folder_fails_closed(self) -> None:
        kml = (FIXTURES / "dishes-2026.kml").read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not match current edition"):
            _filter_award_folder(kml_text=kml, award="dishes", edition=2027)
        with self.assertRaisesRegex(ValueError, "requires exactly one"):
            _filter_award_folder(kml_text=kml.replace(
                "<name>500盤2026</name>", "<name>亞洲100盤2026</name>"), award="dishes", edition=2026)

    def test_unimportable_selected_row_is_never_an_empty_or_partial_success(self) -> None:
        definition = TAIWAN_500_AWARD_DEFINITIONS[0]
        kml_url = "https://www.google.com/maps/d/kml?mid=19E5ausxMMacpaiOhadZFvns_UOlgFNo&forcekml=1"
        root = ElementTree.fromstring(self.fetch(kml_url))
        namespace = {"k": "http://www.opengis.net/kml/2.2"}
        placemark = root.find(".//k:Placemark", namespace)
        assert placemark is not None
        name = placemark.find("k:name", namespace)
        assert name is not None
        name.text = ""
        invalid_kml = ElementTree.tostring(root, encoding="unicode")
        with self.assertRaisesRegex(ValueError, "unimportable rows"):
            _discover_award(definition, lambda url: invalid_kml if url == kml_url else self.fetch(url))


if __name__ == "__main__":
    unittest.main()
