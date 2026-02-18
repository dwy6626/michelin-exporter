"""Tests for per-item delay behavior on Michelin listing pages."""

import unittest
from unittest.mock import Mock, patch

import requests
from bs4 import BeautifulSoup

from michelin_scraper.scraping.fetcher import FetchPageResult
from michelin_scraper.scraping.listing_page import scrape_results_single_page

_LISTING_HTML_WITH_TWO_CARDS = """
<html>
  <body>
    <div class="card__menu">
      <a href="/restaurant-1">
        <h3 class="card__menu-content--title">Alpha</h3>
      </a>
      <div class="card__menu-footer--score">Tokyo</div>
      <div class="card__menu-footer--score">$$ · French</div>
      <span class="distinction-icon">
        <img class="michelin-award" src="/assets/1star.png" />
      </span>
    </div>
    <div class="card__menu">
      <a href="/restaurant-2">
        <h3 class="card__menu-content--title">Beta</h3>
      </a>
      <div class="card__menu-footer--score">Tokyo</div>
      <div class="card__menu-footer--score">$$ · Japanese</div>
      <span class="distinction-icon">
        <img class="michelin-award" src="/assets/1star.png" />
      </span>
    </div>
  </body>
</html>
""".strip()


class _NoOpReporter:
    def update(self, message: str, progress: float | None = None) -> None:
        del message, progress

    def log(self, message: str) -> None:
        del message

    def finish(self, message: str | None = None) -> None:
        del message


class ListingPageSleepTests(unittest.TestCase):
    @patch("michelin_scraper.scraping.listing_page.time.sleep")
    @patch("michelin_scraper.scraping.listing_page.scrape_restaurant_page")
    @patch("michelin_scraper.scraping.listing_page.fetch_page_soup")
    def test_scrape_results_single_page_sleeps_between_cards(
        self,
        mock_fetch_page_soup: Mock,
        mock_scrape_restaurant_page: Mock,
        mock_sleep: Mock,
    ) -> None:
        mock_fetch_page_soup.return_value = FetchPageResult(
            soup=BeautifulSoup(_LISTING_HTML_WITH_TWO_CARDS, "html.parser"),
            fetch_failed=False,
        )
        mock_scrape_restaurant_page.return_value = {
            "Address": "",
            "Description": "",
            "Restaurant Website": "",
            "Telephone Number": "",
            "Reservation Link": "",
            "Latitude": "",
            "Longitude": "",
        }

        page_result = scrape_results_single_page(
            session=requests.Session(),
            url="https://guide.michelin.com/us/en/restaurants",
            headers={},
            tls_verify=True,
            page_number=1,
            estimated_total_pages=None,
            total_restaurants_so_far=0,
            progress_reporter=_NoOpReporter(),
            item_sleep_seconds=1.25,
        )

        self.assertEqual(len(page_result.restaurant_rows), 2)
        self.assertIsNone(page_result.next_url)
        self.assertIsNone(page_result.estimated_total_pages)
        self.assertFalse(page_result.fetch_failed)
        mock_sleep.assert_called_once_with(1.25)

    @patch("michelin_scraper.scraping.listing_page.time.sleep")
    @patch("michelin_scraper.scraping.listing_page.scrape_restaurant_page")
    @patch("michelin_scraper.scraping.listing_page.fetch_page_soup")
    def test_scrape_results_single_page_skips_sleep_when_delay_is_zero(
        self,
        mock_fetch_page_soup: Mock,
        mock_scrape_restaurant_page: Mock,
        mock_sleep: Mock,
    ) -> None:
        mock_fetch_page_soup.return_value = FetchPageResult(
            soup=BeautifulSoup(_LISTING_HTML_WITH_TWO_CARDS, "html.parser"),
            fetch_failed=False,
        )
        mock_scrape_restaurant_page.return_value = {
            "Address": "",
            "Description": "",
            "Restaurant Website": "",
            "Telephone Number": "",
            "Reservation Link": "",
            "Latitude": "",
            "Longitude": "",
        }

        page_result = scrape_results_single_page(
            session=requests.Session(),
            url="https://guide.michelin.com/us/en/restaurants",
            headers={},
            tls_verify=True,
            page_number=1,
            estimated_total_pages=None,
            total_restaurants_so_far=0,
            progress_reporter=_NoOpReporter(),
            item_sleep_seconds=0.0,
        )

        self.assertEqual(len(page_result.restaurant_rows), 2)
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
