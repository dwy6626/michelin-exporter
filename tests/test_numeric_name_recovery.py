"""A shared restaurant number and city must not identify a different business."""

import json
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from michelin_scraper.adapters.google_maps_driver_selectors import PLACE_TITLE_SELECTORS
from michelin_scraper.adapters.google_maps_sync_writer import GoogleMapsRowSyncFailFastError, GoogleMapsSyncWriter
from michelin_scraper.application.place_matcher import PlaceCandidate, assess_place_match
from michelin_scraper.application.place_query_builder import build_place_query_attempts
from tests.fakes import FakeDriver

_FIXTURES = Path(__file__).parent / "fixtures"


class NumericNameRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_street_numeric_restaurant_candidate_stays_rejected_by_writer(self) -> None:
        payload = json.loads((_FIXTURES / "place_matcher/restaurant12-2026-11-08.json").read_text())
        soup = BeautifulSoup(
            (_FIXTURES / "google_maps/restaurant12-wrong-kitchen12-2026-11-08.html").read_text(), "html.parser"
        )
        self.assertTrue(any(
            element.get_text(strip=True) == payload["candidate"]["name"]
            for selector in PLACE_TITLE_SELECTORS for element in soup.select(selector)
        ))
        row, candidate = payload["source_row"], PlaceCandidate(**payload["candidate"])
        self.assertEqual(assess_place_match(row, candidate).strength, "weak")
        driver = FakeDriver()
        driver.candidates_by_query = {query: candidate for query in build_place_query_attempts(row)}
        writer = GoogleMapsSyncWriter(
            user_data_dir=Path("/unused-profile"), headless=True, sync_delay_seconds=0, max_save_retries=0,
            list_name_template="{scope} 米其林 {level_badge}", list_name_prefix="", on_missing_list="stop",
            ignore_existing_lists_check=True, probe_only=True, language="zh_TW", driver=driver,
        )
        await writer.initialize_run(scope_name="臺灣", level_slugs=("stars",))
        with self.assertRaisesRegex(GoogleMapsRowSyncFailFastError, "AmbiguousPlaceMatch"):
            await writer.sync_row("stars", row)
        self.assertEqual(driver.save_calls, [])
