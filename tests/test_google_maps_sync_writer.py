"""Tests for Google Maps sync writer policy behavior."""

import tempfile
import unittest
from pathlib import Path

from michelin_scraper.adapters.google_maps_driver import (
    GoogleMapsListAlreadyExistsError,
    GoogleMapsNoteWriteError,
    GoogleMapsPlaceAlreadySavedError,
    GoogleMapsSelectorError,
    GoogleMapsTransientError,
)
from michelin_scraper.adapters.google_maps_sync_writer import (
    GoogleMapsRowSyncFailFastError,
    GoogleMapsSyncWriter,
    _build_place_note_text,
    _is_saved_list_landing_candidate,
)
from michelin_scraper.application.place_matcher import PlaceCandidate
from tests.fakes import FakeDriver


class GoogleMapsSyncWriterTests(unittest.IsolatedAsyncioTestCase):
    def _build_writer(
        self,
        temp_dir: str,
        on_missing_list: str = "stop",
        list_name_template: str = "{scope}|{level}",
        ignore_existing_lists_check: bool = False,
        probe_only: bool = False,
        language: str = "en",
        updated_on: str = "2026-05-01",
        driver: FakeDriver | None = None,
    ) -> GoogleMapsSyncWriter:
        writer = GoogleMapsSyncWriter(
            user_data_dir=Path(temp_dir) / "profile",
            headless=True,
            sync_delay_seconds=0,
            max_save_retries=0,
            list_name_template=list_name_template,
            list_name_prefix="",
            on_missing_list=on_missing_list,
            ignore_existing_lists_check=ignore_existing_lists_check,
            probe_only=probe_only,
            language=language,
            updated_on=updated_on,
            driver=driver,
        )
        return writer

    def test_build_place_note_text_uses_english_locale_format(self) -> None:
        note_text = _build_place_note_text(
            {
                "GuideYear": "2025",
                "Rating": "3 Stars",
                "Cuisine": "Cantonese",
                "Description": "Refined banquet-style cooking.",
            },
            level_slug="three-star",
            language="en",
            updated_on="2026-05-01",
        )

        self.assertEqual(
            note_text,
            "2025 | 3 Stars | Cantonese | updated 2026-05-01\nRefined banquet-style cooking.",
        )

    def test_build_place_note_text_uses_traditional_chinese_locale_format(self) -> None:
        note_text = _build_place_note_text(
            {
                "GuideYear": "2025",
                "Rating": "3 Stars",
                "Cuisine": "粵菜",
                "Description": "精緻宴席料理。",
            },
            level_slug="three-star",
            language="zh-tw",
            updated_on="2026-05-01",
        )

        self.assertEqual(
            note_text,
            "2025 | 三星 | 粵菜 | 2026-05-01 更新\n精緻宴席料理。",
        )

    def test_build_place_note_text_without_guide_year_still_uses_updated_date(self) -> None:
        note_text = _build_place_note_text(
            {
                "Rating": "Selected",
                "Cuisine": "台菜",
                "Description": "經典風味。",
            },
            level_slug="selected",
            language="zh-tw",
            updated_on="2026-05-01",
        )

        self.assertEqual(
            note_text,
            "入選 | 台菜 | 2026-05-01 更新\n經典風味。",
        )

    async def test_initialize_run_fails_when_required_list_already_exists_at_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            fake_driver.existing_lists.add("Taiwan|one-star")
            writer = self._build_writer(temp_dir, driver=fake_driver)

            with self.assertRaisesRegex(
                GoogleMapsListAlreadyExistsError,
                "List already exists at startup: Taiwan\\|one-star",
            ):
                await writer.initialize_run(scope_name="Taiwan", level_slugs=("one-star",))
            self.assertEqual(fake_driver.created_lists, [])
            self.assertEqual(fake_driver.queries, [])
            self.assertEqual(fake_driver.save_calls, [])

    async def test_sync_rows_by_level_reuses_existing_list_when_check_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            fake_driver.existing_lists.add("Taiwan|one-star")
            fake_driver.openable_lists.add("Taiwan|one-star")
            fake_driver.candidates_by_query = {
                "Alpha": PlaceCandidate(
                    name="Alpha",
                    address="Taipei City",
                    category="Restaurant",
                ),
            }
            writer = self._build_writer(
                temp_dir,
                ignore_existing_lists_check=True,
                driver=fake_driver,
            )

            await writer.initialize_run(scope_name="Taiwan", level_slugs=("one-star",))
            result = await writer.sync_rows_by_level(
                {
                    "one-star": [
                        {
                            "Name": "Alpha",
                            "City": "Taipei",
                            "Address": "No. 1 Example Street",
                            "Cuisine": "Taiwanese",
                        }
                    ]
                }
            )

            self.assertEqual(writer.list_names_by_level["one-star"], "Taiwan|one-star")
            self.assertEqual(result.added_count_by_level["one-star"], 1)
            self.assertEqual(fake_driver.created_lists, [])
            self.assertEqual(writer.list_created_by_level["one-star"], False)

    async def test_initialize_run_probe_only_skips_auth_and_list_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(temp_dir, probe_only=True, driver=fake_driver)

            await writer.initialize_run(scope_name="Taiwan", level_slugs=("one-star",))

            self.assertEqual(fake_driver.auth_checks, 0)
            self.assertEqual(fake_driver.created_lists, [])

    async def test_sync_rows_by_level_continue_policy_tracks_missing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            fake_driver.existing_lists.add("Taiwan|one-star")
            writer = self._build_writer(
                temp_dir,
                on_missing_list="continue",
                ignore_existing_lists_check=True,
                driver=fake_driver,
            )
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("one-star",))
            result = await writer.sync_rows_by_level(
                {
                    "one-star": [{"Name": "Alpha", "City": "Taipei"}],
                }
            )

            self.assertEqual(result.added_count_by_level["one-star"], 0)
            self.assertEqual(result.skipped_count_by_level["one-star"], 1)
            self.assertEqual(len(result.failed_items), 0)
            self.assertEqual(writer.missing_row_counts_by_level["one-star"], 1)

    async def test_sync_rows_by_level_uses_query_cascade_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(temp_dir, driver=fake_driver)
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("one-star",))

            fake_driver.candidates_by_query = {
                "Alpha": PlaceCandidate(
                    name="Alpha",
                    address="Taipei City",
                    category="Restaurant",
                ),
            }

            result = await writer.sync_rows_by_level(
                {
                    "one-star": [
                        {
                            "Name": "Alpha",
                            "City": "Taipei",
                            "Address": "No. 1 Example Street",
                            "Cuisine": "Taiwanese",
                        }
                    ]
                }
            )

            self.assertEqual(result.added_count_by_level["one-star"], 1)
            self.assertEqual(fake_driver.queries, ["Alpha Taipei", "Alpha"])

    async def test_sync_rows_by_level_skips_nested_false_positive_and_uses_next_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            fake_driver.openable_lists.add("Taiwan|selected")
            writer = self._build_writer(
                temp_dir,
                on_missing_list="continue",
                ignore_existing_lists_check=True,
                driver=fake_driver,
            )
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("selected",))
            fake_driver.candidates_by_query = {
                "陽明春天 (士林) Taipei, 臺灣": PlaceCandidate(
                    name="Pure Honey Oil Workshop - Yuanpin Food Co., Ltd.",
                    address="No. 119-1號, Jingshan Rd, Shilin District, Taipei City, 111",
                    category="Store",
                    located_in="Located in: Yangming Spring",
                ),
                "陽明春天 (士林)": PlaceCandidate(
                    name="陽明春天 (士林)",
                    address="No. 119-1號, Jingshan Rd, Shilin District, Taipei City, 111",
                    category="Vegetarian restaurant",
                ),
            }

            result = await writer.sync_rows_by_level(
                {
                    "selected": [
                        {
                            "Name": "陽明春天 (士林)",
                            "City": "Taipei, 臺灣",
                            "Address": "士林區菁山路119之1號",
                            "Cuisine": "Vegetarian",
                        }
                    ]
                }
            )

            self.assertEqual(result.added_count_by_level["selected"], 1)
            self.assertEqual(
                fake_driver.queries,
                [
                    "陽明春天 (士林) 士林區",
                    "陽明春天 (士林) 菁山路119之1號",
                    "陽明春天 (士林) Taipei, 臺灣",
                    "陽明春天 (士林)",
                ],
            )

    async def test_sync_rows_by_level_uses_district_query_before_city_query_for_shou_wu(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            fake_driver.openable_lists.add("Taiwan|selected")
            writer = self._build_writer(
                temp_dir,
                ignore_existing_lists_check=True,
                driver=fake_driver,
            )
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("selected",))
            fake_driver.candidates_by_query = {
                "首烏 板橋區": PlaceCandidate(
                    name="首烏客家小館",
                    address="板橋區民族路27號, New Taipei, 臺灣",
                    category="客家菜館",
                ),
            }

            result = await writer.sync_rows_by_level(
                {
                    "selected": [
                        {
                            "Name": "首烏",
                            "City": "New Taipei, 臺灣",
                            "Address": "板橋區民族路27號",
                            "Cuisine": "客家菜",
                        }
                    ]
                }
            )

            self.assertEqual(result.added_count_by_level["selected"], 1)
            self.assertEqual(fake_driver.queries, ["首烏 板橋區"])

    async def test_sync_rows_by_level_rejects_shou_wu_city_only_wrong_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            fake_driver.openable_lists.add("Taiwan|selected")
            writer = self._build_writer(
                temp_dir,
                on_missing_list="continue",
                ignore_existing_lists_check=True,
                driver=fake_driver,
            )
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("selected",))
            fake_driver.candidates_by_query = {
                "首烏 板橋區": None,
                "首烏 New Taipei, 臺灣": PlaceCandidate(
                    name="模咒CURSE",
                    address=(
                        "No. 77-1號, Fude 2nd Rd, Xizhi District, "
                        "New Taipei City, 221011"
                    ),
                    category="美式餐廳",
                ),
            }

            result = await writer.sync_rows_by_level(
                {
                    "selected": [
                        {
                            "Name": "首烏",
                            "City": "New Taipei, 臺灣",
                            "Address": "板橋區民族路27號",
                            "Cuisine": "客家菜",
                        }
                    ]
                }
            )

            self.assertEqual(result.added_count_by_level["selected"], 0)
            self.assertEqual(len(result.failed_items), 1)
            self.assertEqual(result.failed_items[0].reason, "AmbiguousPlaceMatch")
            self.assertIn("首烏 New Taipei, 臺灣", fake_driver.queries)

    async def test_sync_rows_by_level_rejects_address_only_name_mismatch_and_uses_next_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            fake_driver.openable_lists.add("Taiwan|selected")
            writer = self._build_writer(
                temp_dir,
                ignore_existing_lists_check=True,
                driver=fake_driver,
            )
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("selected",))
            fake_driver.candidates_by_query = {
                "首烏 板橋區": None,
                "首烏 New Taipei, 臺灣": None,
                "首烏": None,
                "板橋區民族路27號": PlaceCandidate(
                    name="No. 27, Minzu Road",
                    address="No. 27, Minzu Road, Banqiao District, New Taipei, Taiwan",
                    category="",
                ),
                "首烏 客家菜 板橋區": PlaceCandidate(
                    name="首烏客家小館",
                    address="板橋區民族路27號, New Taipei, 臺灣",
                    category="客家菜館",
                ),
            }

            result = await writer.sync_rows_by_level(
                {
                    "selected": [
                        {
                            "Name": "首烏",
                            "City": "New Taipei, 臺灣",
                            "Address": "板橋區民族路27號",
                            "Cuisine": "客家菜",
                        }
                    ]
                }
            )

            self.assertEqual(result.added_count_by_level["selected"], 1)
            self.assertEqual(
                fake_driver.queries,
                [
                    "首烏 板橋區",
                    "首烏 民族路27號",
                    "首烏 New Taipei, 臺灣",
                    "首烏",
                    "板橋區民族路27號",
                    "首烏 客家菜 板橋區",
                ],
            )

    def test_saved_list_landing_candidate_guard_matches_list_name_without_place_metadata(self) -> None:
        self.assertTrue(
            _is_saved_list_landing_candidate(
                candidate=PlaceCandidate(name="Tokyo Michelin \u2b50", address="", category=""),
                list_name="Tokyo Michelin \u2b50",
            )
        )
        self.assertFalse(
            _is_saved_list_landing_candidate(
                candidate=PlaceCandidate(name="Tokyo Michelin \u2b50", address="Tokyo", category="Restaurant"),
                list_name="Tokyo Michelin \u2b50",
            )
        )

    async def test_sync_rows_by_level_treats_saved_list_landing_candidates_as_non_place_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(
                temp_dir,
                on_missing_list="continue",
                list_name_template="Tokyo Michelin {level_badge}",
                ignore_existing_lists_check=True,
                language="en",
                driver=fake_driver,
            )
            await writer.initialize_run(scope_name="Tokyo", level_slugs=("one-star",))
            fake_driver.existing_lists.add("Tokyo Michelin \u2b50")
            fake_driver.openable_lists.add("Tokyo Michelin \u2b50")
            fake_driver.candidates_by_query = {
                "Oku Tokyo": PlaceCandidate(name="Tokyo Michelin \u2b50", address="", category=""),
                "Oku": PlaceCandidate(name="Tokyo Michelin \u2b50", address="", category=""),
                "3-42-11 Asakusa": PlaceCandidate(name="Tokyo Michelin \u2b50", address="", category=""),
                "Oku Sushi Tokyo": PlaceCandidate(name="Tokyo Michelin \u2b50", address="", category=""),
            }

            result = await writer.sync_rows_by_level(
                {
                    "one-star": [
                        {
                            "Name": "Oku",
                            "NameLocal": "\u5965",
                            "City": "Tokyo",
                            "Address": "3-42-11 Asakusa",
                            "Cuisine": "Sushi",
                        }
                    ]
                }
            )

            self.assertEqual(result.added_count_by_level["one-star"], 0)
            self.assertEqual(len(result.failed_items), 1)
            self.assertEqual(result.failed_items[0].reason, "PlaceNotFound")

    async def test_sync_rows_by_level_stop_policy_fails_fast_on_first_row_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(temp_dir, on_missing_list="stop", driver=fake_driver)
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("one-star",))
            fake_driver.save_error = GoogleMapsNoteWriteError(
                "Unable to apply note text in the current save surface."
            )
            fake_driver.candidates_by_query = {
                "Alpha Taipei": PlaceCandidate(
                    name="Alpha",
                    address="Taipei City",
                    category="Restaurant",
                ),
                "Beta Taipei": PlaceCandidate(
                    name="Beta",
                    address="Taipei City",
                    category="Restaurant",
                ),
            }

            with self.assertRaises(GoogleMapsRowSyncFailFastError) as captured:
                await writer.sync_rows_by_level(
                    {
                        "one-star": [
                            {"Name": "Alpha", "City": "Taipei", "Cuisine": "Jiangzhe"},
                            {"Name": "Beta", "City": "Taipei", "Cuisine": "French"},
                        ]
                    }
                )

            self.assertEqual(captured.exception.failure.restaurant_name, "Alpha")
            self.assertTrue(captured.exception.failure.reason.startswith("NoteWriteFailed:"))
            self.assertEqual(fake_driver.queries, ["Alpha Taipei"])
            self.assertEqual(captured.exception.added_count_by_level["one-star"], 0)
            self.assertEqual(captured.exception.skipped_count_by_level["one-star"], 0)

    async def test_sync_rows_by_level_records_added_when_note_write_fails_after_place_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(temp_dir, on_missing_list="stop", driver=fake_driver)
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("one-star",))
            fake_driver.candidates_by_query = {
                "Alpha Taipei": PlaceCandidate(
                    name="Alpha",
                    address="Taipei City",
                    category="Restaurant",
                ),
            }
            fake_driver.save_error = GoogleMapsNoteWriteError(
                "Unable to verify note text persisted in the save surface after retry. "
                "Place saved before note failure: yes.",
                place_saved=True,
            )

            result = await writer.sync_rows_by_level(
                {
                    "one-star": [
                        {"Name": "Alpha", "City": "Taipei", "Cuisine": "Jiangzhe"},
                    ]
                }
            )

            self.assertEqual(result.added_count_by_level["one-star"], 1)
            self.assertEqual(result.skipped_count_by_level["one-star"], 0)
            self.assertEqual(len(result.failed_items), 0)

    async def test_sync_rows_by_level_treats_place_already_saved_as_success_after_note_write_retry(
        self,
    ) -> None:
        """Regression test: if attempt 1 saves the place but the note write fails, attempt 2
        should treat GoogleMapsPlaceAlreadySavedError as a success (the place was already saved
        in the prior attempt), not as a fatal error."""
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = GoogleMapsSyncWriter(
                user_data_dir=Path(temp_dir) / "profile",
                headless=True,
                sync_delay_seconds=0,
                max_save_retries=1,  # allow one retry
                list_name_template="{scope}|{level}",
                list_name_prefix="",
                on_missing_list="stop",
                ignore_existing_lists_check=False,
                updated_on="2026-05-01",
                driver=fake_driver,
            )
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("bib-gourmand",))
            fake_driver.candidates_by_query = {
                "\u963f\u7f8e\u98ef\u5e97 Tainan": PlaceCandidate(
                    name="\u963f\u7f8e\u98ef\u5e97",
                    address="Tainan City",
                    category="Restaurant",
                ),
            }

            call_count = 0

            async def save_note_fail_then_success_after_already_saved(list_name: str, note_text: str = "") -> bool:
                nonlocal call_count
                fake_driver.save_calls.append((list_name, note_text))
                call_count += 1
                if call_count == 1:
                    # Attempt 1: place is saved but note write fails.
                    raise GoogleMapsNoteWriteError(
                        "Unable to verify note text persisted in the save surface after retry. "
                        "Place saved before note failure: no.",
                        place_saved=False,
                    )
                # Attempt 2: place was already saved in attempt 1, so the driver
                # should have proceeded to write the note successfully.
                return True

            fake_driver.save_current_place_to_list = save_note_fail_then_success_after_already_saved  # type: ignore[method-assign]

            result = await writer.sync_rows_by_level(
                {
                    "bib-gourmand": [
                        {
                            "Name": "\u963f\u7f8e\u98ef\u5e97",
                            "GuideYear": "2025",
                            "City": "Tainan",
                            "Cuisine": "Taiwanese",
                            "Description": "Authentic Cuisine",
                        }
                    ]
                }
            )

            # The place was saved in attempt 2 (after already being saved in attempt 1);
            # this should be treated as success (added).
            # The note write should include a compact Michelin header on line 1.
            self.assertEqual(result.added_count_by_level["bib-gourmand"], 1)
            self.assertEqual(result.skipped_count_by_level["bib-gourmand"], 0)
            self.assertEqual(len(result.failed_items), 0)
            self.assertEqual(call_count, 2)
            self.assertEqual(
                fake_driver.save_calls[1][1],
                "2025 | Bib Gourmand | Taiwanese | updated 2026-05-01\nAuthentic Cuisine",
            )

    async def test_sync_rows_by_level_does_not_retry_note_failure_when_place_already_saved(
        self,
    ) -> None:
        """Regression test: if note verification fails after the save was applied,
        do not retry the save path and risk duplicating or reprocessing the row."""
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = GoogleMapsSyncWriter(
                user_data_dir=Path(temp_dir) / "profile",
                headless=True,
                sync_delay_seconds=0,
                max_save_retries=1,  # allow one retry
                list_name_template="{scope}|{level}",
                list_name_prefix="",
                on_missing_list="stop",
                ignore_existing_lists_check=False,
                updated_on="2026-05-01",
                driver=fake_driver,
            )
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("bib-gourmand",))

            search_call_count = 0

            async def search_ok_then_transient_error(query: str) -> PlaceCandidate | None:
                nonlocal search_call_count
                search_call_count += 1
                if search_call_count <= 1:
                    # Attempt 1: search succeeds.
                    fake_driver.queries.append(query)
                    return PlaceCandidate(
                        name="\u963f\u7f8e\u98ef\u5e97",
                        address="Tainan City",
                        category="Restaurant",
                    )
                # Attempt 2: search times out.
                fake_driver.queries.append(query)
                raise GoogleMapsTransientError(
                    "Timed out waiting for Google Maps search outcome"
                )

            fake_driver.search_and_open_first_result = search_ok_then_transient_error  # type: ignore[method-assign]

            save_call_count = 0

            async def save_note_fail_on_first(list_name: str, note_text: str = "") -> bool:
                nonlocal save_call_count
                fake_driver.save_calls.append((list_name, note_text))
                save_call_count += 1
                if save_call_count == 1:
                    raise GoogleMapsNoteWriteError(
                        "Unable to verify note text persisted",
                        place_saved=True,
                    )
                return True

            fake_driver.save_current_place_to_list = save_note_fail_on_first  # type: ignore[method-assign]

            result = await writer.sync_rows_by_level(
                {
                    "bib-gourmand": [
                        {
                            "Name": "\u963f\u7f8e\u98ef\u5e97",
                            "GuideYear": "2025",
                            "City": "Tainan",
                            "Cuisine": "Taiwanese",
                            "Description": "Authentic Cuisine",
                        }
                    ]
                }
            )

            # The place was saved in attempt 1 and note verification failed. The row
            # should be treated as added without a second search/save attempt.
            self.assertEqual(len(result.failed_items), 0)
            self.assertEqual(result.added_count_by_level["bib-gourmand"], 1)
            self.assertEqual(result.skipped_count_by_level["bib-gourmand"], 0)
            self.assertEqual(search_call_count, 1)
            self.assertEqual(save_call_count, 1)

    async def test_sync_rows_by_level_refreshes_maps_before_retrying_transient_save_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = GoogleMapsSyncWriter(
                user_data_dir=Path(temp_dir) / "profile",
                headless=True,
                sync_delay_seconds=0,
                max_save_retries=1,
                list_name_template="{scope}|{level}",
                list_name_prefix="",
                on_missing_list="stop",
                ignore_existing_lists_check=False,
                updated_on="2026-05-01",
                driver=fake_driver,
            )
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("one-star",))
            fake_driver.candidates_by_query = {
                "Alpha Taipei": PlaceCandidate(
                    name="Alpha",
                    address="Taipei City",
                    category="Restaurant",
                ),
            }
            call_count = 0

            async def save_transient_then_success(list_name: str, note_text: str = "") -> bool:
                nonlocal call_count
                fake_driver.save_calls.append((list_name, note_text))
                call_count += 1
                if call_count == 1:
                    raise GoogleMapsTransientError("Maps reported failed save to Taiwan|One Star")
                return True

            fake_driver.save_current_place_to_list = save_transient_then_success  # type: ignore[method-assign]

            result = await writer.sync_rows_by_level(
                {
                    "one-star": [
                        {"Name": "Alpha", "City": "Taipei", "Cuisine": "Jiangzhe"},
                    ]
                }
            )

            self.assertEqual(len(result.failed_items), 0)
            self.assertEqual(result.added_count_by_level["one-star"], 1)
            self.assertEqual(call_count, 2)
            self.assertGreaterEqual(fake_driver.open_maps_home_calls, 2)

    async def test_sync_rows_by_level_refreshes_and_retries_when_save_dialog_list_option_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = GoogleMapsSyncWriter(
                user_data_dir=Path(temp_dir) / "profile",
                headless=True,
                sync_delay_seconds=0,
                max_save_retries=1,
                list_name_template="{scope}|{level}",
                list_name_prefix="",
                on_missing_list="stop",
                ignore_existing_lists_check=False,
                updated_on="2026-05-01",
                driver=fake_driver,
            )
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("one-star",))
            fake_driver.candidates_by_query = {
                "Alpha Taipei": PlaceCandidate(
                    name="Alpha",
                    address="Taipei City",
                    category="Restaurant",
                ),
            }
            call_count = 0

            async def save_option_missing_then_success(list_name: str, note_text: str = "") -> bool:
                nonlocal call_count
                fake_driver.save_calls.append((list_name, note_text))
                call_count += 1
                return call_count > 1

            fake_driver.save_current_place_to_list = save_option_missing_then_success  # type: ignore[method-assign]

            result = await writer.sync_rows_by_level(
                {
                    "one-star": [
                        {"Name": "Alpha", "City": "Taipei", "Cuisine": "Jiangzhe"},
                    ]
                }
            )

            self.assertEqual(len(result.failed_items), 0)
            self.assertEqual(result.added_count_by_level["one-star"], 1)
            self.assertEqual(call_count, 2)
            self.assertGreaterEqual(fake_driver.open_maps_home_calls, 3)

    async def test_sync_rows_by_level_continue_policy_keeps_processing_after_row_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(temp_dir, on_missing_list="continue", driver=fake_driver)
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("one-star",))
            fake_driver.candidates_by_query = {
                "Alpha Taipei": PlaceCandidate(
                    name="Alpha",
                    address="Taipei City",
                    category="Restaurant",
                ),
                "Beta Taipei": PlaceCandidate(
                    name="Beta",
                    address="Taipei City",
                    category="Restaurant",
                ),
            }
            call_count = 0

            async def save_with_one_failure(list_name: str, note_text: str = "") -> bool:
                nonlocal call_count
                fake_driver.save_calls.append((list_name, note_text))
                call_count += 1
                if call_count == 1:
                    raise GoogleMapsNoteWriteError("Unable to write note.")
                return list_name in fake_driver.openable_lists

            fake_driver.save_current_place_to_list = save_with_one_failure  # type: ignore[method-assign]

            result = await writer.sync_rows_by_level(
                {
                    "one-star": [
                        {"Name": "Alpha", "City": "Taipei", "Cuisine": "Jiangzhe"},
                        {"Name": "Beta", "City": "Taipei", "Cuisine": "French"},
                    ]
                }
            )

            self.assertEqual(result.added_count_by_level["one-star"], 1)
            self.assertEqual(len(result.failed_items), 1)
            self.assertEqual(result.failed_items[0].restaurant_name, "Alpha")
            self.assertIn("Beta Taipei", fake_driver.queries)

    async def test_sync_rows_by_level_passes_cuisine_and_description_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(temp_dir, driver=fake_driver)
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("one-star",))

            fake_driver.candidates_by_query = {
                "Alpha Taipei": PlaceCandidate(
                    name="Alpha",
                    address="Taipei City",
                    category="Restaurant",
                ),
            }

            result = await writer.sync_rows_by_level(
                {
                    "one-star": [
                        {
                            "Name": "Alpha",
                            "GuideYear": "2025",
                            "City": "Taipei",
                            "Address": "No. 1 Example Street",
                            "Cuisine": "Jiangzhe",
                            "Description": "A vibrant dining room with a skyline view.",
                        }
                    ]
                }
            )

            self.assertEqual(result.added_count_by_level["one-star"], 1)
            self.assertEqual(
                fake_driver.save_calls,
                [
                    (
                        "Taiwan|one-star",
                        "2025 | 1 Star | Jiangzhe | updated 2026-05-01\n"
                        "A vibrant dining room with a skyline view.",
                    )
                ],
            )

    async def test_sync_rows_by_level_smoke_marks_failure_when_note_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(temp_dir, on_missing_list="continue", driver=fake_driver)
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("one-star",))

            fake_driver.candidates_by_query = {
                "Alpha Taipei": PlaceCandidate(
                    name="Alpha",
                    address="Taipei City",
                    category="Restaurant",
                ),
            }
            fake_driver.save_error = GoogleMapsNoteWriteError(
                "Unable to apply note text in the current save surface."
            )

            result = await writer.sync_rows_by_level(
                {
                    "one-star": [
                        {
                            "Name": "Alpha",
                            "GuideYear": "2025",
                            "City": "Taipei",
                            "Address": "No. 1 Example Street",
                            "Cuisine": "Jiangzhe",
                            "Description": "A vibrant dining room with a skyline view.",
                        }
                    ]
                }
            )

            self.assertEqual(result.added_count_by_level["one-star"], 0)
            self.assertEqual(result.skipped_count_by_level["one-star"], 0)
            self.assertEqual(len(result.failed_items), 1)
            self.assertTrue(result.failed_items[0].reason.startswith("NoteWriteFailed:"))
            self.assertEqual(
                fake_driver.save_calls,
                [
                    (
                        "Taiwan|one-star",
                        "2025 | 1 Star | Jiangzhe | updated 2026-05-01\n"
                        "A vibrant dining room with a skyline view.",
                    )
                ],
            )

    async def test_sync_rows_by_level_smoke_preserves_runtime_context_on_selector_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(temp_dir, on_missing_list="continue", driver=fake_driver)
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("one-star",))

            fake_driver.candidates_by_query = {
                "Alpha Taipei": PlaceCandidate(
                    name="Alpha",
                    address="Taipei City",
                    category="Restaurant",
                ),
            }
            fake_driver.save_error = GoogleMapsSelectorError(
                "Google Maps search box is not available. Visible search controls snapshot: input:searchboxinput"
            )

            result = await writer.sync_rows_by_level(
                {
                    "one-star": [
                        {
                            "Name": "Alpha",
                            "City": "Taipei",
                            "Address": "No. 1 Example Street",
                            "Cuisine": "Jiangzhe",
                        }
                    ]
                }
            )

            self.assertEqual(result.added_count_by_level["one-star"], 0)
            self.assertEqual(result.skipped_count_by_level["one-star"], 0)
            self.assertEqual(len(result.failed_items), 1)
            self.assertTrue(result.failed_items[0].reason.startswith("SelectorRuntimeFailure:"))
            self.assertIn("Visible search controls snapshot", result.failed_items[0].reason)

    async def test_sync_rows_by_level_skips_already_saved_place_when_check_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(temp_dir, ignore_existing_lists_check=True, driver=fake_driver)
            await writer.initialize_run(scope_name="Tokyo", level_slugs=("one-star",))
            fake_driver.save_error = GoogleMapsPlaceAlreadySavedError(
                "Current place is already saved in list 'Tokyo|one-star'."
            )
            fake_driver.candidates_by_query = {
                "Alpha Tokyo": PlaceCandidate(
                    name="Alpha",
                    address="No. 1 Example Street, Tokyo, Japan",
                    category="Restaurant",
                ),
            }
            row = {
                "Name": "Alpha",
                "City": "Tokyo",
                "Address": "No. 1 Example Street",
                "Cuisine": "French",
            }

            first_result = await writer.sync_rows_by_level({"one-star": [row]})

            self.assertEqual(first_result.added_count_by_level["one-star"], 0)
            self.assertEqual(first_result.skipped_count_by_level["one-star"], 1)
            self.assertEqual(len(first_result.failed_items), 0)
            first_query_count = len(fake_driver.queries)
            first_save_count = len(fake_driver.save_calls)

            second_result = await writer.sync_rows_by_level({"one-star": [row]})

            self.assertEqual(second_result.added_count_by_level["one-star"], 0)
            self.assertEqual(second_result.skipped_count_by_level["one-star"], 1)
            self.assertEqual(len(second_result.failed_items), 0)
            self.assertEqual(len(fake_driver.queries), first_query_count)
            self.assertEqual(len(fake_driver.save_calls), first_save_count)

    async def test_sync_rows_by_level_raises_for_already_saved_place_without_ignore_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(temp_dir, ignore_existing_lists_check=False, driver=fake_driver)
            await writer.initialize_run(scope_name="Tokyo", level_slugs=("one-star",))
            fake_driver.save_error = GoogleMapsPlaceAlreadySavedError(
                "Current place is already saved in list 'Tokyo|one-star'."
            )
            fake_driver.candidates_by_query = {
                "Alpha Tokyo": PlaceCandidate(
                    name="Alpha",
                    address="No. 1 Example Street, Tokyo, Japan",
                    category="Restaurant",
                ),
            }

            with self.assertRaisesRegex(
                GoogleMapsPlaceAlreadySavedError,
                "Re-run with --ignore-existing-lists-check",
            ):
                await writer.sync_rows_by_level(
                    {
                        "one-star": [
                            {
                                "Name": "Alpha",
                                "City": "Tokyo",
                                "Address": "No. 1 Example Street",
                                "Cuisine": "French",
                            }
                        ]
                    }
                )

    async def test_sync_rows_by_level_probe_only_matches_without_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(temp_dir, probe_only=True, driver=fake_driver)
            await writer.initialize_run(scope_name="Tokyo", level_slugs=("one-star",))

            fake_driver.candidates_by_query = {
                "Alpha": PlaceCandidate(
                    name="Alpha",
                    address="Tokyo",
                    category="Restaurant",
                ),
            }

            result = await writer.sync_rows_by_level(
                {
                    "one-star": [
                        {
                            "Name": "Alpha",
                            "City": "Tokyo",
                            "Address": "No. 1 Example Street",
                            "Cuisine": "French",
                        }
                    ],
                }
            )

            self.assertEqual(result.added_count_by_level["one-star"], 0)
            self.assertEqual(result.skipped_count_by_level["one-star"], 1)
            self.assertEqual(len(result.failed_items), 0)
            self.assertEqual(fake_driver.save_calls, [])

    async def test_initialize_run_renders_level_label_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(
                temp_dir,
                list_name_template="{scope}|{level_label}",
                driver=fake_driver,
            )

            await writer.initialize_run(scope_name="Tokyo", level_slugs=("three-star",))

            self.assertEqual(writer.list_names_by_level["three-star"], "Tokyo|3 Stars")
            self.assertEqual(writer.list_created_by_level["three-star"], False)

    async def test_initialize_run_renders_level_badge_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(
                temp_dir,
                list_name_template="{scope} Michelin {level_badge}",
                driver=fake_driver,
            )

            await writer.initialize_run(scope_name="Tokyo", level_slugs=("three-star",))

            self.assertEqual(writer.list_names_by_level["three-star"], "Tokyo Michelin \u2b50\u2b50\u2b50")
            self.assertEqual(writer.list_created_by_level["three-star"], False)

            await writer.initialize_run(scope_name="Tokyo", level_slugs=("stars",))

            self.assertEqual(writer.list_names_by_level["stars"], "Tokyo Michelin Stars")
            self.assertEqual(writer.list_created_by_level["stars"], False)

            await writer.initialize_run(scope_name="Tokyo", level_slugs=("bib-gourmand",))

            self.assertEqual(
                writer.list_names_by_level["bib-gourmand"],
                "Tokyo Michelin Bib Gourmand",
            )
            self.assertEqual(writer.list_created_by_level["bib-gourmand"], False)

    async def test_initialize_run_renders_selected_level_badge_with_language_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            writer = self._build_writer(
                temp_dir,
                list_name_template="{scope} \u7c73\u5176\u6797 {level_badge}",
                language="zh_TW",
                driver=fake_driver,
            )

            await writer.initialize_run(scope_name="\u81fa\u5357", level_slugs=("selected",))

            self.assertEqual(writer.list_names_by_level["selected"], "\u81fa\u5357 \u7c73\u5176\u6797 \u5165\u9078")
            self.assertEqual(writer.list_created_by_level["selected"], False)

            await writer.initialize_run(scope_name="\u81fa\u5357", level_slugs=("stars",))

            self.assertEqual(writer.list_names_by_level["stars"], "\u81fa\u5357 \u7c73\u5176\u6797 \u661f\u7d1a")
            self.assertEqual(writer.list_created_by_level["stars"], False)

    async def test_sync_rows_by_level_smoke_creates_only_lists_with_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_driver = FakeDriver()
            fake_driver.candidates_by_query = {
                "Alpha": PlaceCandidate(
                    name="Alpha",
                    address="Taipei City",
                    category="Restaurant",
                ),
            }
            writer = self._build_writer(temp_dir, driver=fake_driver)
            await writer.initialize_run(scope_name="Taiwan", level_slugs=("one-star", "two-star"))

            result = await writer.sync_rows_by_level(
                {
                    "one-star": [
                        {
                            "Name": "Alpha",
                            "City": "Taipei",
                            "Address": "No. 1 Example Street",
                            "Cuisine": "Taiwanese",
                        }
                    ],
                    "two-star": [],
                }
            )

            self.assertEqual(result.added_count_by_level["one-star"], 1)
            self.assertEqual(result.added_count_by_level["two-star"], 0)
            self.assertTrue(writer.list_created_by_level["one-star"])
            self.assertFalse(writer.list_created_by_level["two-star"])
            self.assertEqual(fake_driver.created_lists, ["Taiwan|one-star"])


if __name__ == "__main__":
    unittest.main()
