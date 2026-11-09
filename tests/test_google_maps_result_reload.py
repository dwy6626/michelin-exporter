"""Recover a stalled search using only its recorded physical result URL."""

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from bs4 import BeautifulSoup

from michelin_scraper.adapters.google_maps_driver import GoogleMapsDriver, GoogleMapsDriverConfig

FIXTURES = Path(__file__).parent / "fixtures/google_maps"


class ResultReloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_result_reloads_only_the_recorded_physical_place(self) -> None:
        stalled = BeautifulSoup((FIXTURES / "stalled-place-result-2026-11-08.html").read_text(), "html.parser")
        loaded = BeautifulSoup((FIXTURES / "reloaded-place-result-2026-11-08.html").read_text(), "html.parser")
        result_url = str(stalled.select_one("a.hfpxzc")["href"])
        title = loaded.select_one("h1.DUwDvf").get_text(strip=True)
        save = loaded.select_one('button[aria-label="Save"]')
        self.assertIsNotNone(save)
        page = MagicMock()
        page.url = "https://www.google.com/maps/search/魏家婆菜/"

        async def navigate(url: str, *, wait_until: str) -> None:
            self.assertEqual(wait_until, "commit")
            page.url = url

        page.goto = AsyncMock(side_effect=navigate)
        result = MagicMock()
        result.count = AsyncMock(return_value=1)
        result.get_attribute = AsyncMock(return_value=result_url)
        driver = GoogleMapsDriver(GoogleMapsDriverConfig(
            user_data_dir=Path("/unused-profile"), headless=True, sync_delay_seconds=0.1,
        ))
        waits = 0

        async def wait(**kwargs: object) -> bool:
            nonlocal waits
            waits += 1
            if waits == 1:
                return False
            return await kwargs["predicate"]()

        with (patch.object(driver, "_matching_locator_at", AsyncMock(return_value=result)),
              patch.object(driver, "_supports_dom_waits", return_value=True),
              patch.object(driver, "_extract_text", AsyncMock(side_effect=["", title])),
              patch.object(driver, "_resolve_save_button", AsyncMock(return_value=save)),
              patch.object(driver, "_click_locator", AsyncMock()),
              patch.object(driver, "_wait_for_timeout", AsyncMock()),
              patch.object(driver, "_wait_for_condition", side_effect=wait)):
            self.assertTrue(await driver._try_open_result_by_index(page=page, query="魏家婆菜", result_index=0))
        page.goto.assert_awaited_once_with(result_url, wait_until="commit")

