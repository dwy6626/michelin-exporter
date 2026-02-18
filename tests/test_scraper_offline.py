"""Offline integration tests for scraper pagination and parsing."""


import unittest
from typing import Any
from unittest.mock import patch

import requests

from michelin_scraper.scraping import crawl

LISTING_PAGE_1_URL = "https://guide.michelin.com/us/en/restaurants"
LISTING_PAGE_2_URL = "https://guide.michelin.com/us/en/restaurants?page=2"
RESTAURANT_1_URL = "https://guide.michelin.com/restaurant-1"
RESTAURANT_2_URL = "https://guide.michelin.com/restaurant-2"

LISTING_PAGE_1_HTML = """
<html>
  <body>
    <div class="card__menu">
      <a href="/restaurant-1">
        <h3 class="card__menu-content--title">Alpha</h3>
      </a>
      <div class="card__menu-footer--score">Taipei</div>
      <div class="card__menu-footer--score">$$ · Taiwanese</div>
      <span class="distinction-icon">
        <img class="michelin-award" src="/assets/1star.png" />
      </span>
    </div>
    <ul class="pagination">
      <li><a href="?page=1">1</a></li>
      <li><a href="?page=2">2</a></li>
      <li><a href="?page=2"><i class="fa-angle-right"></i></a></li>
    </ul>
  </body>
</html>
""".strip()

LISTING_PAGE_2_HTML = """
<html>
  <body>
    <div class="card__menu">
      <a href="/restaurant-2">
        <h3 class="card__menu-content--title">Beta</h3>
      </a>
      <div class="card__menu-footer--score">Taipei</div>
      <div class="card__menu-footer--score">$ · Noodles</div>
      <span class="distinction-icon">
        <img class="michelin-award" src="/assets/bib-gourmand.png" />
      </span>
    </div>
    <ul class="pagination">
      <li><a href="?page=1">1</a></li>
      <li><a href="?page=2">2</a></li>
    </ul>
  </body>
</html>
""".strip()

RESTAURANT_1_HTML = """
<html>
  <body>
    <div class="data-sheet__block--text">No. 1 Example Street</div>
    <div class="data-sheet__description">First test restaurant</div>
    <a data-event="CTA_website" href="https://alpha.example.com">Website</a>
    <a data-event="CTA_tel" href="tel:+88620001111">Phone</a>
    <a class="js-restaurant-book-btn" href="https://book.example.com/alpha">Reserve</a>
    <iframe src="https://maps.example.com"></iframe>
    <iframe src="https://maps.google.com/?q=25.033,121.565"></iframe>
  </body>
</html>
""".strip()

