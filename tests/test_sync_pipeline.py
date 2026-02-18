"""Tests for the async scrape-sync pipeline."""

import asyncio
import time
import unittest
from typing import Any

from michelin_scraper.adapters.google_maps_sync_writer import (
    GoogleMapsRowSyncFailFastError,
)
from michelin_scraper.application.sync_models import SyncItemFailure
from michelin_scraper.application.sync_pipeline import SyncPipeline


class _FakePageHandler:
    """Minimal page handler that records calls."""

    def __init__(self, *, item_delay: float = 0.0, item_error: Exception | None = None) -> None:
        self.item_calls: list[tuple[int, int | None, int | None, dict]] = []
        self.page_calls: list[tuple] = []
        self._item_delay = item_delay
        self._item_error = item_error

    async def on_item(
        self,
        page_number: int,
        estimated_total_pages: int | None,
        total_restaurants_expected: int | None,
        row: dict[str, Any],
    ) -> None:
        if self._item_error is not None:
            raise self._item_error
        if self._item_delay > 0:
            await asyncio.sleep(self._item_delay)
        self.item_calls.append((page_number, estimated_total_pages, total_restaurants_expected, row))

    async def on_page(self, *args: Any) -> None:
        self.page_calls.append(args)


class SyncPipelineTests(unittest.TestCase):
    """Unit tests for SyncPipeline."""

    def test_items_are_forwarded_to_page_handler(self) -> None:
        handler = _FakePageHandler()
        pipeline = SyncPipeline(_page_handler=handler, _max_queue_size=5)
        row_a = {"name": "A"}
        row_b = {"name": "B"}

        def scrape_fn(on_item, on_page):
            on_item(1, 3, 100, row_a)
            on_item(1, 3, 100, row_b)

        asyncio.run(pipeline.run_async(scrape_fn))

        self.assertEqual(len(handler.item_calls), 2)
        self.assertEqual(handler.item_calls[0], (1, 3, 100, row_a))
        self.assertEqual(handler.item_calls[1], (1, 3, 100, row_b))

    def test_page_events_are_forwarded(self) -> None:
        handler = _FakePageHandler()
        pipeline = SyncPipeline(_page_handler=handler, _max_queue_size=5)

        def scrape_fn(on_item, on_page):
            on_page(1, "http://example.com/1", [], "http://example.com/2", 2, 5, 100)

        asyncio.run(pipeline.run_async(scrape_fn))

        self.assertEqual(len(handler.page_calls), 1)
        self.assertEqual(
            handler.page_calls[0],
            (1, "http://example.com/1", [], "http://example.com/2", 2, 5, 100),
        )

    def test_ordering_preserved(self) -> None:
        """Items and page events are processed in FIFO order."""
        calls: list[str] = []

        class _OrderTracker:
            async def on_item(self, pn, etp, tre, row):
                calls.append(f"item:{row['name']}")

            async def on_page(self, page_number, *args):
                calls.append(f"page:{page_number}")

        pipeline = SyncPipeline(_page_handler=_OrderTracker(), _max_queue_size=5)

        def scrape_fn(on_item, on_page):
            on_item(1, 3, 100, {"name": "A"})
            on_item(1, 3, 100, {"name": "B"})
            on_page(1, "url1", [], "url2", 2, 3, 100)
            on_item(2, 3, 100, {"name": "C"})

        asyncio.run(pipeline.run_async(scrape_fn))

        self.assertEqual(calls, ["item:A", "item:B", "page:1", "item:C"])

    def test_producer_error_propagates(self) -> None:
        handler = _FakePageHandler()
        pipeline = SyncPipeline(_page_handler=handler, _max_queue_size=5)

        def scrape_fn(on_item, on_page):
            raise RuntimeError("scrape failed")

        with self.assertRaises(RuntimeError):
            asyncio.run(pipeline.run_async(scrape_fn))

    def test_consumer_error_propagates(self) -> None:
        handler = _FakePageHandler(item_error=RuntimeError("sync failed"))
        pipeline = SyncPipeline(_page_handler=handler, _max_queue_size=5)

        def scrape_fn(on_item, on_page):
            on_item(1, 3, 100, {"name": "A"})
            # Give consumer time to process and fail before producing more.
            time.sleep(0.15)
            on_item(1, 3, 100, {"name": "B"})

        with self.assertRaises(RuntimeError):
            asyncio.run(pipeline.run_async(scrape_fn))

    def test_fail_fast_error_propagates(self) -> None:
        failure = SyncItemFailure(
            level_slug="one-star",
            row_key="key",
            restaurant_name="Test",
            reason="FailFast",
            attempted_queries=(),
        )
        handler = _FakePageHandler(
            item_error=GoogleMapsRowSyncFailFastError(
                failure=failure,
                added_count_by_level={},
                skipped_count_by_level={},
            ),
        )
        pipeline = SyncPipeline(_page_handler=handler, _max_queue_size=5)

        def scrape_fn(on_item, on_page):
            on_item(1, 3, 100, {"name": "A"})
            time.sleep(0.15)

        with self.assertRaises(GoogleMapsRowSyncFailFastError):
            asyncio.run(pipeline.run_async(scrape_fn))

    def test_backpressure_blocks_producer(self) -> None:
        """When queue is full the producer blocks until consumer catches up."""
        handler = _FakePageHandler(item_delay=0.05)
        pipeline = SyncPipeline(_page_handler=handler, _max_queue_size=1)

        timestamps: list[float] = []

        def scrape_fn(on_item, on_page):
            for i in range(4):
                on_item(1, 1, 4, {"name": f"R{i}"})
                timestamps.append(time.monotonic())

        asyncio.run(pipeline.run_async(scrape_fn))

        self.assertEqual(len(handler.item_calls), 4)
        # With queue size 1 and 50ms consumer delay, producer can't rush through.
        total_span = timestamps[-1] - timestamps[0]
        self.assertGreater(total_span, 0.05)

    def test_returns_scrape_metrics(self) -> None:
        handler = _FakePageHandler()
        pipeline = SyncPipeline(_page_handler=handler, _max_queue_size=5)
        expected_metrics = {"pages": 3, "restaurants": 50}

        def scrape_fn(on_item, on_page):
            return expected_metrics

        result = asyncio.run(pipeline.run_async(scrape_fn))

        self.assertEqual(result, expected_metrics)

    def test_keyboard_interrupt_propagates(self) -> None:
        handler = _FakePageHandler()
        pipeline = SyncPipeline(_page_handler=handler, _max_queue_size=5)

        def scrape_fn(on_item, on_page):
            on_item(1, 1, 10, {"name": "A"})
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            asyncio.run(pipeline.run_async(scrape_fn))


if __name__ == "__main__":
    unittest.main()
