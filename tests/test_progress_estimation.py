import unittest

from bs4 import BeautifulSoup

from michelin_scraper.scraping.listing_page import estimate_progress
from michelin_scraper.scraping.pagination import extract_total_items


class TestProgressEstimation(unittest.TestCase):
    def test_extract_total_items(self):
        html = """
        <h1 class="flex-fill">
            1-20 of 3,113 Restaurants
        </h1>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(extract_total_items(soup), 3113)

    def test_extract_total_items_no_header(self):
        soup = BeautifulSoup("<div></div>", "html.parser")
        self.assertIsNone(extract_total_items(soup))

    def test_extract_total_items_different_format(self):
        html = '<h1 class="flex-fill">1-48 of 18,802 Restaurants</h1>'
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(extract_total_items(soup), 18802)

    def test_extract_total_items_traditional_chinese_range(self):
        html = '<h1 class="flex-fill">日本: 1-48 共 1,106 個餐廳</h1>'
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(extract_total_items(soup), 1106)

    def test_estimate_progress_with_total_restaurants(self):
        # total_restaurants_expected = 100
        # total_restaurants_so_far = 20
        # scraped_rows_count = 5
        # card_index = 10
        # cards_total = 20
        # current_absolute_index = 20 + 5 + (10/20) = 25.5
        # progress = 25.5 / 100 = 0.255
        progress = estimate_progress(
            current_page=2,
            card_index=10,
            cards_total=20,
            total_pages=5,
            total_restaurants_expected=100,
            total_restaurants_so_far=20,
            scraped_rows_count=5
        )
        self.assertAlmostEqual(progress, 0.255)

    def test_estimate_progress_fallback_to_pages(self):
        # current_page = 2
        # card_index = 10
        # cards_total = 20
        # total_pages = 5
        # progress = ((2-1) + 10/20) / 5 = 1.5 / 5 = 0.3
        progress = estimate_progress(
            current_page=2,
            card_index=10,
            cards_total=20,
            total_pages=5,
            total_restaurants_expected=None
        )
        self.assertAlmostEqual(progress, 0.3)

if __name__ == "__main__":
    unittest.main()