RESTAURANT_2_HTML = """
<html>
  <body>
    <div class="data-sheet__block--text">No. 2 Example Street</div>
    <div class="data-sheet__description">Second test restaurant</div>
    <a data-event="CTA_website" href="https://beta.example.com">Website</a>
    <a data-event="CTA_tel" href="tel:+88620002222">Phone</a>
    <a class="js-restaurant-book-btn" href="https://book.example.com/beta">Reserve</a>
    <iframe src="https://maps.google.com/?q=24.987,121.555"></iframe>
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


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP status {self.status_code}")


class ScraperOfflineTests(unittest.TestCase):
    @patch("michelin_scraper.scraping.fetcher.requests.Session.get")
    def test_crawl_with_mocked_html(self, mock_get: Any) -> None:
        html_by_url = {
            LISTING_PAGE_1_URL: LISTING_PAGE_1_HTML,
            LISTING_PAGE_2_URL: LISTING_PAGE_2_HTML,
            RESTAURANT_1_URL: RESTAURANT_1_HTML,
            RESTAURANT_2_URL: RESTAURANT_2_HTML,
        }

        def fake_get(*args: Any, **kwargs: Any) -> _FakeResponse:
            if args and isinstance(args[0], str):
                url = args[0]
            elif len(args) > 1 and isinstance(args[1], str):
                url = args[1]
            else:
                url = kwargs.get("url")
            if not isinstance(url, str):
                raise AssertionError("Unable to resolve URL from mocked requests call.")
            if url not in html_by_url:
                raise requests.exceptions.RequestException(f"Unexpected URL {url}")
            return _FakeResponse(text=html_by_url[url])

        mock_get.side_effect = fake_get

        all_rows: list[dict[str, Any]] = []
        pages_seen: list[tuple[int, str, str | None]] = []

        def on_page(
            page_number: int,
            page_url: str,
            restaurants_on_page: list[dict[str, Any]],
            next_url: str | None,
            _next_page_number: int,
            _estimated_total_pages: int | None,
            _total_restaurants: int,
        ) -> None:
            pages_seen.append((page_number, page_url, next_url))
            all_rows.extend(restaurants_on_page)

        metrics = crawl(
            start_url=LISTING_PAGE_1_URL,
            on_page=on_page,
            sleep_seconds=0,
            progress_reporter=_NoOpReporter(),
        )

        self.assertEqual(metrics.total_restaurants, 2)
        self.assertEqual(metrics.processed_pages, 2)
        self.assertEqual(len(pages_seen), 2)
        self.assertEqual(pages_seen[0][0], 1)
        self.assertEqual(pages_seen[0][2], LISTING_PAGE_2_URL)
        self.assertEqual(pages_seen[1][0], 2)
        self.assertIsNone(pages_seen[1][2])

        self.assertEqual(all_rows[0]["Name"], "Alpha")
        self.assertEqual(all_rows[0]["Rating"], "1 Star")
        self.assertEqual(all_rows[0]["Cuisine"], "Taiwanese")
        self.assertEqual(all_rows[0]["Restaurant Telephone Number"], "+88620001111")
        self.assertEqual(all_rows[0]["Latitude"], 25.033)
        self.assertEqual(all_rows[0]["Longitude"], 121.565)

        self.assertEqual(all_rows[1]["Name"], "Beta")
        self.assertEqual(all_rows[1]["Rating"], "Bib Gourmand")
        self.assertEqual(all_rows[1]["Price Range"], "$")
        self.assertEqual(all_rows[1]["Cuisine"], "Noodles")

    @patch("michelin_scraper.scraping.fetcher.requests.Session.get")
    def test_crawl_stops_when_max_pages_limit_is_reached(self, mock_get: Any) -> None:
        html_by_url = {
            LISTING_PAGE_1_URL: LISTING_PAGE_1_HTML,
            LISTING_PAGE_2_URL: LISTING_PAGE_2_HTML,
            RESTAURANT_1_URL: RESTAURANT_1_HTML,
            RESTAURANT_2_URL: RESTAURANT_2_HTML,
        }

        def fake_get(*args: Any, **kwargs: Any) -> _FakeResponse:
            if args and isinstance(args[0], str):
                url = args[0]
            elif len(args) > 1 and isinstance(args[1], str):
                url = args[1]
            else:
                url = kwargs.get("url")
            if not isinstance(url, str):
                raise AssertionError("Unable to resolve URL from mocked requests call.")
            if url not in html_by_url:
                raise requests.exceptions.RequestException(f"Unexpected URL {url}")
            return _FakeResponse(text=html_by_url[url])

        mock_get.side_effect = fake_get

        pages_seen: list[int] = []

        def on_page(
            page_number: int,
            _page_url: str,
            _restaurants_on_page: list[dict[str, Any]],
            _next_url: str | None,
            _next_page_number: int,
            _estimated_total_pages: int | None,
            _total_restaurants: int,
        ) -> None:
            pages_seen.append(page_number)

        metrics = crawl(
            start_url=LISTING_PAGE_1_URL,
            on_page=on_page,
            sleep_seconds=0,
            progress_reporter=_NoOpReporter(),
            max_pages=1,
        )

        self.assertEqual(metrics.total_restaurants, 1)
        self.assertEqual(metrics.processed_pages, 1)
        self.assertEqual(pages_seen, [1])

    @patch("michelin_scraper.scraping.fetcher.requests.Session.get")
    def test_crawl_marks_fetch_failure_when_listing_request_fails(self, mock_get: Any) -> None:
        def fake_get(*args: Any, **kwargs: Any) -> _FakeResponse:
            del args, kwargs
            raise requests.exceptions.RequestException("DNS failure")

        mock_get.side_effect = fake_get

        seen_pages: list[int] = []

        def on_page(
            page_number: int,
            _page_url: str,
            _restaurants_on_page: list[dict[str, Any]],
            _next_url: str | None,
            _next_page_number: int,
            _estimated_total_pages: int | None,
            _total_restaurants: int,
        ) -> None:
            seen_pages.append(page_number)

        metrics = crawl(
            start_url=LISTING_PAGE_1_URL,
            on_page=on_page,
            sleep_seconds=0,
            progress_reporter=_NoOpReporter(),
        )

        self.assertEqual(metrics.total_restaurants, 0)
        self.assertEqual(metrics.processed_pages, 0)
        self.assertEqual(metrics.fetch_failures, 1)
        self.assertEqual(seen_pages, [])


if __name__ == "__main__":
    unittest.main()
