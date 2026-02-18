"""Progress coordination for scrape + Maps sync runs."""

import threading
from dataclasses import dataclass

from .sync_ports import ProgressOutputPort

_SETUP_WEIGHT = 0.05
_SCRAPE_SYNC_WEIGHT = 0.95


@dataclass(frozen=True)
class SyncPhaseContext:
    """State snapshot used to format sync-progress messages."""

    page_number: int
    estimated_total_pages: int | None
    rows_on_page: int


class SyncProgressCoordinator:
    """Combine setup, scraping, and Maps sync into one monotonic progress stream."""

    def __init__(self, reporter: ProgressOutputPort) -> None:
        self._reporter = reporter
        self._lock = threading.Lock()

        self._setup_completion = 0.0
        self._scrape_sync_completion = 0.0

        self._current_context = SyncPhaseContext(page_number=0, estimated_total_pages=None, rows_on_page=0)
        self._items_synced_total = 0
        self._estimated_total_items: int | None = None

    def create_crawl_reporter(self) -> ProgressOutputPort:
        """Build bridge reporter consumed by the scraping engine."""

        return _CrawlProgressBridge(self)

    def update_setup_progress(self, message: str, completion: float) -> None:
        """Emit setup-phase progress before scraping starts."""

        with self._lock:
            clamped = max(0.0, min(1.0, completion))
            self._setup_completion = max(self._setup_completion, clamped)
            self._reporter.update(message, progress=self._overall_progress())

    def on_scrape_update(self, message: str, progress: float | None) -> None:
        """Handle crawler progress updates and map into overall run progress."""

        with self._lock:
            if progress is not None:
                clamped = max(0.0, min(1.0, progress))
                self._scrape_sync_completion = max(self._scrape_sync_completion, clamped)
            self._reporter.update(message, progress=self._overall_progress())

    def on_scrape_log(self, message: str) -> None:
        """Pass through crawler log output."""

        with self._lock:
            self._reporter.log(message)

    def on_page_sync_start(
        self,
        page_number: int,
        estimated_total_pages: int | None,
        total_restaurants_expected: int | None,
        total_scraped_rows: int,
        rows_on_page: int,
    ) -> None:
        """Update sync context when one page enters Maps sync."""

        with self._lock:
            safe_rows_on_page = max(rows_on_page, 0)
            self._current_context = SyncPhaseContext(
                page_number=page_number,
                estimated_total_pages=estimated_total_pages,
                rows_on_page=safe_rows_on_page,
            )
            if total_restaurants_expected is not None and total_restaurants_expected > 0:
                self._estimated_total_items = total_restaurants_expected
            elif (
                estimated_total_pages is not None
                and estimated_total_pages > 0
                and page_number > 0
                and total_scraped_rows > 0
            ):
                avg = total_scraped_rows / page_number
                self._estimated_total_items = max(
                    int(round(avg * estimated_total_pages)),
                    self._items_synced_total + 1,
                )

    def on_sync_row_progress(
        self,
        processed_rows: int,
        total_rows: int,
        status: str,
        restaurant_name: str,
    ) -> None:
        """Consume row-level Maps sync updates from the writer."""

        with self._lock:
            if total_rows <= 0:
                return

            if status != "processing":
                self._items_synced_total += 1

            page_total = self._current_context.estimated_total_pages or "?"
            estimated = self._estimated_total_items
            display_item = self._items_synced_total + 1 if status == "processing" else self._items_synced_total
            item_str = (
                f"{display_item}/{estimated}"
                if estimated is not None
                else str(display_item)
            )
            visible_name = restaurant_name or "(no name)"
            self._reporter.update(
                (
                    f"sync page {self._current_context.page_number}/{page_total} "
                    f"| item {item_str} "
                    f"| {status} | {visible_name}"
                ),
                progress=self._overall_progress(),
            )

    def finish(self, message: str | None = None) -> None:
        """Finalize progress rendering."""

        with self._lock:
            self._reporter.finish(message)

    def _overall_progress(self) -> float:
        weighted = (
            (_SETUP_WEIGHT * self._setup_completion)
            + (_SCRAPE_SYNC_WEIGHT * self._scrape_sync_completion)
        )
        return max(0.0, min(1.0, weighted))


class _CrawlProgressBridge:
    """Bridge reporter used by scraper engine callbacks."""

    def __init__(self, coordinator: SyncProgressCoordinator) -> None:
        self._coordinator = coordinator

    def update(self, message: str, progress: float | None = None) -> None:
        self._coordinator.on_scrape_update(message=message, progress=progress)

    def log(self, message: str) -> None:
        self._coordinator.on_scrape_log(message)

    def finish(self, message: str | None = None) -> None:
        self._coordinator.finish(message)
