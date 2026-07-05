"""Tests for CLI sync presenter output."""

import io
import unittest
from contextlib import redirect_stdout

from michelin_scraper.application.sync_models import (
    SyncItemFailure,
    SyncSummary,
)
from michelin_scraper.domain import ScrapeRunMetrics
from michelin_scraper.output.console_sync_presenter import ConsoleSyncPresenter


class ConsoleSyncPresenterTests(unittest.TestCase):
    def test_show_final_results_prints_failure_note_text_for_manual_recovery(self) -> None:
        presenter = ConsoleSyncPresenter()
        summary = SyncSummary(
            metrics=ScrapeRunMetrics(total_restaurants=1, processed_pages=1),
            sample_rows=(),
            scraped_count_by_level={"imported": 1},
            added_count_by_level={"imported": 0},
            skipped_count_by_level={"imported": 0},
            failed_items=[
                SyncItemFailure(
                    level_slug="imported",
                    row_key="imported::alpha",
                    restaurant_name="Alpha",
                    reason="PlaceNotFound",
                    attempted_queries=("Alpha Taitung",),
                    note_text="1碗 | 春捲 | 台式 | 蔣勳 | 950臺東縣台東市正氣路453-1號 | 08 933 2520",
                )
            ],
            missing_lists=(),
            list_names_by_level={"imported": "My Places"},
            output_targets=(("imported", "Imported"),),
            elapsed_seconds=1.0,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            presenter.show_final_results(summary)

        text = output.getvalue()
        self.assertIn("Row Failures", text)
        self.assertIn(
            "Note: 1碗 | 春捲 | 台式 | 蔣勳 | 950臺東縣台東市正氣路453-1號 | 08 933 2520",
            text,
        )


if __name__ == "__main__":
    unittest.main()
