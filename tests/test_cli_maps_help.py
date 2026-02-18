"""Tests for root CLI help and auth-prompt behavior."""

import unittest
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from michelin_scraper.application.sync_use_case import AUTH_REQUIRED_EXIT_CODE
from michelin_scraper.config import (
    CRAWL_DELAY_OPTION_FLAGS,
    DEBUG_SYNC_FAILURES_OPTION_FLAGS,
    DEFAULT_GOOGLE_USER_DATA_DIR,
    IGNORE_CHECKPOINT_OPTION_FLAGS,
    IGNORE_EXISTING_LISTS_CHECK_OPTION_FLAGS,
    LANGUAGE_LIST_NAME_TEMPLATE_OVERRIDES,
    LANGUAGE_OPTION_FLAGS,
    LIST_NAME_TEMPLATE_OPTION_FLAGS,
    LOGIN_TIMEOUT_OPTION_FLAGS,
    MAPS_DELAY_OPTION_FLAGS,
    MAPS_PROBE_ONLY_OPTION_FLAGS,
    MAPS_PROBE_ROWS_FILE_OPTION_FLAGS,
    MAX_PAGES_OPTION_FLAGS,
    MAX_ROWS_PER_PAGE_OPTION_FLAGS,
    ON_MISSING_LIST_OPTION_FLAGS,
    RECORD_FIXTURES_DIR_OPTION_FLAGS,
    SANDBOX_OPTION_FLAGS,
    TARGET_OPTION_FLAGS,
)
from michelin_scraper.entrypoints.cli import app


class MapsCliHelpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_root_help_includes_quick_start_and_sync_options(self) -> None:
        result = self.runner.invoke(app, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("uv run playwright install chromium", result.stdout)
        self.assertIn("ms-playwright", result.stdout)
        self.assertIn("may not be secure", result.stdout)
        self.assertIn("TargetClosed", result.stdout)
        self.assertIn(LOGIN_TIMEOUT_OPTION_FLAGS[0], result.stdout)
        self.assertIn(TARGET_OPTION_FLAGS[0], result.stdout)
        self.assertIn(ON_MISSING_LIST_OPTION_FLAGS[0], result.stdout)
        self.assertIn(IGNORE_CHECKPOINT_OPTION_FLAGS[0], result.stdout)
        self.assertIn(DEBUG_SYNC_FAILURES_OPTION_FLAGS[0], result.stdout)
        self.assertIn(IGNORE_EXISTING_LISTS_CHECK_OPTION_FLAGS[0], result.stdout)
        self.assertIn(MAX_PAGES_OPTION_FLAGS[0], result.stdout)
        self.assertIn(MAX_ROWS_PER_PAGE_OPTION_FLAGS[0], result.stdout)
        self.assertIn(CRAWL_DELAY_OPTION_FLAGS[0], result.stdout)
        self.assertIn(MAPS_DELAY_OPTION_FLAGS[0], result.stdout)
        self.assertIn(MAPS_PROBE_ONLY_OPTION_FLAGS[0], result.stdout)
        self.assertIn(MAPS_PROBE_ROWS_FILE_OPTION_FLAGS[0], result.stdout)
        self.assertIn(SANDBOX_OPTION_FLAGS[0], result.stdout)
        self.assertIn(RECORD_FIXTURES_DIR_OPTION_FLAGS[0], result.stdout)
        self.assertIn("Latitude, Longitude", result.stdout)
        self.assertIn("fail immediately on first row failure", result.stdout)
        self.assertIn("Option Sections:", result.stdout)
        self.assertIn("User Options", result.stdout)
        self.assertIn("Developer and Debug Options", result.stdout)

    @patch("michelin_scraper.entrypoints.cli.run_scrape_sync")
    def test_root_passes_ignore_checkpoint_and_existing_list_flags(
        self,
        mock_sync: Mock,
    ) -> None:
        mock_sync.return_value = 0

        result = self.runner.invoke(
            app,
            [
                TARGET_OPTION_FLAGS[0],
                "tokyo",
                IGNORE_CHECKPOINT_OPTION_FLAGS[0],
                DEBUG_SYNC_FAILURES_OPTION_FLAGS[0],
                IGNORE_EXISTING_LISTS_CHECK_OPTION_FLAGS[0],
                MAX_PAGES_OPTION_FLAGS[0],
                "1",
                MAX_ROWS_PER_PAGE_OPTION_FLAGS[0],
                "2",
                MAPS_PROBE_ONLY_OPTION_FLAGS[0],
            ],
        )

        self.assertEqual(result.exit_code, 0)
        mock_sync.assert_called_once()
        called_command = mock_sync.call_args.kwargs["command"]
        self.assertTrue(called_command.ignore_checkpoint)
        self.assertTrue(called_command.debug_sync_failures)
        self.assertTrue(called_command.ignore_existing_lists_check)
        self.assertEqual(called_command.max_pages, 1)
        self.assertTrue(called_command.max_pages_specified)
        self.assertEqual(called_command.max_rows_per_page, 2)
        self.assertTrue(called_command.maps_probe_only)

    @patch("michelin_scraper.entrypoints.cli.run_scrape_sync")
    def test_root_passes_sandbox_and_record_fixtures_dir(
        self,
        mock_sync: Mock,
    ) -> None:
        mock_sync.return_value = 0

        result = self.runner.invoke(
            app,
            [
                TARGET_OPTION_FLAGS[0],
                "tokyo",
                SANDBOX_OPTION_FLAGS[0],
                RECORD_FIXTURES_DIR_OPTION_FLAGS[0],
                "/tmp/maps-fixtures",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        mock_sync.assert_called_once()
        called_command = mock_sync.call_args.kwargs["command"]
        self.assertTrue(called_command.sandbox)
        self.assertEqual(called_command.record_fixtures_dir, "/tmp/maps-fixtures")
        self.assertEqual(called_command.max_pages, 0)
        self.assertFalse(called_command.max_pages_specified)

    @patch("michelin_scraper.entrypoints.cli.run_scrape_sync")
    def test_root_passes_maps_probe_rows_file(
        self,
        mock_sync: Mock,
    ) -> None:
        mock_sync.return_value = 0

        result = self.runner.invoke(
            app,
            [
                TARGET_OPTION_FLAGS[0],
                "tokyo",
                MAPS_PROBE_ROWS_FILE_OPTION_FLAGS[0],
                "/tmp/probe-rows.jsonl",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        mock_sync.assert_called_once()
        called_command = mock_sync.call_args.kwargs["command"]
        self.assertEqual(called_command.maps_probe_rows_file, "/tmp/probe-rows.jsonl")

    @patch("michelin_scraper.entrypoints.cli.run_scrape_sync")
    def test_root_uses_traditional_chinese_output_behavior_when_language_is_zh_tw(
        self,
        mock_sync: Mock,
    ) -> None:
        mock_sync.return_value = 0

        result = self.runner.invoke(
            app,
            [
                TARGET_OPTION_FLAGS[0],
                "taiwan",
                LANGUAGE_OPTION_FLAGS[0],
                "zh-tw",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        mock_sync.assert_called_once()
        called_command = mock_sync.call_args.kwargs["command"]
        self.assertEqual(called_command.language, "zh_TW")
        self.assertEqual(
            called_command.list_name_template,
            LANGUAGE_LIST_NAME_TEMPLATE_OVERRIDES["zh_TW"],
        )

    @patch("michelin_scraper.entrypoints.cli.run_scrape_sync")
    def test_root_keeps_custom_list_template_when_language_is_zh_tw(
        self,
        mock_sync: Mock,
    ) -> None:
        mock_sync.return_value = 0

        result = self.runner.invoke(
            app,
            [
                TARGET_OPTION_FLAGS[0],
                "tainan",
                LANGUAGE_OPTION_FLAGS[0],
                "zh-tw",
                LIST_NAME_TEMPLATE_OPTION_FLAGS[0],
                "{scope} - {level_slug}",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        mock_sync.assert_called_once()
        called_command = mock_sync.call_args.kwargs["command"]
        self.assertEqual(called_command.language, "zh_TW")
        self.assertEqual(called_command.list_name_template, "{scope} - {level_slug}")

    @patch("michelin_scraper.entrypoints.cli.run_scrape_sync")
    def test_root_passes_crawl_and_maps_delay(
        self,
        mock_sync: Mock,
    ) -> None:
        mock_sync.return_value = 0

        result = self.runner.invoke(
            app,
            [
                TARGET_OPTION_FLAGS[0],
                "tokyo",
                CRAWL_DELAY_OPTION_FLAGS[0],
                "1.25",
                MAPS_DELAY_OPTION_FLAGS[0],
                "0.75",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        mock_sync.assert_called_once()
        called_command = mock_sync.call_args.kwargs["command"]
        self.assertEqual(called_command.sleep_seconds, 1.25)
        self.assertEqual(called_command.sync_delay_seconds, 0.75)

    @patch("michelin_scraper.entrypoints.cli.run_maps_login")
    @patch("michelin_scraper.entrypoints.cli.run_scrape_sync")
    @patch("michelin_scraper.entrypoints.cli.typer.confirm")
    def test_root_prompts_login_when_auth_required(
        self,
        mock_confirm: Mock,
        mock_sync: Mock,
        mock_login: Mock,
    ) -> None:
        mock_confirm.return_value = True
        mock_sync.side_effect = [AUTH_REQUIRED_EXIT_CODE, 0]
        mock_login.return_value = 0

        result = self.runner.invoke(
            app,
            [TARGET_OPTION_FLAGS[0], "taiwan"],
        )

        self.assertEqual(result.exit_code, 0)
        mock_confirm.assert_called_once()
        self.assertEqual(mock_sync.call_count, 2)
        self.assertEqual(mock_login.call_count, 1)
        called_command = mock_login.call_args.kwargs["command"]
        self.assertEqual(called_command.google_user_data_dir, DEFAULT_GOOGLE_USER_DATA_DIR)

    @patch("michelin_scraper.entrypoints.cli.run_maps_login")
    @patch("michelin_scraper.entrypoints.cli.run_scrape_sync")
    @patch("michelin_scraper.entrypoints.cli.typer.confirm")
    def test_root_stops_when_user_declines_login_prompt(
        self,
        mock_confirm: Mock,
        mock_sync: Mock,
        mock_login: Mock,
    ) -> None:
        mock_confirm.return_value = False
        mock_sync.return_value = AUTH_REQUIRED_EXIT_CODE
        mock_login.return_value = 0

        result = self.runner.invoke(
            app,
            [TARGET_OPTION_FLAGS[0], "taiwan"],
        )

        self.assertEqual(result.exit_code, AUTH_REQUIRED_EXIT_CODE)
        mock_confirm.assert_called_once()
        mock_sync.assert_called_once()
        mock_login.assert_not_called()


if __name__ == "__main__":
    unittest.main()
