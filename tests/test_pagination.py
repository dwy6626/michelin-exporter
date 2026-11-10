"""Tests for Michelin listing pagination helpers."""

import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from michelin_scraper.scraping import crawl
from michelin_scraper.scraping.models import NullProgressReporter, ScrapeResultsPage
from michelin_scraper.scraping.pagination import extract_next_page_url, extract_page_number, extract_total_pages


class PaginationTests(unittest.TestCase):
    def test_recorded_next_link_continues_beyond_visible_page_estimate(self) -> None:
        soup = BeautifulSoup(
            (Path(__file__).parent / "fixtures/michelin/taiwan-page9-pagination-2026-11-08.html").read_text(),
            "html.parser",
        )
        page9 = "https://guide.michelin.com/tw/zh_TW/selection/taiwan-region/restaurants/page/9"
        next_url = extract_next_page_url(soup, page9)
        self.assertEqual(extract_total_pages(soup), 9)
        self.assertEqual(next_url, page9.removesuffix("9") + "10")
        with patch("michelin_scraper.scraping.engine.scrape_results_single_page", side_effect=[
            ScrapeResultsPage([], next_url, 9, 434, False), ScrapeResultsPage([], None, 10, 434, False),
        ]) as scrape:
            metrics = crawl(
                start_url=page9, sleep_seconds=0, start_page_number=9, start_estimated_total_pages=9,
                progress_reporter=NullProgressReporter(), on_page=None,
            )
        self.assertEqual(metrics.processed_pages, 2)
        self.assertEqual([call.kwargs["url"] for call in scrape.call_args_list], [page9, next_url])

    def test_extract_page_number_from_path_segment(self) -> None:
        page_number = extract_page_number(
            "https://guide.michelin.com/en/tw/taipei-region/taipei/restaurants/page/2",
            fallback=1,
        )

        self.assertEqual(page_number, 2)

    def test_extract_next_page_url_from_modern_arrow_image_pagination(self) -> None:
        soup = BeautifulSoup(
            """
            <ul class="pagination">
              <li><a class="btn active" aria-current="page" href="/restaurants/page/1">1</a></li>
              <li><a class="btn" href="/restaurants/page/2">2</a></li>
              <li>
                <a class="btn" href="/restaurants/page/2">
                  <img class="icon" src="/assets/images/icons/icons8-arrow-right-30.png" />
                </a>
              </li>
            </ul>
            """,
            "html.parser",
        )

        next_url = extract_next_page_url(
            soup,
            "https://guide.michelin.com/en/tw/taipei-region/taipei/restaurants/page/1",
        )

        self.assertEqual(
            next_url,
            "https://guide.michelin.com/restaurants/page/2",
        )


if __name__ == "__main__":
    unittest.main()
