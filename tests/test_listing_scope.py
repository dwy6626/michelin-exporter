"""Tests for listing scope-name extraction helpers."""

import unittest

from bs4 import BeautifulSoup

from michelin_scraper.scraping.listing_scope import extract_scope_name_from_listing_soup


class ListingScopeTests(unittest.TestCase):
    def test_extract_scope_name_prefers_breadcrumb_candidate(self) -> None:
        html = """
        <html>
          <body>
            <nav aria-label="Breadcrumb">
              <ol>
                <li><a href="/country">Country</a></li>
                <li><span>Taipei Region</span></li>
              </ol>
            </nav>
            <h1>Long Heading Value</h1>
          </body>
        </html>
        """.strip()
        soup = BeautifulSoup(html, "html.parser")

        resolved_scope_name = extract_scope_name_from_listing_soup(soup)

        self.assertEqual(resolved_scope_name, "Taipei Region")

    def test_extract_scope_name_falls_back_to_heading(self) -> None:
        html = """
        <html>
          <body>
            <main>
              <h1>Tokyo Region</h1>
            </main>
          </body>
        </html>
        """.strip()
        soup = BeautifulSoup(html, "html.parser")

        resolved_scope_name = extract_scope_name_from_listing_soup(soup)

        self.assertEqual(resolved_scope_name, "Tokyo Region")

    def test_extract_scope_name_uses_meta_or_title_as_last_resort(self) -> None:
        html = """
        <html>
          <head>
            <meta property="og:title" content="Seoul Region | MICHELIN Guide" />
            <title>Ignored Title</title>
          </head>
          <body></body>
        </html>
        """.strip()
        soup = BeautifulSoup(html, "html.parser")

        resolved_scope_name = extract_scope_name_from_listing_soup(soup)

        self.assertEqual(resolved_scope_name, "Seoul Region")

    def test_extract_scope_name_returns_empty_when_page_has_no_candidates(self) -> None:
        html = "<html><body><div>No scope markers</div></body></html>"
        soup = BeautifulSoup(html, "html.parser")

        resolved_scope_name = extract_scope_name_from_listing_soup(soup)

        self.assertEqual(resolved_scope_name, "")

    def test_extract_scope_name_strips_emoji_from_candidates(self) -> None:
        html = """
        <html>
          <body>
            <h1>\u9644\u8fd1\u7684\u9910\u5ef3 \u7c73\u5176\u6797 \U0001f60b</h1>
          </body>
        </html>
        """.strip()
        soup = BeautifulSoup(html, "html.parser")

        resolved_scope_name = extract_scope_name_from_listing_soup(soup)

        self.assertEqual(resolved_scope_name, "\u9644\u8fd1\u7684\u9910\u5ef3 \u7c73\u5176\u6797")

    def test_extract_scope_name_returns_empty_when_only_emoji(self) -> None:
        html = """
        <html>
          <body>
            <h1>\U0001f60b\U0001f37d\ufe0f</h1>
          </body>
        </html>
        """.strip()
        soup = BeautifulSoup(html, "html.parser")

        resolved_scope_name = extract_scope_name_from_listing_soup(soup)

        self.assertEqual(resolved_scope_name, "")

    def test_extract_scope_name_truncates_overly_long_candidates(self) -> None:
        html = """
        <html>
          <body>
            <h1>Very Long Scope Name That Should Be Trimmed For List Stability</h1>
          </body>
        </html>
        """.strip()
        soup = BeautifulSoup(html, "html.parser")

        resolved_scope_name = extract_scope_name_from_listing_soup(soup)

        self.assertEqual(resolved_scope_name, "Very Long Scope Name That")


if __name__ == "__main__":
    unittest.main()
