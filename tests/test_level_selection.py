"""Tests for level selection parsing."""

import unittest

from michelin_scraper.catalog.levels import LEVEL_SLUGS, parse_level_selection


class LevelSelectionTests(unittest.TestCase):
    def test_parse_level_selection_returns_all_when_empty(self) -> None:
        self.assertEqual(parse_level_selection(""), LEVEL_SLUGS)

    def test_parse_level_selection_preserves_catalog_order(self) -> None:
        selected = parse_level_selection("selected,one-star")
        self.assertEqual(selected, ("one-star", "selected"))

    def test_parse_level_selection_raises_for_unknown_value(self) -> None:
        with self.assertRaises(ValueError):
            parse_level_selection("selected,unknown-level")


if __name__ == "__main__":
    unittest.main()
