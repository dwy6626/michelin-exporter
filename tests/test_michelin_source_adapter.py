"""Tests for the Michelin source adapter boundary."""

import unittest
from typing import Any
from unittest.mock import Mock, patch

from michelin_scraper.application.row_router import UnrecognizedRatingError
from michelin_scraper.application.source_models import SourceRunHandlers
from michelin_scraper.application.sync_models import ScrapeSyncCommand
from michelin_scraper.domain import ScrapeRunMetrics
from michelin_scraper.sources.michelin import MichelinSourceAdapter


class _NoOpOutput:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)


class _NoOpReporter:
    def update(self, message: str, progress: float | None = None) -> None:
        del message, progress

    def log(self, message: str) -> None:
        del message

    def finish(self, message: str | None = None) -> None:
        del message


class MichelinSourceAdapterTests(unittest.TestCase):
    def test_prepare_resolves_target_and_selected_level_buckets(self) -> None:
        adapter = MichelinSourceAdapter()
        command = ScrapeSyncCommand(
            target="tokyo",
            google_user_data_dir="/tmp/profile",
            levels=("one-star", "selected"),
        )

        plan = adapter.prepare(command, _NoOpOutput())

        self.assertEqual(plan.source_id, "michelin")
        self.assertEqual(plan.scope_name, "Tokyo")
        self.assertEqual(plan.checkpoint_scope, "Tokyo")
        self.assertTrue(plan.start_url)
        self.assertEqual(
            [(bucket.slug, bucket.label, bucket.badge) for bucket in plan.buckets],
            [("one-star", "1 Star", "⭐"), ("selected", "Selected", "Selected")],
        )

    @patch("michelin_scraper.sources.michelin.resolve_listing_scope_name")
    def test_prepare_keeps_checkpoint_scope_stable_when_list_scope_is_localized(
        self,
        mock_resolve_listing_scope_name: Mock,
    ) -> None:
        mock_resolve_listing_scope_name.return_value = "Language Scope"
        adapter = MichelinSourceAdapter()
        command = ScrapeSyncCommand(
            target="tokyo",
            google_user_data_dir="/tmp/profile",
            levels=("stars",),
            language="zh-tw",
        )

        plan = adapter.prepare(command, _NoOpOutput())

        self.assertEqual(plan.scope_name, "Language Scope")
        self.assertEqual(plan.checkpoint_scope, "東京")

    @patch("michelin_scraper.sources.michelin.resolve_listing_scope_name")
    def test_prepare_prefers_fallback_scope_when_listing_scope_mixes_ascii_and_local_text(
        self,
        mock_resolve_listing_scope_name: Mock,
    ) -> None:
        mock_resolve_listing_scope_name.return_value = "Tainan 餐廳"
        adapter = MichelinSourceAdapter()
        command = ScrapeSyncCommand(
            target="tainan",
            google_user_data_dir="/tmp/profile",
            levels=("stars",),
            language="zh-tw",
        )

        plan = adapter.prepare(command, _NoOpOutput())

        self.assertEqual(plan.scope_name, "臺南")
        self.assertEqual(plan.checkpoint_scope, "臺南")

    def test_group_local_rows_routes_explicit_split_star_level_into_stars_bucket(self) -> None:
        adapter = MichelinSourceAdapter()

        grouped = adapter.group_local_rows_by_bucket(
            rows=[{"Name": "Alpha", "LevelSlug": "one-star"}],
            bucket_slugs=("stars", "selected", "bib-gourmand"),
        )

        self.assertEqual([row["Name"] for row in grouped["stars"]], ["Alpha"])
        self.assertEqual(grouped["selected"], [])
        self.assertEqual(grouped["bib-gourmand"], [])

    @patch("michelin_scraper.sources.michelin.crawl")
    def test_unknown_michelin_rating_fails_inside_adapter_path(self, mock_crawl: Mock) -> None:
        adapter = MichelinSourceAdapter()
        command = ScrapeSyncCommand(
            target="tokyo",
            google_user_data_dir="/tmp/profile",
            levels=("one-star",),
        )
        plan = adapter.prepare(command, _NoOpOutput())

        def fake_crawl(*_: object, **kwargs: Any) -> ScrapeRunMetrics:
            kwargs["on_item"](1, None, None, {"Name": "Mystery", "Rating": "Unknown"})
            return ScrapeRunMetrics(total_restaurants=1, processed_pages=1)

        mock_crawl.side_effect = fake_crawl
        handlers = SourceRunHandlers(
            on_item=lambda *_args: None,
            on_page=lambda *_args: None,
            on_interrupt=lambda *_args: None,
            progress_reporter=_NoOpReporter(),
            start_cursor=plan.start_url or "",
        )

        with self.assertRaises(UnrecognizedRatingError):
            adapter.run(command=command, plan=plan, handlers=handlers)


if __name__ == "__main__":
    unittest.main()
