"""Strategy evaluation tests for place matching."""

import unittest

from michelin_scraper.application.place_matcher_strategies import (
    BaselineNameStrategy,
    CharacterNgramNameStrategy,
    DescriptorAwareNameStrategy,
    NameEvidence,
    RapidFuzzNameStrategy,
    build_name_inputs,
    evaluate_name_evidence,
)
from michelin_scraper.devtools.evaluate_matchers import (
    EVALUATED_STRATEGY_IDS,
    evaluate_all_strategies,
    load_corpus_cases,
    select_best_strategy,
)


class PlaceMatcherStrategyEvaluationTests(unittest.TestCase):
    def test_baseline_strategy_returns_name_evidence(self) -> None:
        inputs = build_name_inputs(
            row_names=("蓮霧腳羊肉湯",),
            candidate_names=("蓮霧腳羊肉",),
        )

        evidence = BaselineNameStrategy().score(inputs)

        self.assertIsInstance(evidence, NameEvidence)
        self.assertGreaterEqual(evidence.score, 80.0)
        self.assertEqual(evidence.strategy, "baseline")

    def test_rapidfuzz_scores_minor_cjk_variants_without_api_calls(self) -> None:
        inputs = build_name_inputs(
            row_names=("君腳庫飯",),
            candidate_names=("君腿庫飯",),
        )

        evidence = RapidFuzzNameStrategy().score(inputs)

        self.assertGreaterEqual(evidence.score, 75.0)
        self.assertEqual(evidence.strategy, "rapidfuzz")

    def test_character_ngram_scores_partial_cjk_business_names(self) -> None:
        inputs = build_name_inputs(
            row_names=("七美麵店",),
            candidate_names=("七美麵、飲、滷味",),
        )

        evidence = CharacterNgramNameStrategy().score(inputs)

        self.assertGreaterEqual(evidence.score, 70.0)
        self.assertEqual(evidence.strategy, "character-ngram")

    def test_descriptor_strategy_vetoes_conflicting_food_items(self) -> None:
        inputs = build_name_inputs(
            row_names=("六號碼頭麵店",),
            candidate_names=("六號碼頭肉圓店",),
        )

        evidence = DescriptorAwareNameStrategy().score(inputs)

        self.assertLess(evidence.score, 60.0)
        self.assertIn("food_descriptor_conflict", evidence.reasons)

    def test_evaluate_name_evidence_uses_best_safe_strategy(self) -> None:
        inputs = build_name_inputs(
            row_names=("福德小館",),
            candidate_names=("福得小館",),
        )

        evidence = evaluate_name_evidence(inputs)

        self.assertGreaterEqual(evidence.score, 75.0)
        self.assertIn(evidence.strategy, {"rapidfuzz", "character-ngram", "baseline"})

    def test_strategy_selection_uses_at_least_200_cases(self) -> None:
        cases = load_corpus_cases()

        self.assertGreaterEqual(len(cases), 200)

    def test_evaluator_does_not_include_hard_condition_strategies(self) -> None:
        self.assertNotIn("decision_tree_v1", EVALUATED_STRATEGY_IDS)
        self.assertNotIn("pairwise_ranker_placeholder", EVALUATED_STRATEGY_IDS)

    def test_selected_production_strategy_has_best_weighted_corpus_score(self) -> None:
        scores = evaluate_all_strategies(load_corpus_cases())

        selected = select_best_strategy(scores)

        self.assertIn(
            selected.strategy_id,
            {
                "weighted_evidence_v1",
                "logistic_evidence_v1",
                "tfidf_ngram_v1",
                "local_embedding_v1",
            },
        )
        self.assertEqual(selected.known_reject_false_accept, 0)
        self.assertGreaterEqual(selected.japan_positive_recall, 0.99)
        self.assertGreaterEqual(selected.taiwan_positive_recall, 0.99)


if __name__ == "__main__":
    unittest.main()
