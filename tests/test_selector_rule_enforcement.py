"""Repository rule enforcement tests for Google Maps selector workflows."""

import unittest
from pathlib import Path


class SelectorRuleEnforcementTests(unittest.TestCase):
    def test_agents_requires_fixture_based_real_dom_evidence_for_selector_work(self) -> None:
        agents_text = Path("AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(
            "recorded, de-identified real DOM snapshots committed as fixtures",
            agents_text,
        )
        self.assertIn(
            "synthetic-only DOM guesses are not acceptable coverage",
            agents_text,
        )
        self.assertNotIn("MICHELIN_LIVE_MAPS_TESTS=1", agents_text)
        self.assertIn(
            'Do not label selector failures as generic "selector changed"',
            agents_text,
        )
        self.assertIn(
            "persist every page-level Maps sync failure HTML snapshot",
            agents_text,
        )
        self.assertIn(
            "Persisted debug HTML snapshots must be de-identified",
            agents_text,
        )
        self.assertIn(
            "Tests and fixtures must never contain real personal account names or account labels",
            agents_text,
        )
        self.assertIn(
            "Redact Google account display names as `<redacted-account-name>`",
            agents_text,
        )

    def test_sync_writer_uses_runtime_selector_failure_reason(self) -> None:
        writer_text = Path("michelin_scraper/adapters/google_maps_sync_writer.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("SelectorChanged:", writer_text)
        self.assertIn("SelectorRuntimeFailure:", writer_text)

    def test_real_fixture_directories_exist(self) -> None:
        self.assertTrue(Path("tests/fixtures/michelin").is_dir())
        self.assertTrue(Path("tests/fixtures/google_maps").is_dir())


if __name__ == "__main__":
    unittest.main()
