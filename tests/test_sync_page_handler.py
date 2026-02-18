"""Tests for sync page handler debug failure logging."""

import unittest
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from michelin_scraper.adapters.google_maps_sync_writer import GoogleMapsRowSyncFailFastError
from michelin_scraper.application.row_router import LevelRowRouter
from michelin_scraper.application.sync_enums import SyncRowStatus
from michelin_scraper.application.sync_models import (
    SyncAccumulation,
    SyncBatchResult,
    SyncItemFailure,
    SyncRowResult,
    create_empty_row_counts,
)
from michelin_scraper.application.sync_page_handler import SyncPageHandler


class _FakeCheckpointStore:
    def __init__(self) -> None:
        self.save_calls = 0
        self.clear_calls = 0

    def save(self, **kwargs: object) -> None:
        del kwargs
        self.save_calls += 1

    def load(self, expected_start_url: str) -> tuple[None, None]:
        del expected_start_url
        return None, None

    def clear(self) -> None:
        self.clear_calls += 1


class _FakeSyncWriter:
    list_names_by_level: dict[str, str] = {"one-star": "Tokyo Michelin ⭐"}
    missing_row_counts_by_level: dict[str, int] = {"one-star": 0}

    async def initialize_run(self, *, scope_name: str, level_slugs: Sequence[str]) -> None:
        del scope_name, level_slugs

    async def sync_row(self, level_slug: str, row: dict[str, Any]) -> SyncRowResult:
        del level_slug, row
        return SyncRowResult(
            status=SyncRowStatus.FAILED,
            failure=SyncItemFailure(
                level_slug="one-star",
                row_key="one-star::alpha",
                restaurant_name="Alpha",
                reason="PlaceNotFound",
                attempted_queries=("Alpha Tokyo", "Alpha"),
            ),
        )

    async def sync_rows_by_level(
        self,
        rows_by_level: Mapping[str, list[dict[str, Any]]],
    ) -> SyncBatchResult:
        del rows_by_level
        return SyncBatchResult(
            added_count_by_level={"one-star": 0},
            skipped_count_by_level={"one-star": 0},
            failed_items=(
                SyncItemFailure(
                    level_slug="one-star",
                    row_key="one-star::alpha",
                    restaurant_name="Alpha",
                    reason="PlaceNotFound",
                    attempted_queries=("Alpha Tokyo", "Alpha"),
                ),
            ),
        )

    async def finalize_run(self) -> None:
        return


class _CapturingSyncWriter:
    list_names_by_level: dict[str, str] = {"one-star": "Tokyo Michelin ⭐"}
    missing_row_counts_by_level: dict[str, int] = {"one-star": 0}

    def __init__(self) -> None:
        self.last_rows: list[dict[str, Any]] = []

    async def initialize_run(self, *, scope_name: str, level_slugs: Sequence[str]) -> None:
        del scope_name, level_slugs

    async def sync_row(self, level_slug: str, row: dict[str, Any]) -> SyncRowResult:
        self.last_rows.append(row)
        return SyncRowResult(status=SyncRowStatus.ADDED)

    async def sync_rows_by_level(
        self,
        rows_by_level: Mapping[str, list[dict[str, Any]]],
    ) -> SyncBatchResult:
        self.last_rows = [
            row
            for rows in rows_by_level.values()
            for row in rows
        ]
        return SyncBatchResult(
            added_count_by_level={"one-star": len(self.last_rows)},
            skipped_count_by_level={"one-star": 0},
            failed_items=(),
        )

    async def finalize_run(self) -> None:
        return


class _FailFastSyncWriter:
    list_names_by_level: dict[str, str] = {"one-star": "Tokyo Michelin ⭐"}
    missing_row_counts_by_level: dict[str, int] = {"one-star": 0}

    async def initialize_run(self, *, scope_name: str, level_slugs: Sequence[str]) -> None:
        del scope_name, level_slugs

    async def sync_row(self, level_slug: str, row: dict[str, Any]) -> SyncRowResult:
        del level_slug, row
        failure = SyncItemFailure(
            level_slug="one-star",
            row_key="one-star::alpha",
            restaurant_name="Alpha",
            reason="NoteWriteFailed: unable to confirm note",
            attempted_queries=("Alpha Tokyo", "Alpha"),
        )
        raise GoogleMapsRowSyncFailFastError(
            failure=failure,
            added_count_by_level={"one-star": 0},
            skipped_count_by_level={"one-star": 0},
        )

    async def sync_rows_by_level(
        self,
        rows_by_level: Mapping[str, list[dict[str, Any]]],
    ) -> SyncBatchResult:
        del rows_by_level
        failure = SyncItemFailure(
            level_slug="one-star",
            row_key="one-star::alpha",
            restaurant_name="Alpha",
            reason="NoteWriteFailed: unable to confirm note",
            attempted_queries=("Alpha Tokyo", "Alpha"),
        )
        raise GoogleMapsRowSyncFailFastError(
            failure=failure,
            added_count_by_level={"one-star": 0},
            skipped_count_by_level={"one-star": 0},
        )

    async def finalize_run(self) -> None:
        return


