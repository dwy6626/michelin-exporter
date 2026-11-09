"""Real Maps business branding must remain distinct from address pins."""

import json
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from michelin_scraper.adapters.google_maps_driver_selectors import (
    PLACE_ADDRESS_SELECTORS,
    PLACE_CATEGORY_SELECTORS,
    PLACE_TITLE_SELECTORS,
)
from michelin_scraper.adapters.google_maps_sync_writer import GoogleMapsSyncWriter
from michelin_scraper.application.place_matcher import PlaceCandidate, assess_place_match
from michelin_scraper.application.place_query_builder import build_place_query_attempts
from michelin_scraper.application.sync_enums import SyncRowStatus
from tests.fakes import FakeDriver

_FIXTURES = Path(__file__).parent / "fixtures"


class NanmenRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_branding_exception_requires_real_location_and_food_evidence(self) -> None:
        payload = json.loads((_FIXTURES / "place_matcher/nanmen-2026-11-08.json").read_text())
        for missing_field in ("address", "category"):
            with self.subTest(missing_field=missing_field):
                fields = dict(payload["maps_candidate"])
                fields[missing_field] = ""
                assessment = assess_place_match(payload["source_row"], PlaceCandidate(**fields))
                self.assertTrue(assessment.address_like_candidate_name)
                self.assertTrue(assessment.hard_veto)
                self.assertEqual(assessment.strength, "weak")

    async def test_recorded_business_with_number_branding_passes_production_matching(self) -> None:
        payload = json.loads((_FIXTURES / "place_matcher/nanmen-2026-11-08.json").read_text())
        soup = BeautifulSoup(
            (_FIXTURES / "google_maps/nanmen-branded-number-2026-11-08.html").read_text(), "html.parser"
        )

        def text(selectors):
            for selector in selectors:
                element = soup.select_one(selector)
                if element:
                    return element.get_text(" ", strip=True)
            self.fail(f"Missing recorded candidate field: {selectors}")

        candidate = PlaceCandidate(
            name=text(PLACE_TITLE_SELECTORS), address=text(PLACE_ADDRESS_SELECTORS),
            category=text(PLACE_CATEGORY_SELECTORS),
        )
        self.assertEqual(candidate.name, payload["maps_candidate"]["name"])
        row = payload["source_row"]
        assessment = assess_place_match(row, candidate)
        self.assertEqual(assessment.strength, "strong")
        self.assertFalse(assessment.hard_veto)
        self.assertFalse(assessment.address_like_candidate_name)
        driver = FakeDriver()
        driver.candidates_by_query = {query: candidate for query in build_place_query_attempts(row)}
        writer = GoogleMapsSyncWriter(
            user_data_dir=Path("/unused-profile"), headless=True, sync_delay_seconds=0, max_save_retries=0,
            list_name_template="{scope} 米其林 {level_badge}", list_name_prefix="", on_missing_list="stop",
            ignore_existing_lists_check=True, probe_only=True, language="zh_TW", driver=driver,
        )
        await writer.initialize_run(scope_name="臺灣", level_slugs=("selected",))
        result = await writer.sync_row("selected", row)
        self.assertEqual(result.status, SyncRowStatus.SKIPPED)
        self.assertIsNone(result.failure)
        self.assertEqual(driver.save_calls, [])
