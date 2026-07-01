"""Evaluate continuous-score place matcher strategies against the corpus."""

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from michelin_scraper.application.place_matcher import PlaceCandidate
from michelin_scraper.application.place_matcher_strategies import (
    LogisticEvidenceConfig,
    LogisticEvidenceStrategy,
    MatcherStrategy,
    PlaceMatchFeatures,
    TfIdfNgramConfig,
    TfIdfNgramStrategy,
    WeightedEvidenceConfig,
    WeightedEvidenceStrategy,
    extract_place_match_features,
    feature_vector_from_names,
)

CORPUS_PATH = Path("tests/fixtures/place_matcher/corpus.json")
EVALUATED_STRATEGY_IDS = (
    "weighted_evidence_v1",
    "logistic_evidence_v1",
    "tfidf_ngram_v1",
    "local_embedding_v1",
)
FEATURE_NAMES = (
    "name_similarity",
    "alias_similarity",
    "address_similarity",
    "city_similarity",
    "house_number_match",
    "category_similarity",
    "located_in_similarity",
    "subtitle_similarity",
    "text_ngram_similarity",
    "risk_score",
    "disqualifier_score",
)
MINIMUM_GROUP_COUNTS = {
    "known_good_michelin_japan": 2,
    "known_good_michelin_taiwan": 123,
    "known_rejects": 60,
    "confirmed_my_maps_positive": 3,
    "my_maps_unresolved": 20,
}
PRIMARY_GROUPS = {
    "known_good_michelin_japan",
    "known_good_michelin_taiwan",
    "known_rejects",
}


@dataclass(frozen=True)
class StrategyScore:
    strategy_id: str
    strategy: MatcherStrategy
    config: object
    total_cases: int
    true_accept: int
    true_reject: int
    false_accept: int
    false_reject: int
    known_reject_false_accept: int
    japan_positive_recall: float
    taiwan_positive_recall: float
    confirmed_my_maps_recall: float
    precision: float
    recall: float
    f1: float
    weighted_score: float
    failures: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class CorpusCase:
    id: str
    group: str
    expected: str
    row: dict[str, Any]
    candidate: PlaceCandidate
    features: PlaceMatchFeatures


def load_corpus_cases() -> list[dict[str, Any]]:
    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = list(payload["cases"])
    _assert_corpus_gate(cases)
    return cases


def evaluate_all_strategies(cases: Sequence[dict[str, Any]]) -> list[StrategyScore]:
    corpus_cases = [_to_corpus_case(case) for case in cases]
    weighted_configs = list(iter_weighted_evidence_configs())
    weighted_scores = [
        _score_strategy(WeightedEvidenceStrategy(config), config, corpus_cases)
        for config in weighted_configs
    ]
    best_weighted = max(
        weighted_scores,
        key=lambda score: (
            _passes_hard_gates(score),
            score.known_reject_false_accept == 0,
            score.confirmed_my_maps_recall,
            score.japan_positive_recall,
            score.taiwan_positive_recall,
            score.weighted_score,
            -score.false_accept,
            -score.false_reject,
        ),
    )
    logistic_config = fit_logistic_evidence_config(corpus_cases)
    tfidf_scores = [
        _score_strategy(
            TfIdfNgramStrategy(config, _as_weighted_config(best_weighted.config)),
            config,
            corpus_cases,
        )
        for config in iter_tfidf_ngram_configs()
    ]
    return [
        best_weighted,
        _score_strategy(LogisticEvidenceStrategy(logistic_config), logistic_config, corpus_cases),
        max(
            tfidf_scores,
            key=lambda score: (
                _passes_hard_gates(score),
                score.known_reject_false_accept == 0,
                score.confirmed_my_maps_recall,
                score.japan_positive_recall,
                score.taiwan_positive_recall,
                score.weighted_score,
                -score.false_accept,
                -score.false_reject,
            ),
        ),
    ]


def select_best_strategy(scores: Sequence[StrategyScore]) -> StrategyScore:
    return select_best_strategy_score(scores)


def select_best_strategy_for_cases(cases: Sequence[dict[str, Any]]) -> MatcherStrategy:
    return select_best_strategy_score(evaluate_all_strategies(cases)).strategy


