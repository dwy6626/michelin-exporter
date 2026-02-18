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
        # sync progress equals scrape progress: both are driven by the same
        # scrape_sync_completion value; sync row updates affect message text only
        self.assertGreaterEqual(sync_progress, scrape_progress)
        self.assertIn("sync page 1/10", reporter.updates[-1][0])

if __name__ == "__main__":
    unittest.main()
