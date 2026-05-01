"""Tests for level selection parsing."""

import unittest

from michelin_scraper.catalog.levels import LEVEL_SLUGS, parse_level_selection


class LevelSelectionTests(unittest.TestCase):
    def test_parse_level_selection_returns_default_buckets_when_empty(self) -> None:
        self.assertEqual(parse_level_selection(""), LEVEL_SLUGS)

    def test_parse_level_selection_preserves_combined_bucket_order(self) -> None:
        selected = parse_level_selection("bib-gourmand,stars")
        self.assertEqual(selected, ("stars", "bib-gourmand"))

    def test_parse_level_selection_preserves_split_star_order(self) -> None:
        selected = parse_level_selection("selected,one-star")
        self.assertEqual(selected, ("one-star", "selected"))

    def test_parse_level_selection_raises_for_unknown_value(self) -> None:
        with self.assertRaises(ValueError):
            parse_level_selection("selected,unknown-level")

    def test_parse_level_selection_rejects_mixed_combined_and_split_star_values(self) -> None:
        with self.assertRaises(ValueError):
            parse_level_selection("stars,one-star")


if __name__ == "__main__":
    unittest.main()
