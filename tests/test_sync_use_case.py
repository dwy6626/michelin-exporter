"""Tests for maps sync use-case behavior."""

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from michelin_scraper.adapters.google_maps_driver import (
    GoogleMapsAuthRequiredError,
    GoogleMapsListAlreadyExistsError,
)
from michelin_scraper.adapters.google_maps_sync_writer import GoogleMapsRowSyncFailFastError
from michelin_scraper.application.html_redaction import redact_html_text
from michelin_scraper.application.sync_enums import SyncRowStatus
from michelin_scraper.application.sync_models import (
    ScrapeSyncCommand,
    SyncBatchResult,
    SyncItemFailure,
    SyncRowResult,
)
from michelin_scraper.application.sync_use_case import (
    AUTH_REQUIRED_EXIT_CODE,
    run_scrape_sync,
)
from michelin_scraper.config import (
    CRAWL_DELAY_OPTION_FLAGS,
    MAPS_DELAY_OPTION_FLAGS,
    MAX_SAVE_RETRIES_OPTION_FLAGS,
)
from michelin_scraper.domain import ScrapeRunMetrics


class _NoOpReporter:
    def update(self, message: str, progress: float | None = None) -> None:
        del message, progress

    def log(self, message: str) -> None:
        del message

    def finish(self, message: str | None = None) -> None:
        del message


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


class _FakeOutput:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.failures: list[str] = []
        self.interrupted_payloads: list[dict[str, int]] = []
        self.final_result_calls = 0

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
        self.interrupted_payloads.append(
            {
                "scraped_total": scraped_total,
                "added_total": added_total,
                "failed_total": failed_total,
                "skipped_total": skipped_total,
            }
        )

    def show_failure(self, message: str) -> None:
        self.failures.append(message)

    def show_final_results(self, summary: object) -> None:
        del summary
        self.final_result_calls += 1

    def create_progress_reporter(self) -> _NoOpReporter:
        return _NoOpReporter()


class _FakeOutputWithRecordingReporter(_FakeOutput):
    def __init__(self) -> None:
        super().__init__()
        self.reporter = _RecordingReporter()

    def create_progress_reporter(self) -> _RecordingReporter:
        return self.reporter


class _AuthFailWriter:
    list_names_by_level: dict[str, str] = {}
    missing_row_counts_by_level: dict[str, int] = {}

    async def initialize_run(self, *, scope_name: str, level_slugs: tuple[str, ...]) -> None:
        del scope_name, level_slugs
        raise GoogleMapsAuthRequiredError("auth required for tests")

    async def sync_row(self, level_slug: str, row: dict[str, Any]) -> None:
        del level_slug, row
        return

    async def sync_rows_by_level(self, rows_by_level: object) -> object:
        del rows_by_level
        return None

    async def finalize_run(self) -> None:
        return


class _BrowserMissingWriter:
    list_names_by_level: dict[str, str] = {}
    missing_row_counts_by_level: dict[str, int] = {}

    async def initialize_run(self, *, scope_name: str, level_slugs: tuple[str, ...]) -> None:
        del scope_name, level_slugs
        raise RuntimeError(
            
                "BrowserType.launch_persistent_context: Executable doesn't exist at "
                "/some/path/chrome"
            
        )

    async def sync_row(self, level_slug: str, row: dict[str, Any]) -> None:
        del level_slug, row
        return

    async def sync_rows_by_level(self, rows_by_level: object) -> object:
        del rows_by_level
        return None

    async def finalize_run(self) -> None:
        return


class _BrowserMissingWriterWithDebugHtml(_BrowserMissingWriter):
    def __init__(self) -> None:
        self.debug_html_paths: list[Path] = []

    async def dump_debug_html(self, path: Path) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html><body>debug</body></html>", encoding="utf-8")
        self.debug_html_paths.append(path)
        return True


class _NoOpWriter:
    list_names_by_level: dict[str, str] = {"one-star": "Tokyo Michelin ⭐"}
    missing_row_counts_by_level: dict[str, int] = {"one-star": 0}

    async def initialize_run(self, *, scope_name: str, level_slugs: tuple[str, ...]) -> None:
        del scope_name, level_slugs
        return

    async def sync_row(self, level_slug: str, row: dict[str, Any]) -> SyncRowResult:
        del level_slug, row
        return SyncRowResult(status=SyncRowStatus.ADDED)

    async def sync_rows_by_level(self, rows_by_level: object) -> object:
        del rows_by_level
        return None

    async def finalize_run(self) -> None:
        return


class _ListExistsWriter(_NoOpWriter):
    async def initialize_run(self, *, scope_name: str, level_slugs: tuple[str, ...]) -> None:
        del scope_name, level_slugs
        raise GoogleMapsListAlreadyExistsError(
            "List already exists at startup: 臺灣 餐廳 米其林 星級 (level=stars)"
        )


