"""Unit tests for HTML parsing helpers."""

import unittest
from typing import Any

from bs4 import BeautifulSoup

from michelin_scraper.catalog.levels import LEVEL_LABELS
from michelin_scraper.scraping.parsers import (
    parse_gm_iframe_url,
    parse_price_cuisine,
    parse_rating,
)


class ParseRatingTests(unittest.TestCase):
    def test_returns_selected_when_distinction_icon_is_missing(self) -> None:
        self.assertEqual(parse_rating(None), LEVEL_LABELS["selected"])

    def test_returns_bib_gourmand_when_bib_icon_exists(self) -> None:
        distinction_icon = self._build_distinction_icon(
            """
            <span class="distinction-icon">
              <img class="michelin-award" src="/assets/1star.png" />
              <img class="michelin-award" src="/assets/bib-gourmand.png" />
            </span>
            """
        )

        self.assertEqual(parse_rating(distinction_icon), LEVEL_LABELS["bib-gourmand"])

    def test_returns_star_rating_based_on_one_star_icon_count(self) -> None:
        distinction_icon = self._build_distinction_icon(
            """
            <span class="distinction-icon">
              <img class="michelin-award" src="/assets/1star.png" />
              <img class="michelin-award" src="/assets/1star.png" />
            </span>
            """
        )

        self.assertEqual(parse_rating(distinction_icon), LEVEL_LABELS["two-star"])

    def test_ignores_non_string_image_sources(self) -> None:
        distinction_icon = self._build_distinction_icon(
            """
            <span class="distinction-icon">
              <img class="michelin-award" />
            </span>
            """
        )

        self.assertEqual(parse_rating(distinction_icon), LEVEL_LABELS["selected"])

    def _build_distinction_icon(self, html: str) -> Any:
        soup = BeautifulSoup(html, "html.parser")
        return soup.select_one("span.distinction-icon")


class ParsePriceCuisineTests(unittest.TestCase):
    def test_returns_empty_values_when_footer_tag_is_missing(self) -> None:
        self.assertEqual(parse_price_cuisine(None), ("", ""))

    def test_splits_price_and_cuisine_from_footer_text(self) -> None:
        footer_tag = self._build_footer_tag("<div>$$ · Taiwanese</div>")

        self.assertEqual(parse_price_cuisine(footer_tag), ("$$", "Taiwanese"))

    def test_returns_empty_cuisine_when_only_price_exists(self) -> None:
        footer_tag = self._build_footer_tag("<div>$$</div>")

        self.assertEqual(parse_price_cuisine(footer_tag), ("$$", ""))

    def _build_footer_tag(self, html: str) -> Any:
        soup = BeautifulSoup(html, "html.parser")
        return soup.select_one("div")


class ParseGmIframeUrlTests(unittest.TestCase):
    def test_parses_latitude_and_longitude_from_q_query(self) -> None:
        latitude, longitude = parse_gm_iframe_url("https://maps.google.com/?q=25.033,121.565")

        self.assertEqual(latitude, 25.033)
        self.assertEqual(longitude, 121.565)

    def test_returns_empty_coordinates_when_q_query_is_missing(self) -> None:
        self.assertEqual(
            parse_gm_iframe_url("https://maps.google.com/?foo=bar"), ("", "")
        )

    def test_returns_empty_coordinates_when_values_are_invalid(self) -> None:
        self.assertEqual(
            parse_gm_iframe_url("https://maps.google.com/?q=abc,xyz"), ("", "")
        )


if __name__ == "__main__":
    unittest.main()
