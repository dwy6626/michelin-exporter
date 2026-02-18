"""Console presenter for CLI-facing maps sync messages."""

from collections.abc import Mapping

from ..application.sync_models import SyncSummary
from .progress_reporter import ProgressReporter


class ConsoleSyncPresenter:
    """Render sync run events and summary to standard output."""

    LEVEL_INFO = "INFO"
    LEVEL_WARN = "WARN"
    LEVEL_ERROR = "ERROR"

    def _emit(self, level: str, message: str) -> None:
        print(f"[{level}] {message}")

    def _line(self, message: str = "") -> None:
        print(message)

    def _section(self, title: str) -> None:
        self._line()
        self._line(title)
        self._line("-" * len(title))

    def info(self, message: str) -> None:
        self._emit(self.LEVEL_INFO, message)

    def warn(self, message: str) -> None:
        self._emit(self.LEVEL_WARN, message)

    def show_resume(
        self,
        next_page_number: int,
        next_url: str,
        scraped_before_resume: int = 0,
        synced_before_resume: int = 0,
        rows_per_level: Mapping[str, int] | None = None,
    ) -> None:
        self.info(f"Resuming from checkpoint at page {next_page_number}.")
        if scraped_before_resume > 0 or synced_before_resume > 0:
            self.info(
                f"Progress before resume: scraped={scraped_before_resume}, "
                f"synced={synced_before_resume}."
            )
        if rows_per_level:
            level_breakdown = ", ".join(
                f"{slug}={count}" for slug, count in rows_per_level.items() if count > 0
            )
            if level_breakdown:
                self.info(f"Scraped by level: {level_breakdown}.")
        self.info(f"Resume URL: {next_url}")

    def show_interrupted(
        self,
        *,
        scraped_total: int = 0,
        added_total: int = 0,
        failed_total: int = 0,
        skipped_total: int = 0,
    ) -> None:
        self.warn(
            "Interrupted by user. Re-run the same command to resume from the latest checkpoint."
        )
        self.warn(
            
                "Partial sync totals: "
                f"scraped={scraped_total}, "
                f"added(success)={added_total}, "
                f"failed={failed_total}, "
                f"skipped={skipped_total}"
            
        )

    def show_failure(self, message: str) -> None:
        self._emit(self.LEVEL_ERROR, f"Run failed: {message}")

    def create_progress_reporter(self) -> ProgressReporter:
        return ProgressReporter()

    def show_final_results(self, summary: SyncSummary) -> None:
        has_failures = bool(
            summary.failed_items
            or summary.missing_lists
            or summary.metrics.fetch_failures > 0
        )
        if has_failures:
            self.warn("Run completed with unresolved sync issues.")
        else:
            self.info("Run completed successfully.")

        self._section("Run Summary")
        self._line(f"Restaurants : {summary.metrics.total_restaurants}")
        self._line(f"Pages       : {summary.metrics.processed_pages}")
        self._line(f"Fetch errors: {summary.metrics.fetch_failures}")
        self._line(f"Duration    : {summary.elapsed_seconds:.2f}s")

        if summary.sample_rows:
            self._section("Sample Rows (Up to 5)")
            for index, row in enumerate(summary.sample_rows, start=1):
                self._line(
                    f"{index}. {row.get('Name', '')} | "
                    f"{row.get('Rating', '')} | {row.get('City', '')}"
                )

        self._section("Level Sync Summary")
        slug_width = max(len(level_slug) for level_slug, _ in summary.output_targets)
        is_resumed = summary.resumed_synced_count > 0
        if is_resumed:
            total_previously_scraped = sum(summary.resumed_scraped_count_by_level.values())
            total_added = sum(summary.added_count_by_level.values())
            total_failed = len(summary.failed_items)
            total_skipped = sum(summary.skipped_count_by_level.values())
            self._line(
                f"(Resumed run: {summary.resumed_synced_count} previously synced, "
                f"{total_previously_scraped} previously scraped)"
            )
            self._line(
                f"(This session: {total_added} added, "
                f"{total_skipped} skipped, "
                f"{total_failed} failed)"
            )
        failed_count_by_level: dict[str, int] = {}
        for failure in summary.failed_items:
            failed_count_by_level[failure.level_slug] = (
                failed_count_by_level.get(failure.level_slug, 0) + 1
            )
        for level_slug, _label in summary.output_targets:
            scraped = summary.scraped_count_by_level[level_slug]
            prev_scraped = summary.resumed_scraped_count_by_level.get(level_slug, 0)
            session_scraped = scraped - prev_scraped
            added = summary.added_count_by_level[level_slug]
            skipped = summary.skipped_count_by_level[level_slug]
            failed = failed_count_by_level.get(level_slug, 0)
            parts = [
                f"{level_slug.ljust(slug_width)}",
                f"scraped {str(session_scraped).rjust(4)}",
                f"added {str(added).rjust(4)}",
                f"skipped {str(skipped).rjust(4)}",
                f"failed {str(failed).rjust(4)}",
                f"list {summary.list_names_by_level[level_slug]}",
            ]
            if is_resumed:
                parts.insert(1, f"prev {str(prev_scraped).rjust(4)}")
            self._line(" | ".join(parts))

        if summary.failed_items:
            self._section("Row Failures")
            for failure in summary.failed_items[:20]:
                self._line(
                    f"{failure.level_slug} | {failure.restaurant_name} | "
                    f"{failure.reason} | {failure.row_key[:12]}"
                )
            if len(summary.failed_items) > 20:
                remaining = len(summary.failed_items) - 20
                self._line(f"... {remaining} more failures omitted")

        if summary.missing_lists:
            self._section("Missing Lists (Manual Action Required)")
            for missing in summary.missing_lists:
                self._line(
                    f"{missing.level_slug} | {missing.list_name} | "
                    f"skipped rows {missing.skipped_rows}"
                )