class _FailingWriterWithOneRow(_NoOpWriter):
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

    async def sync_rows_by_level(self, rows_by_level: object) -> SyncBatchResult:
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


class _FailingWriterWithDebugHtml(_FailingWriterWithOneRow):
    def __init__(self) -> None:
        self.debug_html_paths: list[Path] = []

    async def dump_debug_html(self, path: Path) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            (
                "<html><body>"
                "contact=qa.user@example.com "
                "cookie: SID=abc123 "
                "url=https://maps.google.com/?token=secrettoken "
                "profile=/Users/testuser/Projects/michelin-exporter"
                "</body></html>"
            ),
            encoding="utf-8",
        )
        self.debug_html_paths.append(path)
        return True


class _ProgressCallbackWriter(_NoOpWriter):
    def __init__(self) -> None:
        self._callback: Any = None

    async def sync_row(self, level_slug: str, row: dict[str, Any]) -> SyncRowResult:
        if self._callback is not None:
            self._callback(1, 1, "added", str(row.get("Name", "")))
        return SyncRowResult(status=SyncRowStatus.ADDED)

    def set_row_progress_callback(self, callback: Any) -> None:
        self._callback = callback

    async def sync_rows_by_level(self, rows_by_level: object) -> SyncBatchResult:
        rows = rows_by_level["one-star"]  # type: ignore[index]
        for row in rows:
            await self.sync_row("one-star", row)
        return SyncBatchResult(
            added_count_by_level={"one-star": len(rows)},
            skipped_count_by_level={"one-star": 0},
            failed_items=(),
        )


class _FinalizeInterruptedWriter(_NoOpWriter):
    async def finalize_run(self) -> None:
        raise KeyboardInterrupt()


class _FastShutdownWriter(_NoOpWriter):
    def __init__(self) -> None:
        self.fast_shutdown_requested = False

    def request_fast_shutdown(self) -> None:
        self.fast_shutdown_requested = True


class _FastShutdownWriterWithDebugHtml(_FastShutdownWriter):
    def __init__(self) -> None:
        super().__init__()
        self.debug_html_paths: list[Path] = []

    async def dump_debug_html(self, path: Path) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html><body>interrupt debug</body></html>", encoding="utf-8")
        self.debug_html_paths.append(path)
        return True


class _ProbeRowsWriter(_NoOpWriter):
    def __init__(self) -> None:
        self.list_names_by_level: dict[str, str] = {}
        self.missing_row_counts_by_level: dict[str, int] = {}
        self.rows_by_level_calls: list[dict[str, list[dict[str, Any]]]] = []

    async def initialize_run(self, *, scope_name: str, level_slugs: tuple[str, ...]) -> None:
        self.list_names_by_level = {
            level_slug: f"{scope_name}|{level_slug}" for level_slug in level_slugs
        }
        self.missing_row_counts_by_level = {level_slug: 0 for level_slug in level_slugs}

    async def sync_rows_by_level(self, rows_by_level: object) -> SyncBatchResult:
        normalized_rows_by_level = {
            level_slug: [dict(row) for row in rows]
            for level_slug, rows in rows_by_level.items()  # type: ignore[union-attr]
        }
        self.rows_by_level_calls.append(normalized_rows_by_level)
        added_count_by_level = {level_slug: 0 for level_slug in normalized_rows_by_level}
        skipped_count_by_level = {
            level_slug: len(rows)
            for level_slug, rows in normalized_rows_by_level.items()
        }
        return SyncBatchResult(
            added_count_by_level=added_count_by_level,
            skipped_count_by_level=skipped_count_by_level,
            failed_items=(),
        )


class _SandboxProbeRowsWriter(_ProbeRowsWriter):
    def __init__(self) -> None:
        super().__init__()
        self.list_created_by_level: dict[str, bool] = {}

    async def initialize_run(self, *, scope_name: str, level_slugs: tuple[str, ...]) -> None:
        del scope_name
        self.list_names_by_level = {
            level_slug: f"[TEST] Tokyo Michelin {level_slug}" for level_slug in level_slugs
        }
        self.missing_row_counts_by_level = {level_slug: 0 for level_slug in level_slugs}
        self.list_created_by_level = {level_slug: True for level_slug in level_slugs}


