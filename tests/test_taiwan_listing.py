"""Recorded Taiwan listing regressions for results and recommendation boundaries."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from bs4 import BeautifulSoup

from michelin_scraper.catalog.targets import resolve_target
from michelin_scraper.scraping.fetcher import FetchPageResult
from michelin_scraper.scraping.listing_page import scrape_results_single_page
from michelin_scraper.scraping.models import NullProgressReporter
from michelin_scraper.scraping.restaurant_details import _extract_restaurant_page_data

_FIXTURES = Path(__file__).parent / "fixtures" / "michelin"


class TaiwanListingTests(unittest.TestCase):
    @patch("michelin_scraper.scraping.listing_page._fetch_restaurant_page_data")
    @patch("michelin_scraper.scraping.listing_page.fetch_page_soup")
    def test_verified_country_selection_keeps_48_results_and_excludes_recommendations(
        self, fetch: Mock, details: Mock,
    ) -> None:
        # Detail parsing has separate coverage; use recorded fields for this scope-only test.
        details.return_value = _extract_restaurant_page_data(
            BeautifulSoup((_FIXTURES / "brise-detail-2026-11-07.html").read_text(), "html.parser")
        )
        soup = BeautifulSoup((_FIXTURES / "taiwan-region-2026-11-07.html").read_text(), "html.parser")
        self.assertEqual(len(soup.select("div.card__menu")), 52)
        fetch.return_value = FetchPageResult(soup=soup, fetch_failed=False)
        with requests.Session() as session:
            result = scrape_results_single_page(
                session=session,
                url=resolve_target("taiwan", language="zh_TW").start_url,
                headers={}, tls_verify=True, page_number=1, estimated_total_pages=None,
                total_restaurants_so_far=0, progress_reporter=NullProgressReporter(), local_country_code="tw",
            )
        self.assertFalse(result.fetch_failed)
        self.assertEqual(result.total_restaurants, 434)
        self.assertEqual(len(result.restaurant_rows), 48)
        self.assertEqual(
            [row["Name"] for row in result.restaurant_rows[:5]],
            ["心怡素食", "欣葉小聚 (南港)", "天然茶莊", "食養山房", "logy"],
        )
        self.assertNotIn("老陳家", [row["Name"] for row in result.restaurant_rows])
        self.assertEqual(details.call_count, 48)
        self.assertIsNotNone(result.next_url)
        self.assertIn("taiwan-region/restaurants", result.next_url or "")

    @patch("michelin_scraper.scraping.listing_page._fetch_restaurant_page_data")
    @patch("michelin_scraper.scraping.listing_page.fetch_page_soup")
    def test_empty_search_does_not_fetch_or_emit_recommended_restaurants(self, fetch: Mock, details: Mock) -> None:
        soup = BeautifulSoup((_FIXTURES / "taiwan-empty-2026-11-07.html").read_text(), "html.parser")
        self.assertIn("抱歉，在搜尋的區域內沒有精選餐廳", soup.get_text())
        self.assertEqual(len(soup.select("div.card__menu")), 4)
        fetch.return_value = FetchPageResult(soup=soup, fetch_failed=False)
        on_item = Mock()
        with requests.Session() as session:
            result = scrape_results_single_page(
                session=session, url="https://guide.michelin.com/tw/zh_TW/selection/taiwan/restaurants",
                headers={}, tls_verify=True, page_number=1, estimated_total_pages=None,
                total_restaurants_so_far=0, progress_reporter=NullProgressReporter(),
                local_country_code="tw", on_item=on_item,
            )
        self.assertFalse(result.fetch_failed)
        self.assertEqual(result.restaurant_rows, [])
        self.assertIsNone(result.next_url)
        details.assert_not_called()
        on_item.assert_not_called()


if __name__ == "__main__":
    unittest.main()
