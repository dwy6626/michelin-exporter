"""Tests for level row routing behavior."""

import unittest

from michelin_scraper.application.row_router import LevelRowRouter, UnrecognizedRatingError
from michelin_scraper.catalog.levels import build_rating_to_output_level_slug_map

# Mapping that covers all known Michelin ratings, mirroring _build_row_router().
_ALL_RATINGS: dict[str, str] = {
    "1 Star": "one-star",
    "one-star": "one-star",
    "⭐": "one-star",
    "2 Stars": "two-star",
    "two-star": "two-star",
    "⭐⭐": "two-star",
    "3 Stars": "three-star",
    "three-star": "three-star",
    "⭐⭐⭐": "three-star",
    "Bib Gourmand": "bib-gourmand",
    "bib-gourmand": "bib-gourmand",
    "😋": "bib-gourmand",
    "Selected": "selected",
    "selected": "selected",
}


class LevelRowRouterTests(unittest.TestCase):
    def test_group_rows_by_level_combines_all_star_ratings_into_stars_bucket(self) -> None:
        router = LevelRowRouter(
            level_slugs=("stars", "selected"),
            rating_to_level_slug=build_rating_to_output_level_slug_map(("stars", "selected")),
        )

        grouped = router.group_rows_by_level(
            [
                {"Name": "Alpha", "Rating": "1 Star"},
                {"Name": "Beta", "Rating": "3 Stars"},
                {"Name": "Gamma", "Rating": "Selected"},
            ]
        )

        self.assertEqual([row["Name"] for row in grouped["stars"]], ["Alpha", "Beta"])
        self.assertEqual([row["Name"] for row in grouped["selected"]], ["Gamma"])

    def test_group_rows_by_level_uses_rating_mapping(self) -> None:
        router = LevelRowRouter(
            level_slugs=("one-star", "selected"),
            rating_to_level_slug=_ALL_RATINGS,
        )

        grouped = router.group_rows_by_level(
            [
                {"Name": "Alpha", "Rating": "1 Star"},
                {"Name": "Beta", "Rating": "Selected"},
            ]
        )

        self.assertEqual(len(grouped["one-star"]), 1)
        self.assertEqual(grouped["one-star"][0]["Name"], "Alpha")
        self.assertEqual(len(grouped["selected"]), 1)
        self.assertEqual(grouped["selected"][0]["Name"], "Beta")

    def test_group_rows_by_level_normalizes_rating_value_whitespace_and_case(self) -> None:
        router = LevelRowRouter(
            level_slugs=("one-star", "selected"),
            rating_to_level_slug=_ALL_RATINGS,
        )

        grouped = router.group_rows_by_level(
            [
                {"Name": "Alpha", "Rating": "  1   STAR  "},
                {"Name": "Beta", "Rating": "Selected"},
            ]
        )

        self.assertEqual(len(grouped["one-star"]), 1)
        self.assertEqual(grouped["one-star"][0]["Name"], "Alpha")
        self.assertEqual(len(grouped["selected"]), 1)
        self.assertEqual(grouped["selected"][0]["Name"], "Beta")

    def test_known_but_unselected_rating_is_skipped(self) -> None:
        """Bib Gourmand is a known rating — skip it, don't error."""
        router = LevelRowRouter(
            level_slugs=("one-star", "two-star", "three-star"),
            rating_to_level_slug=_ALL_RATINGS,
        )

        grouped = router.group_rows_by_level(
            [
                {"Name": "Le Palais", "Rating": "3 Stars"},
                {"Name": "Fujin Tree", "Rating": "1 Star"},
                {"Name": "A Bib Place", "Rating": "Bib Gourmand"},
                {"Name": "A Selected Place", "Rating": "Selected"},
            ]
        )

        self.assertEqual(len(grouped["three-star"]), 1)
        self.assertEqual(grouped["three-star"][0]["Name"], "Le Palais")
        self.assertEqual(len(grouped["one-star"]), 1)
        self.assertEqual(grouped["one-star"][0]["Name"], "Fujin Tree")
        self.assertEqual(len(grouped["two-star"]), 0)
        all_routed = [row for rows in grouped.values() for row in rows]
        self.assertEqual(len(all_routed), 2)

    def test_unrecognized_rating_raises_error(self) -> None:
        """A truly unknown rating means something is wrong — raise."""
        router = LevelRowRouter(
            level_slugs=("one-star", "selected"),
            rating_to_level_slug=_ALL_RATINGS,
        )

        with self.assertRaises(UnrecognizedRatingError) as ctx:
            router.group_rows_by_level(
                [{"Name": "Mystery", "Rating": "Something Unknown"}]
            )

        self.assertEqual(ctx.exception.rating, "Something Unknown")
        self.assertEqual(ctx.exception.restaurant_name, "Mystery")


if __name__ == "__main__":
    unittest.main()