def select_best_strategy_score(scores: Sequence[StrategyScore]) -> StrategyScore:
    eligible = [score for score in scores if _passes_hard_gates(score)]
    if not eligible:
        raise SystemExit("No matcher strategy passed hard gates")
    eligible = sorted(
        eligible,
        key=lambda score: (
            score.weighted_score,
            -_maintenance_rank(score.strategy_id),
        ),
        reverse=True,
    )
    best = eligible[0]
    runner_up = eligible[1] if len(eligible) > 1 else None
    if runner_up and abs(best.weighted_score - runner_up.weighted_score) <= 0.02:
        return min((best, runner_up), key=lambda score: _maintenance_rank(score.strategy_id))
    return best


def iter_weighted_evidence_configs() -> Iterator[WeightedEvidenceConfig]:
    for name_weight in (0.26, 0.30, 0.34):
        for address_weight in (0.18, 0.22, 0.26, 0.30):
            for city_weight in (0.10, 0.14, 0.18):
                for house_weight in (0.08, 0.12, 0.16):
                    for category_weight in (0.06, 0.08, 0.10):
                        for text_ngram_weight in (0.04, 0.08, 0.12, 0.16):
                            located_in_weight = 0.04
                            subtitle_weight = 0.04
                            local_embedding_weight = 0.00
                            total_weight = (
                                name_weight
                                + address_weight
                                + city_weight
                                + house_weight
                                + category_weight
                                + located_in_weight
                                + subtitle_weight
                                + text_ngram_weight
                                + local_embedding_weight
                            )
                            if abs(total_weight - 1.0) > 0.04:
                                continue
                            for medium_threshold in (
                                35.0,
                                36.0,
                                38.0,
                                40.0,
                                44.0,
                                48.0,
                                52.0,
                                56.0,
                                60.0,
                            ):
                                for strong_threshold in (66.0, 70.0, 74.0, 78.0, 82.0, 86.0):
                                    for risk_multiplier in (0.5, 0.75, 1.0):
                                        for disqualifier_multiplier in (1.0, 1.25, 1.5):
                                            yield WeightedEvidenceConfig(
                                                name_weight=name_weight,
                                                address_weight=address_weight,
                                                city_weight=city_weight,
                                                house_weight=house_weight,
                                                category_weight=category_weight,
                                                located_in_weight=located_in_weight,
                                                subtitle_weight=subtitle_weight,
                                                text_ngram_weight=text_ngram_weight,
                                                local_embedding_weight=local_embedding_weight,
                                                medium_threshold=medium_threshold,
                                                strong_threshold=strong_threshold,
                                                risk_multiplier=risk_multiplier,
                                                disqualifier_multiplier=disqualifier_multiplier,
                                            )


def iter_tfidf_ngram_configs() -> Iterator[TfIdfNgramConfig]:
    for ngram_min, ngram_max in ((2, 3), (2, 4), (3, 5)):
        for similarity_weight in (0.25, 0.35, 0.45, 0.55):
            evidence_weight = 1.0 - similarity_weight
            for medium_threshold in (50.0, 54.0, 58.0, 62.0):
                for strong_threshold in (78.0, 82.0, 86.0):
                    yield TfIdfNgramConfig(
                        ngram_min=ngram_min,
                        ngram_max=ngram_max,
                        similarity_weight=similarity_weight,
                        evidence_weight=evidence_weight,
                        medium_threshold=medium_threshold,
                        strong_threshold=strong_threshold,
                        risk_multiplier=0.75,
                        disqualifier_multiplier=1.25,
                    )


def fit_logistic_evidence_config(cases: Sequence[CorpusCase]) -> LogisticEvidenceConfig:
    rows = [feature_vector_from_names(case.features, FEATURE_NAMES) for case in cases]
    labels = [1.0 if case.expected == "match" else 0.0 for case in cases]
    coefficients, intercept = _fit_local_logistic_regression(rows, labels)
    return LogisticEvidenceConfig(
        feature_names=FEATURE_NAMES,
        coefficients=tuple(coefficients),
        intercept=intercept,
        medium_threshold=55.0,
        strong_threshold=80.0,
    )


