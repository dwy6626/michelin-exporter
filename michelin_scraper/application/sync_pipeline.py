"""Async pipeline that overlaps Michelin scraping with Google Maps sync.

Uses asyncio.Queue to coordinate a producer (HTTP scraping in a thread)
with a consumer (async Playwright Maps sync) in the same event loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from ..adapters.google_maps_sync_writer import GoogleMapsRowSyncFailFastError

_log = logging.getLogger(__name__)

_SENTINEL = None  # marks end of stream


@dataclass(frozen=True)
class _ItemWork:
    """A scraped row to be synced."""

    page_number: int
    estimated_total_pages: int | None
    total_restaurants_expected: int | None
    row: dict[str, Any]


@dataclass(frozen=True)
class _PageWork:
    """A page-complete event for checkpointing."""

    page_number: int
    page_url: str
    restaurants_on_page: list[dict[str, Any]]
    next_url: str | None
    next_page_number: int
    estimated_total_pages: int | None
    total_restaurants: int


@dataclass
class PipelineResult:
    """Outcome of the consumer task."""

    consumer_error: BaseException | None = None
    fail_fast_error: GoogleMapsRowSyncFailFastError | None = None


@dataclass
class SyncPipeline:
    """Async pipeline bridging scraper (producer) and Maps sync (consumer).

    Usage::

        pipeline = SyncPipeline(page_handler, max_queue_size=10)
        metrics = await pipeline.run_async(scrape_fn)
        # pipeline.result contains any consumer error

    The *scrape_fn* is called with ``on_item`` and ``on_page`` callbacks that
    enqueue work.  The consumer drains the queue by awaiting the corresponding
    ``SyncPageHandler`` async methods.

    The producer (HTTP scraping) runs in a background thread via the event
    loop's default executor.  The consumer (async Playwright Maps sync) runs
    as an async task in the event loop.
    """

    _page_handler: Any  # SyncPageHandler (avoid circular import)
    _max_queue_size: int = 10
    result: PipelineResult = field(default_factory=PipelineResult)

    # ── producer-side helpers (called from scraper thread) ──────────

    def _make_on_item(self, work_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> Any:  # type: ignore[type-arg]
        """Return a sync callback suitable for ``crawl(on_item=...)``."""

        def on_item(
            page_number: int,
            estimated_total_pages: int | None,
            total_restaurants_expected: int | None,
            row: dict[str, Any],
        ) -> None:
            work = _ItemWork(
                page_number=page_number,
                estimated_total_pages=estimated_total_pages,
                total_restaurants_expected=total_restaurants_expected,
                row=row,
            )
            # Called from producer thread — use thread-safe put.
            asyncio.run_coroutine_threadsafe(work_queue.put(work), loop).result()

        return on_item

    def _make_on_page(self, work_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> Any:  # type: ignore[type-arg]
        """Return a sync callback suitable for ``crawl(on_page=...)``."""

        def on_page(
            page_number: int,
            page_url: str,
            restaurants_on_page: list[dict[str, Any]],
            next_url: str | None,
            next_page_number: int,
            estimated_total_pages: int | None,
            total_restaurants: int,
        ) -> None:
            work = _PageWork(
                page_number=page_number,
                page_url=page_url,
                restaurants_on_page=restaurants_on_page,
                next_url=next_url,
                next_page_number=next_page_number,
                estimated_total_pages=estimated_total_pages,
                total_restaurants=total_restaurants,
            )
            asyncio.run_coroutine_threadsafe(work_queue.put(work), loop).result()

        return on_page

    # ── consumer (runs as async task in the event loop) ─────────────

    async def _consume(self, work_queue: asyncio.Queue) -> None:  # type: ignore[type-arg]
        """Drain the queue, dispatching to SyncPageHandler.

        Runs as an async task so that Playwright async operations stay in
        the event loop thread.
        """

        while True:
            work = await work_queue.get()
            try:
                if work is _SENTINEL:
                    break
                try:
                    if isinstance(work, _ItemWork):
                        await self._page_handler.on_item(
                            work.page_number,
                            work.estimated_total_pages,
                            work.total_restaurants_expected,
                            work.row,
                        )
                    elif isinstance(work, _PageWork):
                        await self._page_handler.on_page(
                            work.page_number,
                            work.page_url,
                            work.restaurants_on_page,
                            work.next_url,
                            work.next_page_number,
                            work.estimated_total_pages,
                            work.total_restaurants,
                        )
                except GoogleMapsRowSyncFailFastError as exc:
                    self.result.fail_fast_error = exc
                    _drain_queue(work_queue)
                    break
                except Exception as exc:  # noqa: BLE001
                    _log.warning("Pipeline consumer error: %s", exc)
                    self.result.consumer_error = exc
                    _drain_queue(work_queue)
                    break
            finally:
                work_queue.task_done()

    # ── orchestrator ────────────────────────────────────────────────

    async def run_async(self, scrape_fn: Any) -> Any:  # type: ignore[type-arg]
        """Run producer and consumer concurrently.

        *scrape_fn* is ``callable(on_item, on_page) -> metrics``.

        The **producer** (scraper / HTTP) runs in a background thread via
        the event loop's default executor.
        The **consumer** (async Playwright / Maps sync) runs as an async
        task in the event loop.
        """

        work_queue: asyncio.Queue[_ItemWork | _PageWork | None] = asyncio.Queue(
            maxsize=self._max_queue_size,
        )

        loop = asyncio.get_event_loop()
        on_item = self._make_on_item(work_queue, loop)
        on_page = self._make_on_page(work_queue, loop)

        # Producer runs in a background thread.
        producer_error: list[BaseException | None] = [None]
        producer_metrics: list[Any] = [None]

        def _produce() -> None:
            try:
                producer_metrics[0] = scrape_fn(on_item, on_page)
            except BaseException as exc:  # noqa: BLE001
                producer_error[0] = exc
            finally:
                # Signal consumer to stop.
                asyncio.run_coroutine_threadsafe(work_queue.put(_SENTINEL), loop).result()

        producer_task = loop.run_in_executor(None, _produce)

        # Consumer runs as an async task in the event loop.
        await self._consume(work_queue)

        await producer_task

        # Re-raise errors in priority order.
        if self.result.fail_fast_error is not None:
            raise self.result.fail_fast_error
        if producer_error[0] is not None:
            raise producer_error[0]
        if self.result.consumer_error is not None:
            raise self.result.consumer_error

        return producer_metrics[0]


def _drain_queue(work_queue: asyncio.Queue) -> None:  # type: ignore[type-arg]
    """Discard remaining items so the producer thread is unblocked."""
    while True:
        try:
            work_queue.get_nowait()
            work_queue.task_done()
        except asyncio.QueueEmpty:
            break