class _FakeOutput:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def show_resume(self, next_page_number: int, next_url: str) -> None:
        del next_page_number, next_url

    def show_interrupted(
        self,
        *,
        scraped_total: int = 0,
        added_total: int = 0,
        failed_total: int = 0,
        skipped_total: int = 0,
    ) -> None:
        del scraped_total, added_total, failed_total, skipped_total

    def show_failure(self, message: str) -> None:
        del message

    def show_final_results(self, summary: object) -> None:
        del summary

    def create_progress_reporter(self) -> _NoOpReporter:
        return _NoOpReporter()


class _NoOpReporter:
    def update(self, message: str, progress: float | None = None) -> None:
        del message, progress

    def log(self, message: str) -> None:
        del message

    def finish(self, message: str | None = None) -> None:
        del message


class SyncPageHandlerTests(unittest.IsolatedAsyncioTestCase):
    def _build_handler(
        self,
        *,
        debug_sync_failures: bool,
        on_page_sync_failures: Callable[[int, Sequence[SyncItemFailure]], None] | None = None,
    ) -> tuple[SyncPageHandler, _FakeOutput]:
        level_slugs = ("one-star",)
        row_router = LevelRowRouter(
            level_slugs=level_slugs,
            rating_to_level_slug={"1 Star": "one-star"},
        )
        output = _FakeOutput()
        handler = SyncPageHandler(
            start_url="https://example.com/start",
            checkpoint_store=_FakeCheckpointStore(),
            sync_writer=_FakeSyncWriter(),
            row_router=row_router,
            accumulation=SyncAccumulation(
                scraped_count_by_level=create_empty_row_counts(level_slugs),
                added_count_by_level=create_empty_row_counts(level_slugs),
                skipped_count_by_level=create_empty_row_counts(level_slugs),
                sample_rows=[],
                failed_items=[],
            ),
            output=output,
            debug_sync_failures=debug_sync_failures,
            on_page_sync_failures=on_page_sync_failures,
        )
        return handler, output

    async def test_on_page_logs_failure_debug_details_when_enabled(self) -> None:
        handler, output = self._build_handler(debug_sync_failures=True)
        restaurants = [{"Name": "Alpha", "Rating": "1 Star", "City": "Tokyo"}]

        await handler.on_page(
            page_number=1,
            page_url="https://example.com/start?page=1",
            restaurants_on_page=restaurants,
            next_url="https://example.com/start?page=2",
            next_page_number=2,
            estimated_total_pages=5,
            total_restaurants=1,
        )
        for row in restaurants:
            await handler.on_item(1, None, None, row)

        self.assertEqual(len(output.warnings), 2)
        self.assertIn("[debug] Page 1 sync failures=1.", output.warnings[0])
        self.assertIn("Reasons: PlaceNotFound=1", output.warnings[0])
        self.assertIn("queries='Alpha Tokyo, Alpha'", output.warnings[1])

    async def test_on_page_calls_failure_snapshot_callback_when_enabled(self) -> None:
        callback_calls: list[tuple[int, tuple[str, ...]]] = []

        def _on_page_sync_failures(page_number: int, failed_items: Sequence[SyncItemFailure]) -> None:
            callback_calls.append(
                (
                    page_number,
                    tuple(str(item.reason) for item in failed_items),
                )
            )

        handler, _output = self._build_handler(
            debug_sync_failures=True,
            on_page_sync_failures=_on_page_sync_failures,
        )
        restaurants = [{"Name": "Alpha", "Rating": "1 Star", "City": "Tokyo"}]

        await handler.on_page(
            page_number=1,
            page_url="https://example.com/start?page=1",
            restaurants_on_page=restaurants,
            next_url="https://example.com/start?page=2",
            next_page_number=2,
            estimated_total_pages=5,
            total_restaurants=1,
        )
        for row in restaurants:
            await handler.on_item(1, None, None, row)

        self.assertEqual(callback_calls, [(1, ("PlaceNotFound",))])

    async def test_on_page_skips_failure_debug_details_when_disabled(self) -> None:
        handler, output = self._build_handler(debug_sync_failures=False)
        restaurants = [{"Name": "Alpha", "Rating": "1 Star", "City": "Tokyo"}]

        await handler.on_page(
            page_number=1,
            page_url="https://example.com/start?page=1",
            restaurants_on_page=restaurants,
            next_url="https://example.com/start?page=2",
            next_page_number=2,
            estimated_total_pages=5,
            total_restaurants=1,
        )
        for row in restaurants:
            await handler.on_item(1, None, None, row)

        self.assertEqual(output.warnings, [])

    async def test_on_page_limits_rows_sent_to_sync_writer(self) -> None:
        level_slugs = ("one-star",)
        row_router = LevelRowRouter(
            level_slugs=level_slugs,
            rating_to_level_slug={"1 Star": "one-star"},
        )
        output = _FakeOutput()
        sync_writer = _CapturingSyncWriter()
        accumulation = SyncAccumulation(
            scraped_count_by_level=create_empty_row_counts(level_slugs),
            added_count_by_level=create_empty_row_counts(level_slugs),
            skipped_count_by_level=create_empty_row_counts(level_slugs),
            sample_rows=[],
            failed_items=[],
        )
        handler = SyncPageHandler(
            start_url="https://example.com/start",
            checkpoint_store=_FakeCheckpointStore(),
            sync_writer=sync_writer,
            row_router=row_router,
            accumulation=accumulation,
            output=output,
            debug_sync_failures=False,
            max_rows_per_page=1,
        )

        restaurants = [
            {"Name": "Alpha", "Rating": "1 Star", "City": "Tokyo"},
            {"Name": "Beta", "Rating": "1 Star", "City": "Tokyo"},
        ]
        for row in restaurants:
            await handler.on_item(1, None, None, row)
        await handler.on_page(
            page_number=1,
            page_url="https://example.com/start?page=1",
            restaurants_on_page=restaurants,
            next_url="https://example.com/start?page=2",
            next_page_number=2,
            estimated_total_pages=5,
            total_restaurants=2,
        )

        self.assertEqual(len(sync_writer.last_rows), 1)
        self.assertEqual(accumulation.scraped_count_by_level["one-star"], 2)
        self.assertEqual(accumulation.added_count_by_level["one-star"], 1)
        self.assertEqual(accumulation.skipped_count_by_level["one-star"], 1)

    async def test_on_page_fail_fast_error_logs_and_re_raises_without_checkpoint_save(self) -> None:
        level_slugs = ("one-star",)
        row_router = LevelRowRouter(
            level_slugs=level_slugs,
            rating_to_level_slug={"1 Star": "one-star"},
        )
        output = _FakeOutput()
        checkpoint_store = _FakeCheckpointStore()
        callback_calls: list[tuple[int, tuple[str, ...]]] = []
        accumulation = SyncAccumulation(
            scraped_count_by_level=create_empty_row_counts(level_slugs),
            added_count_by_level=create_empty_row_counts(level_slugs),
            skipped_count_by_level=create_empty_row_counts(level_slugs),
            sample_rows=[],
            failed_items=[],
        )

        def _on_page_sync_failures(page_number: int, failed_items: Sequence[SyncItemFailure]) -> None:
            callback_calls.append((page_number, tuple(item.reason for item in failed_items)))

        handler = SyncPageHandler(
            start_url="https://example.com/start",
            checkpoint_store=checkpoint_store,
            sync_writer=_FailFastSyncWriter(),
            row_router=row_router,
            accumulation=accumulation,
            output=output,
            debug_sync_failures=True,
            on_page_sync_failures=_on_page_sync_failures,
        )

        restaurants = [{"Name": "Alpha", "Rating": "1 Star", "City": "Tokyo"}]
        with self.assertRaises(GoogleMapsRowSyncFailFastError):
            for row in restaurants:
                await handler.on_item(1, None, None, row)

        self.assertEqual(checkpoint_store.save_calls, 0)
        self.assertEqual(checkpoint_store.clear_calls, 0)
        self.assertEqual(len(accumulation.failed_items), 1)
        self.assertEqual(accumulation.failed_items[0].reason, "NoteWriteFailed: unable to confirm note")
        self.assertEqual(callback_calls, [(1, ("NoteWriteFailed: unable to confirm note",))])
        self.assertEqual(len(output.warnings), 2)
        self.assertIn("[debug] Page 1 sync failures=1.", output.warnings[0])

    async def test_on_item_records_failure_for_unrecognized_rating(self) -> None:
        """An unknown rating must appear in failed_items, not crash."""
        level_slugs = ("one-star",)
        row_router = LevelRowRouter(
            level_slugs=level_slugs,
            rating_to_level_slug={"1 Star": "one-star"},
        )
        output = _FakeOutput()
        accumulation = SyncAccumulation(
            scraped_count_by_level=create_empty_row_counts(level_slugs),
            added_count_by_level=create_empty_row_counts(level_slugs),
            skipped_count_by_level=create_empty_row_counts(level_slugs),
            sample_rows=[],
            failed_items=[],
        )
        handler = SyncPageHandler(
            start_url="https://example.com/start",
            checkpoint_store=_FakeCheckpointStore(),
            sync_writer=_FakeSyncWriter(),
            row_router=row_router,
            accumulation=accumulation,
            output=output,
            debug_sync_failures=True,
        )

        row = {"Name": "Mystery Place", "Rating": "Alien Rating"}
        await handler.on_item(1, None, None, row)

        self.assertEqual(len(accumulation.failed_items), 1)
        failure = accumulation.failed_items[0]
        self.assertIn("UnrecognizedRating", failure.reason)
        self.assertIn("Alien Rating", failure.reason)
        self.assertEqual(failure.restaurant_name, "Mystery Place")
        self.assertEqual(len(output.warnings), 1)
        self.assertIn("Unrecognized rating", output.warnings[0])


if __name__ == "__main__":
    unittest.main()
