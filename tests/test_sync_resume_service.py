"""Tests for checkpoint resume policy in Maps sync workflow."""

import unittest

from michelin_scraper.adapters.checkpoint_store import ResumeState
from michelin_scraper.application.sync_resume_service import prepare_resume_plan


class _FakeCheckpointStore:
    def __init__(self, resume_state: ResumeState | None) -> None:
        self.resume_state = resume_state
        self.load_calls = 0
        self.clear_calls = 0

    def load(self, expected_start_url: str) -> tuple[ResumeState | None, str | None]:
        del expected_start_url
        self.load_calls += 1
        return self.resume_state, None

    def clear(self) -> None:
        self.clear_calls += 1


class _FakeOutput:
    def __init__(self) -> None:
        self.resume_events: list[tuple[int, str]] = []
        self.warnings: list[str] = []

    def show_resume(self, next_page_number: int, next_url: str, **kwargs: object) -> None:
        self.resume_events.append((next_page_number, next_url))

    def warn(self, message: str) -> None:
        self.warnings.append(message)


class SyncResumeServiceTests(unittest.TestCase):
    def test_prepare_resume_plan_ignores_checkpoint_when_flag_is_set(self) -> None:
        checkpoint_store = _FakeCheckpointStore(
            ResumeState(
                next_url="https://example.com/page/9",
                next_page_number=9,
                total_restaurants=99,
                estimated_total_pages=50,
                rows_per_level={"one-star": 80},
            )
        )
        output = _FakeOutput()

        plan = prepare_resume_plan(
            start_url="https://example.com/page/1",
            level_slugs=("one-star",),
            checkpoint_store=checkpoint_store,
            output=output,
            ignore_checkpoint=True,
        )

        self.assertEqual(plan.start_scrape_url, "https://example.com/page/1")
        self.assertEqual(plan.start_page_number, 1)
        self.assertEqual(plan.initial_total_restaurants, 0)
        self.assertEqual(plan.row_counts, {"one-star": 0})
        self.assertEqual(checkpoint_store.clear_calls, 1)
        self.assertEqual(checkpoint_store.load_calls, 0)
        self.assertEqual(output.resume_events, [])
        self.assertEqual(
            output.warnings,
            ["Ignoring checkpoint because --ignore-checkpoint is set. Starting a new run."],
        )

    def test_prepare_resume_plan_uses_checkpoint_when_flag_is_unset(self) -> None:
        checkpoint_store = _FakeCheckpointStore(
            ResumeState(
                next_url="https://example.com/page/2",
                next_page_number=2,
                total_restaurants=10,
                estimated_total_pages=5,
                rows_per_level={"one-star": 7},
            )
        )
        output = _FakeOutput()

        plan = prepare_resume_plan(
            start_url="https://example.com/page/1",
            level_slugs=("one-star",),
            checkpoint_store=checkpoint_store,
            output=output,
            ignore_checkpoint=False,
        )

        self.assertEqual(plan.start_scrape_url, "https://example.com/page/2")
        self.assertEqual(plan.start_page_number, 2)
        self.assertEqual(plan.initial_total_restaurants, 10)
        self.assertEqual(plan.row_counts, {"one-star": 7})
        self.assertEqual(checkpoint_store.clear_calls, 0)
        self.assertEqual(checkpoint_store.load_calls, 1)
        self.assertEqual(output.resume_events, [(2, "https://example.com/page/2")])
        self.assertEqual(output.warnings, [])


if __name__ == "__main__":
    unittest.main()
