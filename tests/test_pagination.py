"""Tests for Michelin listing pagination helpers."""

import unittest

from bs4 import BeautifulSoup

from michelin_scraper.scraping.pagination import extract_next_page_url, extract_page_number


class PaginationTests(unittest.TestCase):
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
