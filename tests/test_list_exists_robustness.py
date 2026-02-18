"""Tests for Google Maps driver list_exists robustness."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from michelin_scraper.adapters.google_maps_driver import (
    GoogleMapsDriver,
    GoogleMapsDriverConfig,
)


class ListExistsRobustnessTests(unittest.IsolatedAsyncioTestCase):
    def _build_driver(self) -> GoogleMapsDriver:
        return GoogleMapsDriver(
            GoogleMapsDriverConfig(
                user_data_dir=Path("/tmp/michelin-list-exists-test"),
                headless=True,
                sync_delay_seconds=0,
            )
        )

    async def test_list_exists_finds_list_after_view_more(self) -> None:
        driver = self._build_driver()
        page = MagicMock()

        # Mock _require_page to return our mock page
        with patch.object(driver, "_require_page", return_value=page):
            # Mock high-level flow methods
            with (
                patch.object(driver, "_open_saved_tab") as mock_open_tab,
                patch.object(driver, "_ensure_lists_view") as mock_ensure_view,
                patch.object(driver, "_wait_until_list_entry_available") as mock_wait_available,
                patch.object(driver, "_open_saved_view_more") as mock_view_more,
                patch.object(driver, "_find_list_entry_locator"),
                patch.object(driver, "_wait_for_timeout")
            ):
                # First attempt: not found
                # Second attempt (after view more): found
                mock_wait_available.side_effect = [None, MagicMock()]
                mock_view_more.return_value = True

                exists = await driver.list_exists("Target List")

                self.assertTrue(exists)
                mock_open_tab.assert_called_once()
                mock_ensure_view.assert_called_once_with(page)
                self.assertEqual(mock_wait_available.call_count, 2)
                mock_view_more.assert_called_once_with(page)

    async def test_list_exists_returns_false_if_not_found_even_after_view_more(self) -> None:
        driver = self._build_driver()
        page = MagicMock()

        with patch.object(driver, "_require_page", return_value=page):
            with (
                patch.object(driver, "_open_saved_tab"),
                patch.object(driver, "_ensure_lists_view"),
                patch.object(driver, "_wait_until_list_entry_available", return_value=None),
                patch.object(driver, "_open_saved_view_more", return_value=True),
                patch.object(driver, "_wait_for_timeout")
            ):
                exists = await driver.list_exists("Non-existent List")

                self.assertFalse(exists)

    async def test_list_exists_returns_true_if_found_immediately(self) -> None:
        driver = self._build_driver()
        page = MagicMock()

        with patch.object(driver, "_require_page", return_value=page):
            with (
                patch.object(driver, "_open_saved_tab"),
                patch.object(driver, "_ensure_lists_view"),
                patch.object(driver, "_wait_until_list_entry_available", return_value=MagicMock()),
                patch.object(driver, "_open_saved_view_more") as mock_view_more
            ):
                exists = await driver.list_exists("Existing List")

                self.assertTrue(exists)
                mock_view_more.assert_not_called()

if __name__ == "__main__":
    unittest.main()
