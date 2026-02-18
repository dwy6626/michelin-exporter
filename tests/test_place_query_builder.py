"""Tests for Google Maps place query cascade behavior."""

import unittest

from michelin_scraper.application.place_query_builder import build_place_query_attempts


class PlaceQueryBuilderTests(unittest.TestCase):
    def test_build_place_query_attempts_uses_expected_priority(self) -> None:
        row = {
            "Name": "Alpha",
            "City": "Taipei",
            "Address": "No. 1 Example Street",
            "Cuisine": "Taiwanese",
            "Latitude": "25.0330",
            "Longitude": "121.5654",
        }

        attempts = build_place_query_attempts(row)

        self.assertEqual(
            attempts,
            (
                "Alpha Taipei",
                "Alpha",
                "No. 1 Example Street",
                "Alpha Taiwanese Taipei",
            ),
        )

    def test_build_place_query_attempts_removes_empty_and_duplicates(self) -> None:
        row = {
            "Name": "Alpha",
            "City": "",
            "Address": "Alpha",
            "Cuisine": "",
            "Latitude": "",
            "Longitude": "",
        }

        attempts = build_place_query_attempts(row)

        self.assertEqual(attempts, ("Alpha",))


if __name__ == "__main__":
    unittest.main()
