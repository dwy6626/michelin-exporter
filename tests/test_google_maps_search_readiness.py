"""Offline browser integration for recorded Maps search controls."""

import shutil
import unittest
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.async_api import Error, async_playwright

from michelin_scraper.adapters.google_maps_driver import GoogleMapsDriver, GoogleMapsDriverConfig

_FIXTURE = Path(__file__).parent / "fixtures" / "google_maps" / "search-readiness-2026-11-07.html"


class GoogleMapsSearchReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        runtime = await async_playwright().start()
        self.addAsyncCleanup(runtime.stop)
        browser = await runtime.chromium.launch(
            headless=True,
            channel="chrome" if shutil.which("google-chrome") else None,
        )
        self.addAsyncCleanup(browser.close)
        context = await browser.new_context()
        self.addAsyncCleanup(context.close)
        await context.route("**/*", lambda route: route.abort())
        self.page = await context.new_page()
        soup = BeautifulSoup(_FIXTURE.read_text(encoding="utf-8"), "html.parser")
        search_surface = soup.select_one('[role="search"]')
        self.assertIsNotNone(search_surface)
        # Render the recorded subtree without executing scripts from the full snapshot.
        await self.page.set_content(str(search_surface))
        self.driver = GoogleMapsDriver(GoogleMapsDriverConfig(
            user_data_dir=Path("/unused-profile"),
            headless=True,
            sync_delay_seconds=0,
        ))

    async def _assert_recorded_input_can_be_filled(self) -> None:
        locator = await self.driver._resolve_search_box_locator(self.page)
        self.assertIsNotNone(locator)
        assert locator is not None
        self.assertEqual(await locator.get_attribute("name"), "q")
        self.assertEqual(await locator.get_attribute("role"), "combobox")
        await locator.fill("Taiwan")
        self.assertEqual(await locator.input_value(), "Taiwan")

    async def test_already_ready_recorded_search_control_can_be_filled(self) -> None:
        await self._assert_recorded_input_can_be_filled()

    async def test_recorded_combobox_is_accessible_by_its_search_label(self) -> None:
        locator = self.page.get_by_role("combobox", name="Search Google Maps")
        self.assertEqual(await locator.count(), 1)
        self.assertTrue(await locator.is_visible())

    async def test_absent_recorded_input_returns_none(self) -> None:
        await self.page.locator('input[name="q"][role="combobox"]').evaluate("input => input.remove()")
        self.assertIsNone(await self.driver._resolve_search_box_locator(self.page))

    async def test_closed_page_errors_propagate_instead_of_becoming_missing_input(self) -> None:
        await self.page.close()
        with self.assertRaises(Error):
            await self.driver._resolve_search_box_locator(self.page)


if __name__ == "__main__":
    unittest.main()
