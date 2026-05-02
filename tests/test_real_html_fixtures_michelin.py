"""Offline tests using de-identified real Michelin HTML fixtures."""

import json
import unittest
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from michelin_scraper.application.row_router import LevelRowRouter
from michelin_scraper.catalog.levels import build_rating_to_output_level_slug_map
from michelin_scraper.catalog.targets import normalize_target, resolve_target
from michelin_scraper.scraping.listing_page import scrape_results_single_page
from michelin_scraper.scraping.listing_scope import extract_scope_name_from_listing_soup

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "michelin"
_BASE_URL = "https://guide.michelin.com"
_LISTING_URL = f"{_BASE_URL}/en/jp/tokyo-region/tokyo/restaurants"
_DETAIL_ALPHA_URL = f"{_BASE_URL}/en/jp/tokyo-region/tokyo/restaurant/alpha"
_DETAIL_BETA_URL = f"{_BASE_URL}/en/jp/tokyo-region/tokyo/restaurant/beta"
_LISTING_ZH_TW_URL = f"{_BASE_URL}/tw/zh_TW/selection/taiwan/restaurants"
_DETAIL_GAMMA_URL = f"{_BASE_URL}/tw/zh_TW/taipei-region/restaurant/gamma"
_DETAIL_DELTA_URL = f"{_BASE_URL}/tw/zh_TW/taichung-region/restaurant/delta"


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP status {self.status_code}")


class _FixtureSession:
    def __init__(self, html_by_url: dict[str, str]) -> None:
        self._html_by_url = html_by_url

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: int,
        verify: bool | str,
    ) -> _FakeResponse:
        del headers, timeout, verify
        if url not in self._html_by_url:
            raise requests.exceptions.RequestException(f"Missing fixture for URL: {url}")
        return _FakeResponse(self._html_by_url[url])


class _NoOpReporter:
    def update(self, message: str, progress: float | None = None) -> None:
        del message, progress

    def log(self, message: str) -> None:
        del message

    def finish(self, message: str | None = None) -> None:
        del message


def _read_fixture_text(filename: str) -> str:
    return (_FIXTURE_DIR / filename).read_text(encoding="utf-8")


def _read_fixture_metadata(filename: str) -> dict[str, Any]:
    return json.loads(_read_fixture_text(filename))


