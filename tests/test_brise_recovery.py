"""Recorded Brisé detail readiness and owned source-browser cleanup regressions."""

import json
import unittest
from pathlib import Path
from queue import ShutDown
from threading import Event, Timer
from unittest.mock import MagicMock, Mock, patch

from bs4 import BeautifulSoup

from michelin_scraper.adapters.google_maps_sync_writer import GoogleMapsSyncWriter
from michelin_scraper.application.place_matcher import PlaceCandidate
from michelin_scraper.application.place_query_builder import build_place_query_attempts
from michelin_scraper.application.row_identity import build_row_identity_key
from michelin_scraper.application.sync_enums import SyncRowStatus
from michelin_scraper.scraping.fetcher import FetchPageResult, _BrowserHtmlFetcher, _fetch_html_with_browser, fetch_page_soup
from michelin_scraper.scraping.listing_page import _build_restaurant_row_from_card, scrape_results_single_page
from michelin_scraper.scraping.models import NullProgressReporter
from michelin_scraper.scraping.restaurant_details import scrape_restaurant_page
from tests.fakes import FakeDriver

_FIXTURES = Path(__file__).parent / "fixtures" / "michelin"
_URL = "https://guide.michelin.com/tw/zh_TW/taipei-region/taipei/restaurant/brise"


class BriseRecoveryTests(unittest.TestCase):
    def test_incomplete_rendered_detail_is_a_fetch_failure(self) -> None:
        incomplete = (_FIXTURES / "brise-incomplete-2026-11-07.html").read_text()
        session = MagicMock()
        response = session.get.return_value
        response.status_code, response.text, response.headers = 202, incomplete, {"x-amzn-waf-action": "challenge"}
        with patch("michelin_scraper.scraping.fetcher._fetch_html_with_browser", return_value=incomplete) as browser:
            result = fetch_page_soup(
                session=session, url=_URL, headers={}, tls_verify=True, progress_reporter=NullProgressReporter(),
                page_type="restaurant", required_selector="div.data-sheet__block--text",
            )
        self.assertTrue(result.fetch_failed)
        self.assertIsNone(result.soup)
        browser.assert_called_once()

    def test_stopped_listing_cancels_unstarted_detail_jobs(self) -> None:
        soup = BeautifulSoup((_FIXTURES / "taiwan-region-2026-11-07.html").read_text(), "html.parser")
        first_name = soup.select_one('.js-restaurant__list_items h3').get_text(strip=True)
        release = Event()
        started: list[str] = []
        timers: list[Timer] = []

        def build(*, restaurant_name, **kwargs):
            started.append(restaurant_name)
            if restaurant_name != first_name:
                release.wait(timeout=1)
            return {"Name": restaurant_name}

        def stop(*args):
            timer = Timer(0.03, release.set)
            timers.append(timer)
            timer.start()
            raise ShutDown

        try:
            with (
                patch("michelin_scraper.scraping.listing_page.fetch_page_soup", return_value=FetchPageResult(soup, False)),
                patch("michelin_scraper.scraping.listing_page._build_restaurant_row_from_card", side_effect=build),
                self.assertRaises(ShutDown),
            ):
                scrape_results_single_page(
                    session=MagicMock(), url="https://guide.michelin.com/tw/zh_TW/selection/taiwan-region/restaurants",
                    headers={}, tls_verify=True, page_number=1, estimated_total_pages=9, total_restaurants_so_far=0,
                    progress_reporter=NullProgressReporter(), on_item=stop,
                )
        finally:
            release.set()
            for timer in timers:
                timer.join(timeout=1)
        self.assertLess(len(started), 48)

    def test_detail_waits_for_recorded_address_before_parsing(self) -> None:
        incomplete = (_FIXTURES / "brise-incomplete-2026-11-07.html").read_text()
        complete = (_FIXTURES / "brise-detail-2026-11-07.html").read_text()
        self.assertIsNone(BeautifulSoup(incomplete, "html.parser").select_one('.data-sheet__block--text'))
        for headers in ({"x-amzn-waf-action": "challenge"}, {}):
            with self.subTest(headers=headers), patch("playwright.sync_api.sync_playwright") as start:
                runtime = start.return_value.start.return_value
                page = runtime.chromium.launch.return_value.new_context.return_value.new_page.return_value
                page.content.side_effect = lambda page=page: complete if page.wait_for_selector.called else incomplete
                session = MagicMock()
                response = session.get.return_value
                response.status_code, response.text, response.headers = 202, incomplete, headers
                data = scrape_restaurant_page(
                    session=session, url=_URL, headers={}, tls_verify=True, progress_reporter=NullProgressReporter(),
                )
                self.assertIn("中山區建國北路一段92巷8號", data["Address"])
                runtime.stop.assert_called_once()

    def test_browser_fetch_wrapper_closes_owned_runtime(self) -> None:
        with (
            patch.object(_BrowserHtmlFetcher, "fetch", return_value="<html></html>"),
            patch.object(_BrowserHtmlFetcher, "close") as close,
        ):
            _fetch_html_with_browser(url=_URL, headers={})
            close.assert_called_once()

    def test_browser_close_error_still_stops_playwright(self) -> None:
        fetcher = _BrowserHtmlFetcher()
        runtime, browser = Mock(), Mock()
        fetcher._playwright, fetcher._browser = runtime, browser
        browser.close.side_effect = RuntimeError("owned browser close failed")
        with self.assertRaisesRegex(RuntimeError, "owned browser close failed"):
            fetcher.close()
        runtime.stop.assert_called_once()


class BriseMatchingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_restored_source_address_resolves_real_candidate_without_accepting_addressless_row(self) -> None:
        payload = json.loads((Path(__file__).parent / "fixtures/place_matcher/brise-2026-11-07.json").read_text())
        card = BeautifulSoup((_FIXTURES / "brise-listing-card-2026-11-07.html").read_text(), "html.parser").select_one('div.card__menu')
        session = MagicMock()
        response = session.get.return_value
        response.status_code, response.headers = 200, {}
        response.text = (_FIXTURES / "brise-detail-2026-11-07.html").read_text()
        row = _build_restaurant_row_from_card(
            session=session, listing_card=card, restaurant_name="君尹", headers={}, tls_verify=True,
            progress_reporter=NullProgressReporter(), local_language="zh_TW", local_country_code="tw", requested_language="zh_TW",
        )
        self.assertEqual(
            build_row_identity_key("selected", row), build_row_identity_key("selected", payload["source_row"])
        )
        self.assertEqual(row["Rating"], "Selected")
        candidate = PlaceCandidate(**payload["maps_candidate"])
        for has_address in (False, True):
            with self.subTest(has_address=has_address):
                test_row = dict(row)
                if not has_address:
                    test_row["Address"] = ""
                driver = FakeDriver()
                driver.candidates_by_query = {query: candidate for query in build_place_query_attempts(test_row)}
                writer = GoogleMapsSyncWriter(
                    user_data_dir=Path("/unused-profile"), headless=True, sync_delay_seconds=0, max_save_retries=0,
                    list_name_template="{scope} 米其林 {level_badge}", list_name_prefix="", on_missing_list="continue",
                    ignore_existing_lists_check=True, probe_only=True, language="zh_TW", driver=driver,
                )
                await writer.initialize_run(scope_name="臺灣", level_slugs=("selected",))
                result = await writer.sync_row("selected", test_row)
                if has_address:
                    self.assertEqual(result.status, SyncRowStatus.SKIPPED)
                    self.assertIsNone(result.failure)
                else:
                    self.assertEqual(result.status, SyncRowStatus.FAILED)
                    assert result.failure is not None
                    self.assertEqual(result.failure.reason, "AmbiguousPlaceMatch")
                self.assertEqual(driver.save_calls, [])


if __name__ == "__main__":
    unittest.main()