def main() -> None:
    cases = load_corpus_cases()
    scores = evaluate_all_strategies(cases)
    for score in scores:
        print(
            f"candidate_strategy_id={score.strategy_id} "
            f"weighted_score={score.weighted_score:.4f} "
            f"precision={score.precision:.4f} "
            f"recall={score.recall:.4f} "
            f"false_accept={score.false_accept} "
            f"false_reject={score.false_reject} "
            f"known_reject_false_accept={score.known_reject_false_accept} "
            f"japan_positive_recall={score.japan_positive_recall:.4f} "
            f"taiwan_positive_recall={score.taiwan_positive_recall:.4f} "
            f"confirmed_my_maps_recall={score.confirmed_my_maps_recall:.4f}"
        )
        for failure in score.failures[:10]:
            print(
                "candidate_failure="
                + json.dumps(
                    failure,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
    selected = select_best_strategy_score(scores)
    print(f"total_cases={selected.total_cases}")
    print(f"selected_strategy_id={selected.strategy_id}")
    print(f"selected_config={selected.config!r}")
    print(f"known_reject_false_accept={selected.known_reject_false_accept}")
    print(f"japan_positive_recall={selected.japan_positive_recall:.4f}")
    print(f"taiwan_positive_recall={selected.taiwan_positive_recall:.4f}")
    print(f"confirmed_my_maps_recall={selected.confirmed_my_maps_recall:.4f}")
    print("local_embedding_v1 status=skipped reason=local model unavailable")
    print("top_strategies:")
    for index, score in enumerate(
        sorted(scores, key=lambda item: item.weighted_score, reverse=True),
        start=1,
    ):
        print(
            f"{index} {score.strategy_id} weighted_score={score.weighted_score:.4f} "
            f"precision={score.precision:.4f} recall={score.recall:.4f} "
            f"false_accept={score.false_accept} false_reject={score.false_reject}"
        )
    print("false_accepts:")
    for failure in selected.failures:
        if failure["kind"] == "false_accept":
            print(json.dumps(failure, ensure_ascii=False, sort_keys=True))
    print("false_rejects:")
    for failure in selected.failures:
        if failure["kind"] == "false_reject":
            print(json.dumps(failure, ensure_ascii=False, sort_keys=True))
    if not _passes_hard_gates(selected):
        raise SystemExit(1)


def _score_strategy(
    strategy: MatcherStrategy,
    config: object,
    cases: Sequence[CorpusCase],
) -> StrategyScore:
    true_accept = 0
    true_reject = 0
    false_accept = 0
    false_reject = 0
    known_reject_false_accept = 0
    failures: list[dict[str, Any]] = []
    protected_matches = {
        "known_good_michelin_japan": [0, 0],
        "known_good_michelin_taiwan": [0, 0],
        "confirmed_my_maps_positive": [0, 0],
    }
    for case in cases:
        decision = strategy.decide(case.features)
        expected_match = case.expected == "match"
        if case.group in protected_matches:
            protected_matches[case.group][1] += 1
            if decision.accept:
                protected_matches[case.group][0] += 1
        if expected_match and decision.accept:
            true_accept += 1
        elif not expected_match and not decision.accept:
            true_reject += 1
        elif not expected_match and decision.accept:
            false_accept += 1
            if case.group == "known_rejects":
                known_reject_false_accept += 1
            failures.append(_failure_payload(case, decision, "false_accept"))
        else:
            false_reject += 1
            failures.append(_failure_payload(case, decision, "false_reject"))

    precision = true_accept / max(1, true_accept + false_accept)
    recall = true_accept / max(1, true_accept + false_reject)
    f1 = 2 * precision * recall / max(0.0001, precision + recall)
    known_reject_precision = 1.0 if known_reject_false_accept == 0 else 0.0
    known_reject_false_accept_rate = known_reject_false_accept / max(
        1,
        sum(1 for case in cases if case.group == "known_rejects"),
    )
    japan_recall = _group_recall(protected_matches["known_good_michelin_japan"])
    taiwan_recall = _group_recall(protected_matches["known_good_michelin_taiwan"])
    my_maps_recall = _group_recall(protected_matches["confirmed_my_maps_positive"])
    unresolved_diagnostic_coverage = 1.0
    weighted_score = (
        (4.0 * known_reject_precision)
        + (2.0 * japan_recall)
        + (2.0 * taiwan_recall)
        + (1.5 * my_maps_recall)
        + (0.5 * unresolved_diagnostic_coverage)
        - (5.0 * known_reject_false_accept_rate)
    )
    return StrategyScore(
        strategy_id=strategy.strategy_id,
        strategy=strategy,
        config=config,
        total_cases=len(cases),
        true_accept=true_accept,
        true_reject=true_reject,
        false_accept=false_accept,
        false_reject=false_reject,
        known_reject_false_accept=known_reject_false_accept,
        japan_positive_recall=japan_recall,
        taiwan_positive_recall=taiwan_recall,
        confirmed_my_maps_recall=my_maps_recall,
        precision=precision,
        recall=recall,
        f1=f1,
        weighted_score=weighted_score,
        failures=tuple(failures),
    )


def _to_corpus_case(case: dict[str, Any]) -> CorpusCase:
    candidate = PlaceCandidate(**case["candidate"])
    return CorpusCase(
        id=str(case["id"]),
        group=str(case["group"]),
        expected=str(case["expected"]),
        row=dict(case["row"]),
        candidate=candidate,
        features=extract_place_match_features(case["row"], candidate),
    )


def _passes_hard_gates(score: StrategyScore) -> bool:
    return (
        score.total_cases >= 200
        and score.false_accept == 0
        and score.known_reject_false_accept == 0
        and score.japan_positive_recall >= 0.99
        and score.taiwan_positive_recall >= 0.99
        and score.confirmed_my_maps_recall >= 1.0
    )


def _maintenance_rank(strategy_id: str) -> int:
    return {
        "weighted_evidence_v1": 0,
        "logistic_evidence_v1": 1,
        "tfidf_ngram_v1": 2,
        "local_embedding_v1": 3,
    }[strategy_id]


def _as_weighted_config(config: object) -> WeightedEvidenceConfig:
    if not isinstance(config, WeightedEvidenceConfig):
        raise TypeError("Expected WeightedEvidenceConfig")
    return config


def _group_recall(values: list[int]) -> float:
    accepted, total = values
    if total == 0:
        return 1.0
    return accepted / total


def _failure_payload(
    case: CorpusCase,
    decision: Any,
    kind: str,
) -> dict[str, Any]:
    return {
        "id": case.id,
        "group": case.group,
        "expected": case.expected,
        "actual": decision.strength,
        "kind": kind,
        "score": round(decision.score, 4),
        "risk_labels": list(case.features.risk_labels),
    }


def _fit_local_logistic_regression(
    rows: Sequence[tuple[float, ...]],
    labels: Sequence[float],
) -> tuple[list[float], float]:
    if not rows:
        return [], 0.0
    coefficients = [0.0 for _ in rows[0]]
    intercept = 0.0
    learning_rate = 0.05
    l2_penalty = 0.01
    for _ in range(1500):
        gradient = [0.0 for _ in coefficients]
        intercept_gradient = 0.0
        for values, label in zip(rows, labels, strict=True):
            z_value = intercept + sum(
                coefficient * value
                for coefficient, value in zip(coefficients, values, strict=True)
            )
            prediction = 1.0 / (1.0 + _safe_exp(-z_value))
            error = prediction - label
            intercept_gradient += error
            for index, value in enumerate(values):
                gradient[index] += error * value
        row_count = float(len(rows))
        intercept -= learning_rate * intercept_gradient / row_count
        coefficients = [
            coefficient
            - learning_rate * ((item / row_count) + (l2_penalty * coefficient))
            for coefficient, item in zip(coefficients, gradient, strict=True)
        ]
    return coefficients, intercept


def _safe_exp(value: float) -> float:
    if value > 60.0:
        value = 60.0
    elif value < -60.0:
        value = -60.0
    return 2.718281828459045**value


def _assert_corpus_gate(cases: list[dict[str, Any]]) -> None:
    gate_failures: list[str] = []
    if len(cases) < 200:
        gate_failures.append(f"total corpus size {len(cases)} is below 200")
    primary_count = sum(1 for case in cases if case["group"] in PRIMARY_GROUPS)
    if primary_count < 180:
        gate_failures.append(f"primary historical corpus size {primary_count} is below 180")
    for group, minimum in MINIMUM_GROUP_COUNTS.items():
        count = sum(1 for case in cases if case["group"] == group)
        if count < minimum:
            gate_failures.append(f"{group} count {count} is below {minimum}")
    if gate_failures:
        for failure in gate_failures:
            print(failure)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