class RealHtmlMichelinFixtureTests(unittest.TestCase):
    def test_fixture_metadata_marks_snapshots_as_sanitized(self) -> None:
        metadata_files = sorted(_FIXTURE_DIR.glob("*.metadata.json"))
        self.assertGreater(len(metadata_files), 0)
        for metadata_file in metadata_files:
            payload = json.loads(metadata_file.read_text(encoding="utf-8"))
            self.assertTrue(payload.get("sanitized"), f"Fixture metadata not sanitized: {metadata_file.name}")
            self.assertEqual(payload.get("source"), "real-page-snapshot")

    def test_listing_fixture_parses_rows_and_pagination(self) -> None:
        html_by_url = {
            _LISTING_URL: _read_fixture_text("listing-en.html"),
            _DETAIL_ALPHA_URL: _read_fixture_text("detail-alpha.html"),
            _DETAIL_BETA_URL: _read_fixture_text("detail-alpha.html"),
        }
        page_result = scrape_results_single_page(
            session=_FixtureSession(html_by_url),  # type: ignore[arg-type]
            url=_LISTING_URL,
            headers={},
            tls_verify=True,
            page_number=1,
            estimated_total_pages=None,
            total_restaurants_so_far=0,
            progress_reporter=_NoOpReporter(),
            item_sleep_seconds=0.0,
        )

        self.assertFalse(page_result.fetch_failed)
        self.assertEqual(len(page_result.restaurant_rows), 2)
        self.assertEqual(
            page_result.next_url,
            "https://guide.michelin.com/en/jp/tokyo-region/tokyo/restaurants?page=2",
        )
        self.assertEqual(page_result.restaurant_rows[0]["Name"], "Alpha")
        self.assertEqual(page_result.restaurant_rows[0]["Rating"], "2 Stars")
        self.assertEqual(page_result.restaurant_rows[0]["GuideYear"], "2025")
        self.assertEqual(page_result.restaurant_rows[1]["Rating"], "Bib Gourmand")
        self.assertEqual(page_result.restaurant_rows[1]["GuideYear"], "2025")

    def test_listing_fixture_routes_real_rows_into_default_level_buckets(self) -> None:
        html_by_url = {
            _LISTING_URL: _read_fixture_text("listing-en.html"),
            _DETAIL_ALPHA_URL: _read_fixture_text("detail-alpha.html"),
            _DETAIL_BETA_URL: _read_fixture_text("detail-alpha.html"),
        }
        page_result = scrape_results_single_page(
            session=_FixtureSession(html_by_url),  # type: ignore[arg-type]
            url=_LISTING_URL,
            headers={},
            tls_verify=True,
            page_number=1,
            estimated_total_pages=None,
            total_restaurants_so_far=0,
            progress_reporter=_NoOpReporter(),
            item_sleep_seconds=0.0,
        )
        router = LevelRowRouter(
            level_slugs=("stars", "selected", "bib-gourmand"),
            rating_to_level_slug=build_rating_to_output_level_slug_map(
                ("stars", "selected", "bib-gourmand")
            ),
        )

        grouped = router.group_rows_by_level(page_result.restaurant_rows)

        self.assertEqual([row["Name"] for row in grouped["stars"]], ["Alpha"])
        self.assertEqual(grouped["selected"], [])
        self.assertEqual([row["Name"] for row in grouped["bib-gourmand"]], ["Beta"])

    def test_zh_tw_listing_fixture_extracts_local_scope_name(self) -> None:
        soup = BeautifulSoup(_read_fixture_text("listing-zh-tw.html"), "html.parser")
        scope_name = extract_scope_name_from_listing_soup(soup)
        self.assertEqual(scope_name, "臺北餐廳")

    def test_zh_tw_listing_fixture_parses_modern_star_icon_rating(self) -> None:
        html_by_url = {
            _LISTING_ZH_TW_URL: _read_fixture_text("listing-zh-tw.html"),
            _DETAIL_GAMMA_URL: _read_fixture_text("detail-alpha.html"),
            _DETAIL_DELTA_URL: _read_fixture_text("detail-alpha.html"),
        }
        page_result = scrape_results_single_page(
            session=_FixtureSession(html_by_url),  # type: ignore[arg-type]
            url=_LISTING_ZH_TW_URL,
            headers={},
            tls_verify=True,
            page_number=1,
            estimated_total_pages=None,
            total_restaurants_so_far=0,
            progress_reporter=_NoOpReporter(),
            item_sleep_seconds=0.0,
        )

        self.assertFalse(page_result.fetch_failed)
        self.assertEqual(len(page_result.restaurant_rows), 2)
        self.assertEqual(page_result.restaurant_rows[0]["Name"], "Gamma")
        self.assertEqual(page_result.restaurant_rows[0]["Rating"], "1 Star")
        self.assertEqual(page_result.restaurant_rows[0]["GuideYear"], "2025")
        self.assertEqual(page_result.restaurant_rows[1]["Name"], "Delta")
        self.assertEqual(page_result.restaurant_rows[1]["Rating"], "Bib Gourmand")
        self.assertEqual(page_result.restaurant_rows[1]["GuideYear"], "2025")

    def test_zh_tw_listing_fixture_routes_modern_bibendum_icon_to_bib_bucket(self) -> None:
        html_by_url = {
            _LISTING_ZH_TW_URL: _read_fixture_text("listing-zh-tw.html"),
            _DETAIL_GAMMA_URL: _read_fixture_text("detail-alpha.html"),
            _DETAIL_DELTA_URL: _read_fixture_text("detail-alpha.html"),
        }
        page_result = scrape_results_single_page(
            session=_FixtureSession(html_by_url),  # type: ignore[arg-type]
            url=_LISTING_ZH_TW_URL,
            headers={},
            tls_verify=True,
            page_number=1,
            estimated_total_pages=None,
            total_restaurants_so_far=0,
            progress_reporter=_NoOpReporter(),
            item_sleep_seconds=0.0,
        )
        router = LevelRowRouter(
            level_slugs=("stars", "selected", "bib-gourmand"),
            rating_to_level_slug=build_rating_to_output_level_slug_map(
                ("stars", "selected", "bib-gourmand")
            ),
        )

        grouped = router.group_rows_by_level(page_result.restaurant_rows)

        self.assertEqual([row["Name"] for row in grouped["stars"]], ["Gamma"])
        self.assertEqual(grouped["selected"], [])
        self.assertEqual([row["Name"] for row in grouped["bib-gourmand"]], ["Delta"])

    def test_target_resolution_keeps_language_specific_path(self) -> None:
        resolved = resolve_target(normalize_target("taipei"), language="zh-tw")
        self.assertIn("/tw/zh_TW/taipei-region/restaurants", resolved.start_url)
        self.assertEqual(resolved.scope_name, "臺北")

    def test_metadata_language_matches_fixture_variant(self) -> None:
        metadata = _read_fixture_metadata("listing-zh-tw.metadata.json")
        self.assertEqual(metadata["language"], "zh-tw")


if __name__ == "__main__":
    unittest.main()
