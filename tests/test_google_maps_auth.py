"""Auth detection against recorded, de-identified Google Maps controls."""

import asyncio
import shutil
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

from michelin_scraper.adapters.google_maps_driver import GoogleMapsDriver, GoogleMapsDriverConfig
from michelin_scraper.adapters.google_maps_driver_selectors import SIGN_IN_BUTTON_SELECTORS

_FIXTURES = Path(__file__).parent / "fixtures" / "google_maps"


class GoogleMapsAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        runtime = await async_playwright().start()
        self.addAsyncCleanup(runtime.stop)
        browser = await runtime.chromium.launch(headless=True, channel="chrome" if shutil.which("google-chrome") else None)
        self.addAsyncCleanup(browser.close)
        context = await browser.new_context()
        self.addAsyncCleanup(context.close)
        await context.route("**/*", lambda route: route.abort())
        self.page = await context.new_page()
        self.driver = GoogleMapsDriver(GoogleMapsDriverConfig(
            user_data_dir=Path("/unused-profile"), headless=True, sync_delay_seconds=0,
            google_cdp_url="http://127.0.0.1:9222",
        ))
        self.driver._context = context
        self.driver._page = self.page

    async def test_signed_in_account_menu_is_not_a_sign_in_prompt(self) -> None:
        await self.page.set_content((_FIXTURES / "auth-signed-in-2026-11-07.html").read_text())
        self.assertEqual(await self.page.locator('a[href*="accounts.google.com/SignOutOptions"]').count(), 1)
        for selector in SIGN_IN_BUTTON_SELECTORS:
            self.assertEqual(await self.page.locator(selector).count(), 0)
        with patch.object(self.driver, "_has_google_session_cookie", new=AsyncMock()) as cookies:
            self.assertTrue(await self.driver.is_authenticated(refresh=False))
            cookies.assert_not_called()

    async def test_loading_snapshot_waits_for_the_recorded_account_control(self) -> None:
        await self.page.set_content((_FIXTURES / "auth-loading-2026-11-07.html").read_text())
        signed_in_html = (_FIXTURES / "auth-signed-in-2026-11-07.html").read_text()

        async def render_account() -> None:
            await asyncio.sleep(0.15)
            await self.page.set_content(signed_in_html)

        task = asyncio.create_task(render_account())
        try:
            with patch.object(self.driver, "_has_google_session_cookie", new=AsyncMock()) as cookies:
                self.assertTrue(await self.driver.is_authenticated(refresh=False))
                cookies.assert_not_called()
        finally:
            await task


if __name__ == "__main__":
    unittest.main()