class _FailFastWriterWithDebugHtml(_NoOpWriter):
    def __init__(self) -> None:
        self.debug_html_paths: list[Path] = []

    async def sync_row(self, level_slug: str, row: dict[str, Any]) -> SyncRowResult:
        del level_slug, row
        failure = SyncItemFailure(
            level_slug="one-star",
            row_key="one-star::alpha",
            restaurant_name="Alpha",
            reason="NoteWriteFailed: unable to verify note",
            attempted_queries=("Alpha Tokyo", "Alpha"),
        )
        raise GoogleMapsRowSyncFailFastError(
            failure=failure,
            added_count_by_level={"one-star": 0},
            skipped_count_by_level={"one-star": 0},
        )

    async def sync_rows_by_level(self, rows_by_level: object) -> SyncBatchResult:
        del rows_by_level
        failure = SyncItemFailure(
            level_slug="one-star",
            row_key="one-star::alpha",
            restaurant_name="Alpha",
            reason="NoteWriteFailed: unable to verify note",
            attempted_queries=("Alpha Tokyo", "Alpha"),
        )
        raise GoogleMapsRowSyncFailFastError(
            failure=failure,
            added_count_by_level={"one-star": 0},
            skipped_count_by_level={"one-star": 0},
        )

    async def dump_debug_html(self, path: Path) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html><body>fail-fast debug</body></html>", encoding="utf-8")
        self.debug_html_paths.append(path)
        return True


