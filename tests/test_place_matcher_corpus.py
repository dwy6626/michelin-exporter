"""Corpus regression tests for place matching."""

import json
import unittest
from pathlib import Path
from typing import Any

from michelin_scraper.application.place_matcher import PlaceCandidate, assess_place_match
from michelin_scraper.devtools.evaluate_matchers import (
    MINIMUM_GROUP_COUNTS as EVALUATOR_MINIMUM_GROUP_COUNTS,
)

CORPUS_PATH = Path(__file__).parent / "fixtures" / "place_matcher" / "corpus.json"
MINIMUM_GROUP_COUNTS = {
    "known_good_michelin_japan": 2,
    "known_good_michelin_taiwan": 123,
    "known_rejects": 55,
    "confirmed_my_maps_positive": 11,
    "my_maps_unresolved": 17,
}
PRIMARY_GROUPS = {
    "known_good_michelin_japan",
    "known_good_michelin_taiwan",
    "known_rejects",
}


def _load_cases() -> list[dict[str, Any]]:
    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    return list(payload["cases"])


class PlaceMatcherCorpusTests(unittest.TestCase):
    def test_corpus_minimum_metadata_matches_runtime_gates(self) -> None:
        payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        metadata_minimums = dict(payload["minimums"])
        metadata_minimums.pop("total", None)

        self.assertEqual(metadata_minimums, MINIMUM_GROUP_COUNTS)
        self.assertEqual(metadata_minimums, EVALUATOR_MINIMUM_GROUP_COUNTS)

    def test_corpus_minimum_size_and_distribution(self) -> None:
        cases = _load_cases()
        self.assertGreaterEqual(len(cases), 200)
        primary_count = sum(1 for case in cases if case["group"] in PRIMARY_GROUPS)
        self.assertGreaterEqual(primary_count, 180)
        for group, minimum in MINIMUM_GROUP_COUNTS.items():
            with self.subTest(group=group):
                count = sum(1 for case in cases if case["group"] == group)
                self.assertGreaterEqual(count, minimum)

    def test_corpus_ids_are_unique(self) -> None:
        ids = [case["id"] for case in _load_cases()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_corpus_cases_have_required_shape(self) -> None:
        for case in _load_cases():
            with self.subTest(case=case["id"]):
                self.assertIn(case["group"], set(MINIMUM_GROUP_COUNTS))
                self.assertIn(case["expected"], {"match", "reject", "manual"})
                for row_key in ("Name", "City", "Address", "Cuisine"):
                    self.assertIn(row_key, case["row"])
                for candidate_key in ("name", "address", "category", "subtitle", "located_in"):
                    self.assertIn(candidate_key, case["candidate"])
                self.assertNotIn("/Users/", json.dumps(case, ensure_ascii=False))

    def test_corpus_cases_match_expected_strength(self) -> None:
        for case in _load_cases():
            with self.subTest(case=case["id"]):
                assessment = assess_place_match(case["row"], PlaceCandidate(**case["candidate"]))
                if case["expected"] == "match":
                    self.assertIn(
                        assessment.strength,
                        {"medium", "strong"},
                        self._failure_message(case, assessment),
                    )
                elif case["expected"] in {"reject", "manual"}:
                    self.assertEqual(assessment.strength, "weak", self._failure_message(case, assessment))
                else:
                    self.fail(f"Unknown expected label: {case['expected']}")

    def test_known_good_michelin_japan_all_match(self) -> None:
        self._assert_group("known_good_michelin_japan", expected_strengths={"medium", "strong"})

    def test_known_good_michelin_taiwan_all_match(self) -> None:
        self._assert_group("known_good_michelin_taiwan", expected_strengths={"medium", "strong"})

    def test_known_rejects_all_reject(self) -> None:
        self._assert_group("known_rejects", expected_strengths={"weak"})

    def test_confirmed_my_maps_positives_all_match(self) -> None:
        self._assert_group("confirmed_my_maps_positive", expected_strengths={"medium", "strong"})

    def _assert_group(self, group: str, *, expected_strengths: set[str]) -> None:
        cases = [case for case in _load_cases() if case["group"] == group]
        self.assertGreater(len(cases), 0)
        for case in cases:
            with self.subTest(case=case["id"]):
                assessment = assess_place_match(case["row"], PlaceCandidate(**case["candidate"]))
                self.assertIn(assessment.strength, expected_strengths, self._failure_message(case, assessment))

    def _failure_message(self, case: dict[str, Any], assessment: object) -> str:
        return (
            f"case={case['id']} expected={case['expected']} "
            f"actual={getattr(assessment, 'strength', '<missing>')} "
            f"name_score={getattr(assessment, 'name_score', '<missing>')} "
            f"address_score={getattr(assessment, 'address_score', '<missing>')} "
            f"match_score={getattr(assessment, 'match_score', '<missing>')} "
            f"hard_veto={getattr(assessment, 'hard_veto', '<missing>')} "
            f"veto_reasons={getattr(assessment, 'veto_reasons', '<missing>')}"
        )


if __name__ == "__main__":
    unittest.main()
