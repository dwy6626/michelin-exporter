"""Page callback implementation for maps sync workflows."""

import asyncio
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from ..adapters.google_maps_sync_writer import GoogleMapsRowSyncFailFastError
from .row_router import LevelRowRouter, UnrecognizedRatingError
from .sync_enums import SyncRowStatus
from .sync_models import SyncAccumulation, SyncItemFailure
from .sync_ports import CheckpointStorePort, LevelSyncWriterPort, SyncOutputPort


class SyncPageHandler:
    """Handle page-by-page scrape output synchronization."""

    def __init__(
        self,
        *,
        start_url: str,
        checkpoint_store: CheckpointStorePort,
        sync_writer: LevelSyncWriterPort,
        row_router: LevelRowRouter,
        accumulation: SyncAccumulation,
        output: SyncOutputPort | None = None,
        debug_sync_failures: bool = False,
        sample_limit: int = 5,
        max_rows_per_page: int = 0,
        on_page_sync_start: Callable[[int, int | None, int | None, int, int], None] | None = None,
        on_page_sync_failures: Callable[[int, Sequence[SyncItemFailure]], Any] | None = None,
    ) -> None:
        self._start_url = start_url
        self._checkpoint_store = checkpoint_store
        self._sync_writer = sync_writer
        self._row_router = row_router
        self._accumulation = accumulation
        self._output = output
        self._debug_sync_failures = debug_sync_failures
        self._sample_limit = sample_limit
        self._max_rows_per_page = max(max_rows_per_page, 0)
        self._on_page_sync_start = on_page_sync_start
        self._on_page_sync_failures = on_page_sync_failures
        self._current_page_number: int = 0
        self._current_estimated_total_pages: int | None = None
        self._page_item_budget: int = self._max_rows_per_page

    async def on_item(
        self, page_number: int, estimated_total_pages: int | None, total_restaurants_expected: int | None, row: dict[str, Any]
    ) -> None:
        """Sync one scraped item to Google Maps immediately after it is fetched."""
        if page_number != self._current_page_number:
            self._current_page_number = page_number
            self._current_estimated_total_pages = estimated_total_pages
            self._page_item_budget = self._max_rows_per_page
            if self._on_page_sync_start is not None:
                total_scraped_rows = sum(self._accumulation.scraped_count_by_level.values())
                self._on_page_sync_start(page_number, estimated_total_pages, total_restaurants_expected, total_scraped_rows, 0)
        self._collect_sample_rows([row])
        try:
            grouped_rows = self._row_router.group_rows_by_level([row])
        except UnrecognizedRatingError as exc:
            failure = SyncItemFailure(
                level_slug="",
                row_key="",
                restaurant_name=exc.restaurant_name,
                reason=f"UnrecognizedRating: {exc.rating!r}",
                attempted_queries=(),
            )
            self._accumulation.failed_items.append(failure)
            if self._output is not None:
                self._output.warn(
                    f"Unrecognized rating {exc.rating!r} for restaurant "
                    f"{exc.restaurant_name!r} — recorded as failure."
                )
            return
        self._increment_scraped_counts(grouped_rows)
        grouped_rows_for_sync, limited_out_count = self._apply_page_sync_row_limit(grouped_rows)
        if limited_out_count > 0:
            self._increment_limit_skip_counts(grouped_rows, grouped_rows_for_sync)

        try:
            for level_slug, rows in grouped_rows_for_sync.items():
                for r in rows:
                    result = await self._sync_writer.sync_row(level_slug, r)
                    if result.status == SyncRowStatus.ADDED:
                        self._accumulation.added_count_by_level[level_slug] += 1
                    elif result.status == SyncRowStatus.SKIPPED:
                        self._accumulation.skipped_count_by_level[level_slug] += 1
                    elif result.status == SyncRowStatus.FAILED:
                        if result.failure:
                            self._accumulation.failed_items.append(result.failure)
                        if self._debug_sync_failures and result.failure:
                            if self._output is not None:
                                self._emit_failure_debug_logs(page_number=self._current_page_number, failed_items=[result.failure])
                            if self._on_page_sync_failures is not None:
                                _cb_result = self._on_page_sync_failures(self._current_page_number, [result.failure])
                                if asyncio.iscoroutine(_cb_result):
                                    await _cb_result
        except GoogleMapsRowSyncFailFastError as exc:
            self._accumulation.failed_items.append(exc.failure)
            failed_items = (exc.failure,)
            if self._debug_sync_failures:
                if self._output is not None:
                    self._emit_failure_debug_logs(page_number=self._current_page_number, failed_items=failed_items)
                if self._on_page_sync_failures is not None:
                    _cb_result = self._on_page_sync_failures(self._current_page_number, failed_items)
                    if asyncio.iscoroutine(_cb_result):
                        await _cb_result
            raise

    async def on_page(
        self,
        page_number: int,
        page_url: str,
        restaurants_on_page: list[dict[str, Any]],
        next_url: str | None,
        next_page_number: int,
        estimated_total_pages: int | None,
        total_restaurants: int,
    ) -> None:
        if next_url:
            self._checkpoint_store.save(
                start_url=self._start_url,
                page_number=page_number,
                page_url=page_url,
                next_url=next_url,
                next_page_number=next_page_number,
                estimated_total_pages=estimated_total_pages,
                total_restaurants=total_restaurants,
                rows_per_level=self._accumulation.scraped_count_by_level,
            )
            return
        self._checkpoint_store.clear()

    def _collect_sample_rows(self, restaurants_on_page: list[dict[str, Any]]) -> None:
        if not restaurants_on_page or len(self._accumulation.sample_rows) >= self._sample_limit:
            return
        remaining_slots = self._sample_limit - len(self._accumulation.sample_rows)
        self._accumulation.sample_rows.extend(restaurants_on_page[:remaining_slots])

    def _increment_scraped_counts(self, grouped_rows: dict[str, list[dict[str, Any]]]) -> None:
        for level_slug, rows in grouped_rows.items():
            self._accumulation.scraped_count_by_level[level_slug] += len(rows)

    def _increment_limit_skip_counts(
        self,
        grouped_rows: dict[str, list[dict[str, Any]]],
        grouped_rows_for_sync: dict[str, list[dict[str, Any]]],
    ) -> None:
        for level_slug, rows in grouped_rows.items():
            deferred_count = len(rows) - len(grouped_rows_for_sync.get(level_slug, []))
            if deferred_count <= 0:
                continue
            self._accumulation.skipped_count_by_level[level_slug] += deferred_count

    def _apply_page_sync_row_limit(
        self,
        grouped_rows: dict[str, list[dict[str, Any]]],
    ) -> tuple[dict[str, list[dict[str, Any]]], int]:
        if self._max_rows_per_page <= 0:
            return grouped_rows, 0

        limited_grouped_rows: dict[str, list[dict[str, Any]]] = {}
        deferred_count = 0
        for level_slug, rows in grouped_rows.items():
            if self._page_item_budget <= 0:
                limited_grouped_rows[level_slug] = []
                deferred_count += len(rows)
                continue
            take_count = min(len(rows), self._page_item_budget)
            limited_grouped_rows[level_slug] = rows[:take_count]
            deferred_count += max(0, len(rows) - take_count)
            self._page_item_budget -= take_count
        return limited_grouped_rows, deferred_count

    def _emit_failure_debug_logs(
        self,
        *,
        page_number: int,
        failed_items: Sequence[SyncItemFailure],
    ) -> None:
        output = self._output
        if output is None:
            return
        reason_counts = Counter(str(failure.reason) for failure in failed_items)
        reason_breakdown = ", ".join(
            f"{reason}={count}" for reason, count in reason_counts.most_common(5)
        )
        output.warn(
            
                f"[debug] Page {page_number} sync failures={len(failed_items)}. "
                f"Reasons: {reason_breakdown}"
            
        )
        for failure in failed_items[:3]:
            attempted_queries = ", ".join(failure.attempted_queries[:3]) or "<none>"
            output.warn(
                
                    "[debug] Failure sample: "
                    f"name='{failure.restaurant_name}', reason='{failure.reason}', "
                    f"queries='{attempted_queries}'"
                
            )