class SyncUseCaseTests(unittest.TestCase):
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_returns_auth_required_exit_code(
        self,
        mock_create_sync_writer: Mock,
    ) -> None:
        mock_create_sync_writer.return_value = _AuthFailWriter()
        output = _FakeOutput()
        command = ScrapeSyncCommand(
            target="taiwan",
            google_user_data_dir="~/.michelin-gmaps-profile",
            levels=("one-star",),
            dry_run=False,
        )

        exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, AUTH_REQUIRED_EXIT_CODE)
        self.assertEqual(output.failures, [])
        self.assertTrue(any("auth required" in message for message in output.warnings))

    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_shows_setup_guide_for_playwright_missing_browser(
        self,
        mock_create_sync_writer: Mock,
    ) -> None:
        mock_create_sync_writer.return_value = _BrowserMissingWriter()
        output = _FakeOutput()
        command = ScrapeSyncCommand(
            target="tokyo",
            google_user_data_dir="~/.michelin-gmaps-profile",
            levels=("one-star",),
            dry_run=False,
        )

        exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(output.failures), 1)
        self.assertIn("uv run playwright install chromium", output.failures[0])

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_fails_before_crawl_when_required_list_exists_at_startup(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        mock_create_sync_writer.return_value = _ListExistsWriter()
        output = _FakeOutput()
        command = ScrapeSyncCommand(
            target="taiwan",
            google_user_data_dir="~/.michelin-gmaps-profile",
            levels=("stars",),
            dry_run=False,
        )

        exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 1)
        self.assertEqual(
            output.failures,
            ["List already exists at startup: 臺灣 餐廳 米其林 星級 (level=stars)"],
        )
        mock_crawl.assert_not_called()

    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_writes_debug_html_on_runtime_failure(
        self,
        mock_create_sync_writer: Mock,
    ) -> None:
        writer = _BrowserMissingWriterWithDebugHtml()
        mock_create_sync_writer.return_value = writer
        output = _FakeOutput()

        with tempfile.TemporaryDirectory() as temp_dir:
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                dry_run=False,
                state_dir=temp_dir,
            )
            exit_code = run_scrape_sync(command=command, output=output)

            self.assertEqual(exit_code, 1)
            self.assertEqual(len(writer.debug_html_paths), 1)
            debug_html_path = writer.debug_html_paths[0]
            self.assertTrue(debug_html_path.exists())
            self.assertEqual(debug_html_path.suffix, ".html")
            self.assertIn("debug", debug_html_path.parts)
            self.assertTrue(
                any(
                    "Debug HTML snapshot written to:" in warning
                    and str(debug_html_path) in warning
                    for warning in output.warnings
                )
            )

    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_records_debug_fixture_artifacts_when_configured(
        self,
        mock_create_sync_writer: Mock,
    ) -> None:
        writer = _BrowserMissingWriterWithDebugHtml()
        mock_create_sync_writer.return_value = writer
        output = _FakeOutput()

        with tempfile.TemporaryDirectory() as temp_dir:
            fixtures_dir = Path(temp_dir) / "recorded-fixtures"
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                dry_run=False,
                state_dir=temp_dir,
                record_fixtures_dir=str(fixtures_dir),
            )
            exit_code = run_scrape_sync(command=command, output=output)

            self.assertEqual(exit_code, 1)
            self.assertEqual(len(writer.debug_html_paths), 1)
            debug_html_path = writer.debug_html_paths[0]
            fixture_base = debug_html_path.stem
            fixture_html_path = fixtures_dir / f"{fixture_base}.html"
            fixture_metadata_path = fixtures_dir / f"{fixture_base}.metadata.json"
            self.assertTrue(fixture_html_path.exists())
            self.assertTrue(fixture_metadata_path.exists())
            fixture_metadata = fixture_metadata_path.read_text(encoding="utf-8")
            self.assertIn('"source": "debug-sync-snapshot"', fixture_metadata)
            self.assertIn('"sanitized": true', fixture_metadata)
            self.assertTrue(
                any(
                    warning.startswith("Recorded debug fixture HTML:")
                    for warning in output.warnings
                )
            )

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_captures_deidentified_debug_html_on_page_failure(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        writer = _FailingWriterWithDebugHtml()
        mock_create_sync_writer.return_value = writer

        def fake_crawl(*_: object, **kwargs: Any) -> ScrapeRunMetrics:
            on_page = kwargs["on_page"]
            on_item = kwargs["on_item"]
            restaurants = [{"Name": "Alpha", "Rating": "1 Star", "City": "Tokyo"}]
            for row in restaurants:
                on_item(1, 10, 1, row)
            on_page(
                1,
                "https://example.com/page/1",
                restaurants,
                None,
                2,
                10,
                1,
            )
            return ScrapeRunMetrics(total_restaurants=1, processed_pages=1)

        mock_crawl.side_effect = fake_crawl
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                dry_run=False,
                state_dir=temp_dir,
                debug_sync_failures=True,
            )

            exit_code = run_scrape_sync(command=command, output=output)

            self.assertEqual(exit_code, 1)
            self.assertEqual(len(writer.debug_html_paths), 1)
            debug_html_path = writer.debug_html_paths[0]
            self.assertTrue(debug_html_path.exists())
            debug_html = debug_html_path.read_text(encoding="utf-8")
            self.assertNotIn("qa.user@example.com", debug_html)
            self.assertNotIn("SID=abc123", debug_html)
            self.assertNotIn("token=secrettoken", debug_html)
            self.assertNotIn("/Users/testuser", debug_html)
            self.assertIn("<redacted-email>", debug_html)
            self.assertIn("SID=<redacted>", debug_html)
            self.assertIn("token=<redacted>", debug_html)
            self.assertIn("/Users/<redacted-user>", debug_html)
            self.assertTrue(
                any(
                    "Debug HTML snapshot written to:" in warning and "(de-identified)" in warning
                    for warning in output.warnings
                )
            )

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_fail_fast_writes_failure_report_and_stops_without_summary(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        writer = _FailFastWriterWithDebugHtml()
        mock_create_sync_writer.return_value = writer

        def fake_crawl(*_: object, **kwargs: Any) -> ScrapeRunMetrics:
            on_page = kwargs["on_page"]
            on_item = kwargs["on_item"]
            restaurants = [{"Name": "Alpha", "Rating": "1 Star", "City": "Tokyo"}]
            for row in restaurants:
                on_item(1, 10, 1, row)
            on_page(
                1,
                "https://example.com/page/1",
                restaurants,
                "https://example.com/page/2",
                2,
                10,
                1,
            )
            return ScrapeRunMetrics(total_restaurants=1, processed_pages=1)

        mock_crawl.side_effect = fake_crawl
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                dry_run=False,
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

            self.assertEqual(exit_code, 1)
            self.assertEqual(output.final_result_calls, 0)
            self.assertEqual(len(output.failures), 1)
            self.assertIn("Fail-fast policy stopped sync immediately.", output.failures[0])
            self.assertEqual(len(writer.debug_html_paths), 1)
            self.assertTrue(
                any(
                    warning.startswith("Failure report written to:")
                    for warning in output.warnings
                )
            )
            error_report_path = Path(temp_dir) / "tokyo-maps-sync-errors.jsonl"
            self.assertTrue(error_report_path.exists())
            error_report_text = error_report_path.read_text(encoding="utf-8")
            self.assertIn("NoteWriteFailed", error_report_text)
            self.assertIn("one-star::alpha", error_report_text)

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_reports_partial_totals_on_interrupt(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        mock_create_sync_writer.return_value = _NoOpWriter()
        mock_crawl.side_effect = KeyboardInterrupt()
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                dry_run=False,
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

            self.assertEqual(exit_code, 130)
            self.assertEqual(len(output.interrupted_payloads), 1)
            self.assertEqual(
                output.interrupted_payloads[0],
                {
                    "scraped_total": 0,
                    "added_total": 0,
                    "failed_total": 0,
                    "skipped_total": 0,
                },
            )

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_saves_checkpoint_on_interrupt(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        mock_create_sync_writer.return_value = _NoOpWriter()

        def fake_crawl(*_: object, **kwargs: Any) -> object:
            on_interrupt = kwargs["on_interrupt"]
            on_interrupt("https://example.com/page/3", 3, 10, 120)
            raise KeyboardInterrupt()

        mock_crawl.side_effect = fake_crawl
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                dry_run=False,
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

            self.assertEqual(exit_code, 130)
            checkpoint_paths = list(Path(temp_dir).glob("*-michelin-checkpoint.json"))
            self.assertEqual(len(checkpoint_paths), 1)
            payload = json.loads(checkpoint_paths[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["last_page_number"], 3)
            self.assertEqual(payload["last_page_url"], "https://example.com/page/3")
            self.assertEqual(payload["next_url"], "https://example.com/page/3")
            self.assertEqual(payload["next_page_number"], 3)
            self.assertEqual(payload["estimated_total_pages"], 10)
            self.assertEqual(payload["total_restaurants"], 120)
            self.assertEqual(payload["rows_per_level"], {"one-star": 0})

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_writes_partial_failure_report_on_interrupt(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        mock_create_sync_writer.return_value = _FailingWriterWithOneRow()

        def fake_crawl(*_: object, **kwargs: Any) -> object:
            on_page = kwargs["on_page"]
            on_item = kwargs["on_item"]
            restaurants = [{"Name": "Alpha", "Rating": "1 Star", "City": "Tokyo"}]
            for row in restaurants:
                on_item(1, 10, 1, row)
            on_page(
                1,
                "https://example.com/page/1",
                restaurants,
                "https://example.com/page/2",
                2,
                10,
                1,
            )
            raise KeyboardInterrupt()

        mock_crawl.side_effect = fake_crawl
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                dry_run=False,
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

            self.assertEqual(exit_code, 130)
            self.assertTrue(
                any(
                    warning.startswith("Partial failure report written to:")
                    for warning in output.warnings
                )
            )
            self.assertTrue(
                any(
                    warning == "Partial failure reasons: PlaceNotFound=1"
                    for warning in output.warnings
                )
            )
            error_report_path = Path(temp_dir) / "tokyo-maps-sync-errors.jsonl"
            self.assertTrue(error_report_path.exists())
            report_body = error_report_path.read_text(encoding="utf-8")
            self.assertIn("PlaceNotFound", report_body)
            self.assertIn("one-star::alpha", report_body)

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_progress_includes_sync_row_updates(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        mock_create_sync_writer.return_value = _ProgressCallbackWriter()

        def fake_crawl(*_: object, **kwargs: Any) -> ScrapeRunMetrics:
            progress_reporter = kwargs["progress_reporter"]
            on_page = kwargs["on_page"]
            on_item = kwargs["on_item"]
            restaurants = [{"Name": "Alpha", "Rating": "1 Star", "City": "Tokyo"}]
            progress_reporter.update("page 1/10 | item 1/1", progress=0.1)
            for row in restaurants:
                on_item(1, 10, 1, row)
            on_page(
                1,
                "https://example.com/page/1",
                restaurants,
                None,
                1,
                10,
                1,
            )
            return ScrapeRunMetrics(total_restaurants=1, processed_pages=1)

        mock_crawl.side_effect = fake_crawl
        output = _FakeOutputWithRecordingReporter()
        with tempfile.TemporaryDirectory() as temp_dir:
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                dry_run=False,
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 0)
        rendered_messages = [message for message, _progress in output.reporter.updates]
        self.assertTrue(
            any(message.startswith("sync page 1/10 | item 1/1") for message in rendered_messages)
        )

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_debug_timing_warning_uses_cli_option_names(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        mock_create_sync_writer.return_value = _NoOpWriter()
        mock_crawl.return_value = ScrapeRunMetrics(total_restaurants=0, processed_pages=0)
        output = _FakeOutput()
        command = ScrapeSyncCommand(
            target="tokyo",
            google_user_data_dir="~/.michelin-gmaps-profile",
            levels=("one-star",),
            dry_run=False,
            debug_sync_failures=True,
            sleep_seconds=1.25,
            sync_delay_seconds=0.75,
            max_save_retries=2,
        )

        exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 0)
        self.assertIn(
            (
                "[debug] Effective timing: "
                f"{CRAWL_DELAY_OPTION_FLAGS[0]}=1.25, "
                f"{MAPS_DELAY_OPTION_FLAGS[0]}=0.75, "
                f"{MAX_SAVE_RETRIES_OPTION_FLAGS[0]}=2"
            ),
            output.warnings,
        )

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_passes_max_pages_and_warns_probe_mode(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        mock_create_sync_writer.return_value = _NoOpWriter()

        def fake_crawl(*_: object, **kwargs: Any) -> ScrapeRunMetrics:
            self.assertEqual(kwargs["max_pages"], 2)
            return ScrapeRunMetrics(total_restaurants=0, processed_pages=0)

        mock_crawl.side_effect = fake_crawl
        output = _FakeOutput()
        command = ScrapeSyncCommand(
            target="tokyo",
            google_user_data_dir="~/.michelin-gmaps-profile",
            levels=("one-star",),
            dry_run=False,
            maps_probe_only=True,
            max_pages=2,
            max_rows_per_page=3,
        )

        exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 0)
        self.assertTrue(
            any("maps-probe-only mode enabled" in warning for warning in output.warnings)
        )

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_sandbox_overrides_prefix_checks_and_default_max_pages(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        mock_create_sync_writer.return_value = _NoOpWriter()

        def fake_crawl(*_: object, **kwargs: Any) -> ScrapeRunMetrics:
            self.assertEqual(kwargs["max_pages"], 1)
            return ScrapeRunMetrics(total_restaurants=0, processed_pages=0)

        mock_crawl.side_effect = fake_crawl
        output = _FakeOutput()
        command = ScrapeSyncCommand(
            target="tokyo",
            google_user_data_dir="~/.michelin-gmaps-profile",
            levels=("one-star",),
            dry_run=False,
            sandbox=True,
            list_name_prefix="prod-",
            ignore_existing_lists_check=False,
        )

        exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 0)
        called_command = mock_create_sync_writer.call_args.args[0]
        self.assertEqual(called_command.list_name_prefix, "[TEST] ")
        self.assertTrue(called_command.ignore_existing_lists_check)
        self.assertEqual(called_command.max_pages, 1)
        self.assertTrue(
            any("sandbox mode default applied: max-pages=1" in warning for warning in output.warnings)
        )

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_sandbox_preserves_explicit_max_pages(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        mock_create_sync_writer.return_value = _NoOpWriter()

        def fake_crawl(*_: object, **kwargs: Any) -> ScrapeRunMetrics:
            self.assertEqual(kwargs["max_pages"], 4)
            return ScrapeRunMetrics(total_restaurants=0, processed_pages=0)

        mock_crawl.side_effect = fake_crawl
        output = _FakeOutput()
        command = ScrapeSyncCommand(
            target="tokyo",
            google_user_data_dir="~/.michelin-gmaps-profile",
            levels=("one-star",),
            dry_run=False,
            sandbox=True,
            max_pages=4,
            max_pages_specified=True,
        )

        exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 0)
        called_command = mock_create_sync_writer.call_args.args[0]
        self.assertEqual(called_command.max_pages, 4)
        self.assertTrue(called_command.max_pages_specified)
        self.assertFalse(
            any("sandbox mode default applied: max-pages=1" in warning for warning in output.warnings)
        )

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_sandbox_reports_created_test_lists(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        writer = _SandboxProbeRowsWriter()
        mock_create_sync_writer.return_value = writer
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            probe_file = Path(temp_dir) / "probe.jsonl"
            probe_file.write_text('{"Name":"Alpha","City":"Tokyo","Rating":"1 Star"}\n', encoding="utf-8")
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                dry_run=False,
                maps_probe_rows_file=str(probe_file),
                sandbox=True,
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 0)
        mock_crawl.assert_not_called()
        self.assertTrue(
            any("sandbox mode created test lists." in warning for warning in output.warnings)
        )
        self.assertTrue(
            any("[TEST] Tokyo Michelin one-star" in warning for warning in output.warnings)
        )

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case.resolve_listing_scope_name")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_uses_listing_scope_name_when_language_is_zh_tw(
        self,
        mock_create_sync_writer: Mock,
        mock_resolve_listing_scope_name: Mock,
        mock_crawl: Mock,
    ) -> None:
        writer = _ProbeRowsWriter()
        mock_create_sync_writer.return_value = writer
        mock_resolve_listing_scope_name.return_value = "Language Scope"
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            probe_file = Path(temp_dir) / "probe.jsonl"
            probe_file.write_text(
                '{"Name":"Alpha","City":"Tokyo","Rating":"1 Star"}\n',
                encoding="utf-8",
            )
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                language="zh-tw",
                dry_run=False,
                maps_probe_rows_file=str(probe_file),
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 0)
        mock_crawl.assert_not_called()
        self.assertEqual(writer.list_names_by_level["one-star"], "Language Scope|one-star")

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case.resolve_listing_scope_name")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_prefers_traditional_scope_label_when_listing_scope_contains_ascii_letters(
        self,
        mock_create_sync_writer: Mock,
        mock_resolve_listing_scope_name: Mock,
        mock_crawl: Mock,
    ) -> None:
        writer = _ProbeRowsWriter()
        mock_create_sync_writer.return_value = writer
        mock_resolve_listing_scope_name.return_value = "Tainan 餐廳"
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            probe_file = Path(temp_dir) / "probe.jsonl"
            probe_file.write_text(
                '{"Name":"Alpha","City":"Tainan","Rating":"1 Star"}\n',
                encoding="utf-8",
            )
            command = ScrapeSyncCommand(
                target="tainan",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                language="zh-tw",
                dry_run=False,
                maps_probe_rows_file=str(probe_file),
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 0)
        mock_crawl.assert_not_called()
        self.assertEqual(writer.list_names_by_level["one-star"], "\u81fa\u5357|one-star")

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_uses_maps_probe_rows_file_without_crawl(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        writer = _ProbeRowsWriter()
        mock_create_sync_writer.return_value = writer
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            probe_file = Path(temp_dir) / "probe.jsonl"
            probe_file.write_text(
                (
                    '{"Name":"Biriyani Osawa","City":"Tokyo","Address":"B1F 1-15-12 Uchikanda",'
                    '"Cuisine":"Indian","Rating":"Bib Gourmand"}\n'
                ),
                encoding="utf-8",
            )
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("bib-gourmand",),
                dry_run=False,
                maps_probe_rows_file=str(probe_file),
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 0)
        mock_crawl.assert_not_called()
        self.assertEqual(len(writer.rows_by_level_calls), 1)
        self.assertEqual(
            writer.rows_by_level_calls[0]["bib-gourmand"][0]["Name"],
            "Biriyani Osawa",
        )
        self.assertTrue(
            any("maps-probe-rows-file mode enabled" in warning for warning in output.warnings)
        )

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_routes_probe_rows_explicit_split_star_levelslug_into_stars_bucket(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        writer = _ProbeRowsWriter()
        mock_create_sync_writer.return_value = writer
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            probe_file = Path(temp_dir) / "probe.jsonl"
            probe_file.write_text(
                '{"Name":"Alpha","City":"Tokyo","Cuisine":"French","LevelSlug":"one-star"}\n',
                encoding="utf-8",
            )
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("stars", "selected", "bib-gourmand"),
                dry_run=False,
                maps_probe_rows_file=str(probe_file),
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 0)
        mock_crawl.assert_not_called()
        self.assertEqual(len(writer.rows_by_level_calls), 1)
        self.assertEqual(len(writer.rows_by_level_calls[0]["stars"]), 1)
        self.assertEqual(len(writer.rows_by_level_calls[0]["selected"]), 0)
        self.assertEqual(len(writer.rows_by_level_calls[0]["bib-gourmand"]), 0)

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_routes_probe_rows_rating_slug_into_stars_bucket_by_default(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        writer = _ProbeRowsWriter()
        mock_create_sync_writer.return_value = writer
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            probe_file = Path(temp_dir) / "probe.jsonl"
            probe_file.write_text(
                '{"Name":"Alpha","City":"Tokyo","Cuisine":"French","Rating":"one-star"}\n',
                encoding="utf-8",
            )
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("stars", "selected", "bib-gourmand"),
                dry_run=False,
                maps_probe_rows_file=str(probe_file),
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 0)
        mock_crawl.assert_not_called()
        self.assertEqual(len(writer.rows_by_level_calls), 1)
        self.assertEqual(len(writer.rows_by_level_calls[0]["stars"]), 1)
        self.assertEqual(len(writer.rows_by_level_calls[0]["selected"]), 0)
        self.assertEqual(len(writer.rows_by_level_calls[0]["bib-gourmand"]), 0)

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_routes_probe_rows_rating_slug_without_falling_to_selected(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        writer = _ProbeRowsWriter()
        mock_create_sync_writer.return_value = writer
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            probe_file = Path(temp_dir) / "probe.jsonl"
            probe_file.write_text(
                '{"Name":"Alpha","City":"Tokyo","Cuisine":"French","Rating":"one-star"}\n',
                encoding="utf-8",
            )
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star", "selected"),
                dry_run=False,
                maps_probe_rows_file=str(probe_file),
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 0)
        mock_crawl.assert_not_called()
        self.assertEqual(len(writer.rows_by_level_calls), 1)
        self.assertEqual(len(writer.rows_by_level_calls[0]["one-star"]), 1)
        self.assertEqual(len(writer.rows_by_level_calls[0]["selected"]), 0)

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_routes_probe_rows_rating_badge_without_falling_to_selected(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        writer = _ProbeRowsWriter()
        mock_create_sync_writer.return_value = writer
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            probe_file = Path(temp_dir) / "probe.jsonl"
            probe_file.write_text(
                '{"Name":"Beta","City":"Tokyo","Cuisine":"French","Rating":"⭐"}\n',
                encoding="utf-8",
            )
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star", "selected"),
                dry_run=False,
                maps_probe_rows_file=str(probe_file),
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 0)
        mock_crawl.assert_not_called()
        self.assertEqual(len(writer.rows_by_level_calls), 1)
        self.assertEqual(len(writer.rows_by_level_calls[0]["one-star"]), 1)
        self.assertEqual(len(writer.rows_by_level_calls[0]["selected"]), 0)

    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_fails_when_maps_probe_rows_file_is_invalid_json(
        self,
        mock_create_sync_writer: Mock,
    ) -> None:
        mock_create_sync_writer.return_value = _ProbeRowsWriter()
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            probe_file = Path(temp_dir) / "probe.jsonl"
            probe_file.write_text("not-json\n", encoding="utf-8")
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                dry_run=False,
                maps_probe_rows_file=str(probe_file),
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(output.failures), 1)
        self.assertIn("Invalid JSON in maps-probe-rows-file", output.failures[0])

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_handles_finalize_interrupt_after_keyboard_interrupt(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        mock_create_sync_writer.return_value = _FinalizeInterruptedWriter()
        mock_crawl.side_effect = KeyboardInterrupt()
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                dry_run=False,
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

            self.assertEqual(exit_code, 130)
            self.assertTrue(
                any(
                    warning.startswith("Failed to finalize Maps writer cleanly:")
                    for warning in output.warnings
                )
            )

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_requests_fast_shutdown_on_interrupt(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        writer = _FastShutdownWriter()
        mock_create_sync_writer.return_value = writer
        mock_crawl.side_effect = KeyboardInterrupt()
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                dry_run=False,
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

            self.assertEqual(exit_code, 130)
            self.assertTrue(writer.fast_shutdown_requested)

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_captures_debug_html_on_interrupt(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        writer = _FastShutdownWriterWithDebugHtml()
        mock_create_sync_writer.return_value = writer
        mock_crawl.side_effect = KeyboardInterrupt()
        output = _FakeOutput()
        with tempfile.TemporaryDirectory() as temp_dir:
            command = ScrapeSyncCommand(
                target="tokyo",
                google_user_data_dir="~/.michelin-gmaps-profile",
                levels=("one-star",),
                dry_run=False,
                state_dir=temp_dir,
            )

            exit_code = run_scrape_sync(command=command, output=output)

            self.assertEqual(exit_code, 130)
            self.assertEqual(len(writer.debug_html_paths), 1)
            debug_html_path = writer.debug_html_paths[0]
            self.assertTrue(
                any(
                    "Debug HTML snapshot written to:" in warning
                    and str(debug_html_path) in warning
                    for warning in output.warnings
                )
            )

    def test_redact_debug_html_text_masks_sensitive_tokens(self) -> None:
        raw_html = (
            "<html><body>"
            "email=person@example.com "
            "cookie SID=abc123; "
            "url=https://maps.google.com/?access_token=secrettoken "
            "escaped=https://maps.google.com/?center=1,2&amp;key=AIzaSyBoYjeRtfVI0Jd8Q_9mnflo9i4sOYpShB0 "
            "path=/Users/testuser/.michelin-gmaps-profile "
            '<div class="gb_g">Example Account</div> '
            '<a aria-label="Google Account: Example Account  person@example.com">'
            "</body></html>"
        )

        redacted_html = redact_html_text(raw_html)

        self.assertNotIn("person@example.com", redacted_html)
        self.assertNotIn("SID=abc123", redacted_html)
        self.assertNotIn("access_token=secrettoken", redacted_html)
        self.assertNotIn("AIzaSyBoYjeRtfVI0Jd8Q_9mnflo9i4sOYpShB0", redacted_html)
        self.assertNotIn("/Users/testuser", redacted_html)
        self.assertNotIn("Example Account", redacted_html)
        self.assertIn("<redacted-email>", redacted_html)
        self.assertIn("SID=<redacted>", redacted_html)
        self.assertIn("access_token=<redacted>", redacted_html)
        self.assertIn("&amp;key=<redacted>", redacted_html)
        self.assertIn("/Users/<redacted-user>", redacted_html)
        self.assertIn('<div class="gb_g"><redacted-account-name></div>', redacted_html)
        self.assertIn('aria-label="Google Account: <redacted-account-name>"', redacted_html)

    @patch("michelin_scraper.application.sync_use_case.crawl")
    @patch("michelin_scraper.application.sync_use_case._create_sync_writer")
    def test_run_scrape_sync_returns_non_zero_when_listing_fetch_fails(
        self,
        mock_create_sync_writer: Mock,
        mock_crawl: Mock,
    ) -> None:
        mock_create_sync_writer.return_value = _NoOpWriter()
        mock_crawl.return_value = ScrapeRunMetrics(
            total_restaurants=0,
            processed_pages=0,
            fetch_failures=1,
        )
        output = _FakeOutput()
        command = ScrapeSyncCommand(
            target="tokyo",
            google_user_data_dir="~/.michelin-gmaps-profile",
            levels=("one-star",),
            dry_run=False,
        )

        exit_code = run_scrape_sync(command=command, output=output)

        self.assertEqual(exit_code, 1)
        self.assertTrue(
            any("listing-page fetch failures" in failure for failure in output.failures)
        )


if __name__ == "__main__":
    unittest.main()
