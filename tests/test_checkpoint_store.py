"""Tests for JSON checkpoint adapter behavior."""


import json
import tempfile
import unittest
from pathlib import Path

from michelin_scraper.adapters.checkpoint_store import JsonCheckpointStore


class CheckpointStoreTests(unittest.TestCase):
    def test_load_missing_checkpoint_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint_path = Path(temp_dir) / "checkpoint.json"
            store = JsonCheckpointStore(checkpoint_path, level_slugs=("one-star", "selected"))
            resume_state, warning = store.load(expected_start_url="https://example.com")
            self.assertIsNone(resume_state)
            self.assertIsNone(warning)

    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint_path = Path(temp_dir) / "checkpoint.json"
            store = JsonCheckpointStore(checkpoint_path, level_slugs=("one-star", "selected"))

            store.save(
                start_url="https://example.com/start",
                page_number=3,
                page_url="https://example.com/start?page=3",
                next_url="https://example.com/start?page=4",
                next_page_number=4,
                estimated_total_pages=10,
                total_restaurants=33,
                rows_per_level={"one-star": 10, "selected": 23},
            )

            resume_state, warning = store.load(expected_start_url="https://example.com/start")
            self.assertIsNone(warning)
            self.assertIsNotNone(resume_state)
            assert resume_state is not None
            self.assertEqual(resume_state.next_page_number, 4)
            self.assertEqual(resume_state.total_restaurants, 33)
            self.assertEqual(resume_state.rows_per_level["one-star"], 10)
            self.assertEqual(resume_state.rows_per_level["selected"], 23)

    def test_load_with_invalid_rows_returns_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint_path = Path(temp_dir) / "checkpoint.json"
            payload = {
                "version": 1,
                "start_url": "https://example.com/start",
                "next_url": "https://example.com/start?page=2",
                "next_page_number": 2,
                "total_restaurants": 3,
                "estimated_total_pages": 8,
                "rows_per_level": {"one-star": -1},
            }
            checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")
            store = JsonCheckpointStore(checkpoint_path, level_slugs=("one-star", "selected"))

            resume_state, warning = store.load(expected_start_url="https://example.com/start")
            self.assertIsNone(resume_state)
            self.assertEqual(
                warning,
                "checkpoint row counters are invalid.",
            )


if __name__ == "__main__":
    unittest.main()
