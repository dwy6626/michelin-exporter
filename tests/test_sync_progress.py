"""Tests for setup/scrape/sync progress coordination."""

import unittest

from michelin_scraper.application.sync_progress import SyncProgressCoordinator


class _RecordingReporter:
    def __init__(self) -> None:
        self.updates: list[tuple[str, float | None]] = []
        self.logs: list[str] = []
        self.finished_messages: list[str | None] = []

    def update(self, message: str, progress: float | None = None) -> None:
        self.updates.append((message, progress))

    def log(self, message: str) -> None:
        self.logs.append(message)

    def finish(self, message: str | None = None) -> None:
        self.finished_messages.append(message)


class SyncProgressCoordinatorTests(unittest.TestCase):
    def test_progress_includes_setup_scrape_and_sync_stages(self) -> None:
        reporter = _RecordingReporter()
        coordinator = SyncProgressCoordinator(reporter)
        crawl_reporter = coordinator.create_crawl_reporter()

        coordinator.update_setup_progress("setup", completion=0.5)
        crawl_reporter.update("scrape", progress=0.1)
        coordinator.on_page_sync_start(
            page_number=1,
            estimated_total_pages=10,
            total_restaurants_expected=100,
            total_scraped_rows=50,
            rows_on_page=50,
        )
        coordinator.on_sync_row_progress(
            processed_rows=25,
            total_rows=50,
            status="added",
            restaurant_name="Alpha",
        )

        self.assertGreaterEqual(len(reporter.updates), 3)
        setup_progress = reporter.updates[0][1]
        scrape_progress = reporter.updates[1][1]
        sync_progress = reporter.updates[-1][1]
        self.assertIsNotNone(setup_progress)
        self.assertIsNotNone(scrape_progress)
        self.assertIsNotNone(sync_progress)
        assert setup_progress is not None
        assert scrape_progress is not None
        assert sync_progress is not None
        self.assertGreater(scrape_progress, setup_progress)
        self.assertGreaterEqual(sync_progress, scrape_progress)
        self.assertIn("sync page 1/10", reporter.updates[-1][0])

    def test_sync_row_processing_advances_progress_from_completed_rows(self) -> None:
        reporter = _RecordingReporter()
        coordinator = SyncProgressCoordinator(reporter)

        coordinator.update_setup_progress("setup", completion=1.0)
        coordinator.on_page_sync_start(
            page_number=1,
            estimated_total_pages=1,
            total_restaurants_expected=472,
            total_scraped_rows=472,
            rows_on_page=472,
        )
        coordinator.on_sync_row_progress(
            processed_rows=202,
            total_rows=472,
            status="processing",
            restaurant_name="日進客家菜",
        )

        message, progress = reporter.updates[-1]

        self.assertIn("item 203/472", message)
        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertAlmostEqual(progress, 0.05 + (0.95 * (202 / 472)))

    def test_sync_row_progress_does_not_reduce_existing_crawl_estimate(self) -> None:
        reporter = _RecordingReporter()
        coordinator = SyncProgressCoordinator(reporter)
        crawl_reporter = coordinator.create_crawl_reporter()

        coordinator.update_setup_progress("setup", completion=1.0)
        crawl_reporter.update("scrape", progress=0.8)
        crawl_progress = reporter.updates[-1][1]
        coordinator.on_page_sync_start(
            page_number=1,
            estimated_total_pages=10,
            total_restaurants_expected=100,
            total_scraped_rows=10,
            rows_on_page=10,
        )
        coordinator.on_sync_row_progress(
            processed_rows=1,
            total_rows=10,
            status="added",
            restaurant_name="Alpha",
        )

        sync_progress = reporter.updates[-1][1]

        self.assertIsNotNone(crawl_progress)
        self.assertIsNotNone(sync_progress)
        assert crawl_progress is not None
        assert sync_progress is not None
        self.assertEqual(sync_progress, crawl_progress)

if __name__ == "__main__":
    unittest.main()
