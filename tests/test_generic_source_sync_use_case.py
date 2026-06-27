"""Tests for the generic source-to-Maps sync boundary."""

import inspect
import unittest
from typing import Any
from unittest.mock import Mock, patch

from michelin_scraper.application import sync_use_case
from michelin_scraper.application.source_models import (
    SourceBucket,
    SourcePlan,
    SourceRunHandlers,
    SourceRunResult,
)
from michelin_scraper.application.sync_enums import SyncRowStatus
from michelin_scraper.application.sync_models import ScrapeSyncCommand, SyncRowResult


class _NoOpReporter:
    def update(self, message: str, progress: float | None = None) -> None:
        del message, progress

    def log(self, message: str) -> None:
        del message

    def finish(self, message: str | None = None) -> None:
        del message


class _FakeOutput:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.failures: list[str] = []
        self.final_summaries: list[Any] = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def show_resume(self, next_page_number: int, next_url: str, **kwargs: object) -> None:
        del next_page_number, next_url, kwargs

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
        self.failures.append(message)

    def show_final_results(self, summary: object) -> None:
        self.final_summaries.append(summary)

    def create_progress_reporter(self) -> _NoOpReporter:
        return _NoOpReporter()


class _RecordingWriter:
    def __init__(self) -> None:
        self.list_names_by_level: dict[str, str] = {}
        self.missing_row_counts_by_level: dict[str, int] = {}
        self.synced_rows: list[tuple[str, dict[str, Any]]] = []

    async def initialize_run(self, *, scope_name: str, level_slugs: tuple[str, ...]) -> None:
        self.list_names_by_level = {
            level_slug: f"{scope_name}|{level_slug}" for level_slug in level_slugs
        }
        self.missing_row_counts_by_level = {level_slug: 0 for level_slug in level_slugs}

    async def sync_row(self, level_slug: str, row: dict[str, Any]) -> SyncRowResult:
        self.synced_rows.append((level_slug, dict(row)))
        return SyncRowResult(status=SyncRowStatus.ADDED)

    async def sync_rows_by_level(self, rows_by_level: object) -> object:
        del rows_by_level
        raise AssertionError("generic streamed source should sync rows through sync_row")

    async def finalize_run(self) -> None:
        return


class _FakeSourceAdapter:
    def prepare(self, command: ScrapeSyncCommand, output: object) -> SourcePlan:
        del command, output
        return SourcePlan(
            source_id="fake",
            scope_name="Imported Places",
            checkpoint_scope="imported-places",
            start_url="source://fake/start",
            buckets=(SourceBucket(slug="imported", label="Imported", badge="Imported"),),
        )

    def run(
        self,
        *,
        command: ScrapeSyncCommand,
        plan: SourcePlan,
        handlers: SourceRunHandlers,
    ) -> SourceRunResult:
        del command, plan
        row = {"Name": "Alpha", "City": "Taipei"}
        handlers.on_item(1, 1, 1, "imported", row)
        handlers.on_page(1, "source://fake/start", [row], None, 2, 1, 1)
        return SourceRunResult(total_rows=1, processed_pages=1)

    def group_local_rows_by_bucket(
        self,
        *,
        rows: list[dict[str, Any]],
        bucket_slugs: tuple[str, ...],
    ) -> dict[str, list[dict[str, Any]]]:
        del rows, bucket_slugs
        raise AssertionError("not used by streamed fake source")


class GenericSourceSyncUseCaseTests(unittest.TestCase):
    @patch("michelin_scraper.application.sync_use_case._create_source_adapter")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_sync_core_runs_from_generic_source_adapter(
        self,
        mock_create_sync_writer: Mock,
        mock_create_source_adapter: Mock,
    ) -> None:
        writer = _RecordingWriter()
        mock_create_sync_writer.return_value = writer
        mock_create_source_adapter.return_value = _FakeSourceAdapter()
        output = _FakeOutput()
        command = ScrapeSyncCommand(
            target="tokyo",
            google_user_data_dir="/tmp/profile",
            levels=("one-star",),
        )

        exit_code = sync_use_case.run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 0)
        self.assertEqual(writer.synced_rows, [("imported", {"Name": "Alpha", "City": "Taipei"})])
        self.assertEqual(writer.list_names_by_level, {"imported": "Imported Places|imported"})
        self.assertEqual(output.failures, [])
        self.assertEqual(len(output.final_summaries), 1)
        summary = output.final_summaries[0]
        self.assertEqual(summary.output_targets, (("imported", "Imported"),))
        self.assertEqual(summary.scraped_count_by_level, {"imported": 1})

    def test_sync_use_case_no_longer_imports_michelin_catalog_or_scraping_modules(self) -> None:
        source = inspect.getsource(sync_use_case)

        self.assertNotIn("from ..catalog", source)
        self.assertNotIn("from ..scraping", source)
        self.assertNotIn("resolve_target", source)
        self.assertNotIn("crawl(", source)


if __name__ == "__main__":
    unittest.main()
