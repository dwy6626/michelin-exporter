"""Tests for list name boundary matching — prevents star emoji misplacement.

The save dialog matcher must NOT treat ⭐ (U+2B50) as a word boundary.
Otherwise "臺北 米其林 ⭐" (1-star) would be a boundary-bounded substring
of "臺北 米其林 ⭐⭐⭐" (3-star), causing restaurants to be saved to the
wrong Google Maps list.
"""

import unittest
from pathlib import Path

from michelin_scraper.adapters.google_maps_driver import (
    GoogleMapsDriver,
    GoogleMapsDriverConfig,
    _contains_text_with_boundaries,
    _is_list_name_boundary_character,
)


def _build_driver() -> GoogleMapsDriver:
    return GoogleMapsDriver(
        GoogleMapsDriverConfig(
            user_data_dir=Path("/tmp/michelin-list-match-test"),
            headless=True,
            sync_delay_seconds=0,
        )
    )


class BoundaryCharacterTests(unittest.TestCase):
    """Verify which characters count as word boundaries."""

    def test_empty_string_is_boundary(self) -> None:
        self.assertTrue(_is_list_name_boundary_character(""))

    def test_space_is_boundary(self) -> None:
        self.assertTrue(_is_list_name_boundary_character(" "))

    def test_pipe_is_boundary(self) -> None:
        self.assertTrue(_is_list_name_boundary_character("|"))

    def test_plus_is_boundary(self) -> None:
        self.assertTrue(_is_list_name_boundary_character("+"))

    def test_star_emoji_is_not_boundary(self) -> None:
        """⭐ (U+2B50) is used in level badges and must NOT be a boundary."""
        self.assertFalse(_is_list_name_boundary_character("⭐"))

    def test_yum_emoji_is_not_boundary(self) -> None:
        """😋 is used for Bib Gourmand badge and must NOT be a boundary."""
        self.assertFalse(_is_list_name_boundary_character("😋"))

    def test_cjk_character_is_not_boundary(self) -> None:
        self.assertFalse(_is_list_name_boundary_character("米"))

    def test_latin_letter_is_not_boundary(self) -> None:
        self.assertFalse(_is_list_name_boundary_character("a"))


class ContainsWithBoundariesTests(unittest.TestCase):
    """Verify substring-with-boundary matching for list names."""

    def test_exact_substring_with_space_boundary(self) -> None:
        self.assertTrue(
            _contains_text_with_boundaries(
                haystack="臺北 米其林 ⭐ 3 saved",
                needle="臺北 米其林 ⭐",
            )
        )

    def test_one_star_not_substring_of_three_star(self) -> None:
        """Critical: '⭐' followed by '⭐' is NOT a boundary."""
        self.assertFalse(
            _contains_text_with_boundaries(
                haystack="臺北 米其林 ⭐⭐⭐",
                needle="臺北 米其林 ⭐",
            )
        )

    def test_one_star_not_substring_of_two_star(self) -> None:
        self.assertFalse(
            _contains_text_with_boundaries(
                haystack="臺北 米其林 ⭐⭐",
                needle="臺北 米其林 ⭐",
            )
        )

    def test_one_star_not_substring_of_three_star_with_suffix(self) -> None:
        """Even with trailing text, the star boundary must block."""
        self.assertFalse(
            _contains_text_with_boundaries(
                haystack="臺北 米其林 ⭐⭐⭐ 3 saved",
                needle="臺北 米其林 ⭐",
            )
        )

    def test_two_star_not_substring_of_three_star(self) -> None:
        self.assertFalse(
            _contains_text_with_boundaries(
                haystack="臺北 米其林 ⭐⭐⭐",
                needle="臺北 米其林 ⭐⭐",
            )
        )

    def test_three_star_not_substring_of_one_star(self) -> None:
        """Longer needle doesn't appear in shorter haystack."""
        self.assertFalse(
            _contains_text_with_boundaries(
                haystack="臺北 米其林 ⭐",
                needle="臺北 米其林 ⭐⭐⭐",
            )
        )

    def test_exact_match_is_also_boundary_match(self) -> None:
        self.assertTrue(
            _contains_text_with_boundaries(
                haystack="臺北 米其林 ⭐",
                needle="臺北 米其林 ⭐",
            )
        )

    def test_bib_gourmand_not_confused_with_star(self) -> None:
        self.assertFalse(
            _contains_text_with_boundaries(
                haystack="臺北 米其林 😋",
                needle="臺北 米其林 ⭐",
            )
        )


class ListNameMatchScoreTests(unittest.TestCase):
    """Verify the scoring logic that selects the correct list."""

    def setUp(self) -> None:
        self.driver = _build_driver()

    def test_exact_match_scores_3(self) -> None:
        score = self.driver._list_name_match_score(
            list_name="臺北 米其林 ⭐",
            candidate_text="臺北 米其林 ⭐",
        )
        self.assertEqual(score, 3)

    def test_one_star_target_does_not_match_three_star_candidate(self) -> None:
        """Must NOT match — this was the root cause of the misplacement bug."""
        score = self.driver._list_name_match_score(
            list_name="臺北 米其林 ⭐",
            candidate_text="臺北 米其林 ⭐⭐⭐",
        )
        self.assertEqual(score, 0)

    def test_one_star_target_does_not_match_two_star_candidate(self) -> None:
        score = self.driver._list_name_match_score(
            list_name="臺北 米其林 ⭐",
            candidate_text="臺北 米其林 ⭐⭐",
        )
        self.assertEqual(score, 0)

    def test_one_star_with_suffix_scores_2(self) -> None:
        """Target appears with a whitespace boundary → valid partial match."""
        score = self.driver._list_name_match_score(
            list_name="臺北 米其林 ⭐",
            candidate_text="臺北 米其林 ⭐ 3 saved",
        )
        self.assertEqual(score, 2)

    def test_three_star_with_suffix_does_not_match_one_star(self) -> None:
        score = self.driver._list_name_match_score(
            list_name="臺北 米其林 ⭐",
            candidate_text="臺北 米其林 ⭐⭐⭐ 3 saved",
        )
        self.assertEqual(score, 0)

    def test_three_star_exact_match(self) -> None:
        score = self.driver._list_name_match_score(
            list_name="臺北 米其林 ⭐⭐⭐",
            candidate_text="臺北 米其林 ⭐⭐⭐",
        )
        self.assertEqual(score, 3)

    def test_empty_target_scores_0(self) -> None:
        score = self.driver._list_name_match_score(
            list_name="",
            candidate_text="臺北 米其林 ⭐",
        )
        self.assertEqual(score, 0)

    def test_empty_candidate_scores_0(self) -> None:
        score = self.driver._list_name_match_score(
            list_name="臺北 米其林 ⭐",
            candidate_text="",
        )
        self.assertEqual(score, 0)


if __name__ == "__main__":
    unittest.main()
