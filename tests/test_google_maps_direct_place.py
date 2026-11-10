"""Recorded direct-place panels must not navigate into nearby businesses."""

import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from michelin_scraper.adapters.google_maps_driver import GoogleMapsDriver, GoogleMapsDriverConfig
from michelin_scraper.application.place_matcher import PlaceCandidate, assess_place_match

_FIXTURES = Path(__file__).parent / "fixtures"


class GoogleMapsDirectPlaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_place_is_not_replaced_by_global_nearby_result_link(self) -> None:
        soup = BeautifulSoup(
            (_FIXTURES / "google_maps/direct-place-with-nearby-links-2026-11-08.html").read_text(), "html.parser"
        )
        self.assertEqual(soup.select_one("h1.DUwDvf").get_text(strip=True), "葉家藥燉排骨")
        self.assertIsNone(soup.select_one("[role='feed']"))
        self.assertTrue(any(soup.select(selector) for selector in GoogleMapsDriver._FIRST_RESULT_SELECTORS))
        for script in soup.select("script"):
            script.decompose()
        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch(headless=True, channel="chrome" if shutil.which("google-chrome") else None)
            try:
                context = await browser.new_context()
                await context.route("**/*", lambda route: (
                    route.fulfill(body=str(soup), content_type="text/html")
                    if route.request.resource_type == "document" else route.abort()
                ))
                page = await context.new_page()
                metadata = json.loads(
                    (_FIXTURES / "google_maps/direct-place-with-nearby-links-2026-11-08.metadata.json").read_text()
                )
                await page.goto(metadata["source_url"], wait_until="domcontentloaded")
                driver = GoogleMapsDriver(GoogleMapsDriverConfig(
                    user_data_dir=Path("/unused-profile"), headless=True, sync_delay_seconds=0,
                ))
                with (
                    patch.object(driver, "_click_locator", new_callable=AsyncMock) as click,
                    patch.object(driver, "_resolve_save_button", new_callable=AsyncMock, return_value=object()),
                ):
                    opened = await driver._try_open_result_by_index(page=page, query="葉家藥燉排骨 板橋區", result_index=0)
                self.assertFalse(opened)
                click.assert_not_awaited()
            finally:
                await browser.close()

    def test_recorded_tourist_attraction_remains_a_non_food_veto(self) -> None:
        payload = json.loads((_FIXTURES / "place_matcher/monsoon-2026-11-08.json").read_text())
        assessment = assess_place_match(payload["source_row"], PlaceCandidate(**payload["candidate"]))
        self.assertTrue(assessment.hard_veto)
        self.assertEqual(assessment.strength, "weak")

    def test_unrecognized_category_needs_strong_identity_from_recorded_campground(self) -> None:
        payload = json.loads((_FIXTURES / "place_matcher/monsoon-campground-2026-11-08.json").read_text())
        assessment = assess_place_match(payload["source_row"], PlaceCandidate(**payload["candidate"]))
        self.assertTrue(assessment.hard_veto)
        self.assertEqual(assessment.strength, "weak")

    def test_recorded_sushi_shop_remains_food_service_with_translated_name(self) -> None:
        payload = json.loads((_FIXTURES / "place_matcher/nomura-sushi-2026-11-08.json").read_text())
        assessment = assess_place_match(payload["source_row"], PlaceCandidate(**payload["candidate"]))
        self.assertFalse(assessment.hard_veto)
        self.assertIn(assessment.strength, ("medium", "strong"))
