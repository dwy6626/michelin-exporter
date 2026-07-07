"""Offline name-similarity strategies for place matching."""

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import exp, sqrt
from typing import Any, Literal, Protocol

from pypinyin import lazy_pinyin
from rapidfuzz import fuzz

_WORD_OR_NUMBER_PATTERN = re.compile(r"[^\W\d_]+|\d+")
_ASCII_WORD_OR_NUMBER_PATTERN = re.compile(r"[a-z0-9]+")
_CJK_SEQUENCE_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_NAME_COMPACT_TOKEN_PATTERN = re.compile(r"[a-z0-9\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_PARENTHETICAL_SEGMENT_PATTERN = re.compile(r"\s*[（(][^）)]{1,24}[）)]\s*")
_PARENTHETICAL_BRACKET_PATTERN = re.compile(r"[（）()]")
_CJK_CHARACTER_PATTERN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_GREEK_TRANSLITERATION = {
    "α": "a",
    "β": "v",
    "γ": "g",
    "δ": "d",
    "ε": "e",
    "ζ": "z",
    "η": "i",
    "θ": "th",
    "ι": "i",
    "κ": "k",
    "λ": "l",
    "μ": "m",
    "ν": "n",
    "ξ": "x",
    "ο": "o",
    "π": "p",
    "ρ": "r",
    "σ": "s",
    "ς": "s",
    "τ": "t",
    "υ": "u",
    "φ": "f",
    "χ": "ch",
    "ψ": "ps",
    "ω": "o",
}
_CJK_NAME_VARIANT_TRANSLATION = str.maketrans(
    {
        "の": "的",
        "囍": "禧",
        "脚": "腳",
        "焿": "羹",
        "敘": "序",
        "臺": "台",
        "得": "德",
        "腿": "腳",
    }
)
_LATIN_NAME_DESCRIPTOR_TOKENS = {
    "bar",
    "bistro",
    "cafe",
    "co",
    "company",
    "dining",
    "gastronomy",
    "grill",
    "kai",
    "kitchen",
    "restaurant",
    "sia",
}
_LATIN_NAME_CONTEXT_TOKENS = {
    "branch",
    "city",
    "district",
    "east",
    "north",
    "south",
    "taipei",
    "taiwan",
    "village",
    "west",
}
_GENERIC_CJK_NAME_TOKENS = {
    "小料理",
    "料理",
    "餐廳",
    "牛排館",
    "牛排",
    "小吃",
}
_FOOD_DESCRIPTOR_TERMS = {
    "麵",
    "面",
    "麵店",
    "米粉",
    "米糕",
    "肉圓",
    "水餃",
    "蒸餃",
    "湯包",
    "豆腐",
    "甜不辣",
    "豬血糕",
    "蔥油餅",
    "飯",
    "粥",
    "菜飯",
    "肉羹",
    "香腸",
    "咖啡",
    "披薩",
    "pizza",
    "noodle",
    "dumpling",
    "rice",
    "congee",
}
_CJK_NAME_PHRASE_REPLACEMENTS = (
    ("專賣", "專門"),
    ("麵攤", "麵店"),
)
_ALIAS_SEPARATOR_PATTERN = re.compile(r"[()（）［］\[\]{}｛｝/／|｜]")
_CJK_HOUSE_NUMBER_PATTERN = re.compile(r"(\d+(?:[-之]\d+)?)\s*號")
_LATIN_HOUSE_NUMBER_PATTERN = re.compile(r"\bno\.?\s*(\d+(?:-\d+)?)\b", re.IGNORECASE)
_LOW_PRECISION_SOURCE_ADDRESS_PATTERN = re.compile(
    r"(?:\d+\s*號\s*(?:[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaffa-z0-9]{1,16})?"
    r"(?:前|旁邊|旁|附近|對面|隔壁|門口|入口)|夜市|市場|門口|入口|附近|對面|隔壁|旁邊)"
)
_NEARBY_OR_LANDMARK_NAME_PATTERN = re.compile(
    r"(?:廟口|路口|街口|巷口|夜市|市場|入口|門口|附近|對面)"
)
_CJK_ADDRESS_COMPONENT_PATTERN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]{2,}(?:大道|路|街|巷|弄)"
)
_CJK_ADDRESS_COMPONENT_PREFIX_PATTERN = re.compile(r"^.*[縣市區鎮鄉村里]")
_CJK_ADDRESS_SUFFIX_PATTERN = re.compile(r"(?:大道|路|街|巷|弄)$")
_ADDRESS_LIKE_PLACE_NAME_PATTERN = re.compile(
    r"^\s*(?:no\.?\s*\d|\d{1,3}\s*$|\d{3,}\s*,|\d+\s*(?:f|floor|樓)\s*$)",
    re.IGNORECASE,
)
_COORDINATE_DMS_PATTERN = re.compile(
    r"""^\s*
    \d{1,3}\s*°\s*\d{1,2}\s*['′]\s*\d{1,2}(?:\.\d+)?\s*["″]\s*[NS]
    [\s,]+
    \d{1,3}\s*°\s*\d{1,2}\s*['′]\s*\d{1,2}(?:\.\d+)?\s*["″]\s*[EW]
    \s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_COORDINATE_DECIMAL_PATTERN = re.compile(
    r"""^\s*
    [+-]?\d{1,2}(?:\.\d+)?\s*,\s*
    [+-]?\d{1,3}(?:\.\d+)?
    \s*$""",
    re.VERBOSE,
)
_FOOD_SERVICE_CATEGORY_KEYWORDS = (
    "restaurant",
    "cafe",
    "coffee",
    "tea",
    "bar",
    "bistro",
    "bakery",
    "izakaya",
    "sushi",
    "ramen",
    "noodle",
    "congee",
    "food",
    "dim sum",
    "deli",
    "taiwanese",
    "chinese",
    "japanese",
    "餐廳",
    "菜館",
    "料理",
    "食堂",
    "麵",
    "面",
    "熟食",
    "小吃",
    "點心",
    "糕",
    "餅",
    "餃",
    "湯包",
    "豆腐",
    "甜點",
    "甜品",
)
_NON_FOOD_CATEGORY_KEYWORDS = (
    "temple",
    "church",
    "shrine",
    "hotel",
    "parking",
    "park",
    "school",
    "workshop",
    "office",
    "tourist attraction",
    "宗教",
    "寺",
    "廟",
    "停車",
    "飯店",
    "酒店",
)
_GENERIC_COMMERCE_CATEGORY_KEYWORDS = (
    "store",
    "shop",
    "商店",
    "商行",
)
_GENERIC_LOCATION_TOKENS = {
    "city",
    "county",
    "district",
    "township",
    "village",
    "taiwan",
    "japan",
    "號",
}
MatchStrength = Literal["strong", "medium", "weak"]


@dataclass(frozen=True)
class NameInputs:
    """Normalized name values supplied to one strategy."""

    row_names: tuple[str, ...]
    candidate_names: tuple[str, ...]


@dataclass(frozen=True)
class NameEvidence:
    """Best name-similarity evidence returned by a strategy."""

    score: float
    strategy: str
    row_name: str
    candidate_name: str
    reasons: tuple[str, ...] = ()

    @property
    def matched(self) -> bool:
        return self.score >= 70.0


class NameStrategy(Protocol):
    """Protocol for an offline name matching strategy."""

    def score(self, inputs: NameInputs) -> NameEvidence: ...


class PlaceCandidateLike(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def address(self) -> str: ...

    @property
    def category(self) -> str: ...

    @property
    def subtitle(self) -> str: ...

    @property
    def located_in(self) -> str: ...


@dataclass(frozen=True)
class PlaceMatchFeatures:
    name_similarity: float
    alias_similarity: float
    address_similarity: float
    city_similarity: float
    house_number_match: float
    category_similarity: float
    located_in_similarity: float
    subtitle_similarity: float
    text_ngram_similarity: float
    local_embedding_similarity: float
    risk_score: float
    disqualifier_score: float
    risk_labels: tuple[str, ...]

    @property
    def combined_positive_evidence(self) -> float:
        return (
            max(self.name_similarity, self.alias_similarity)
            + self.address_similarity
            + self.city_similarity
            + self.house_number_match
            + self.category_similarity
            + self.located_in_similarity
            + self.subtitle_similarity
            + self.text_ngram_similarity
            + self.local_embedding_similarity
        )


@dataclass(frozen=True)
class MatcherDecision:
    accept: bool
    strength: MatchStrength
    score: float
    strategy_id: str
    explanation: tuple[str, ...]


class MatcherStrategy(Protocol):
    strategy_id: str

    def decide(self, features: PlaceMatchFeatures) -> MatcherDecision: ...


@dataclass(frozen=True)
class WeightedEvidenceConfig:
    name_weight: float
    address_weight: float
    city_weight: float
    house_weight: float
    category_weight: float
    located_in_weight: float
    subtitle_weight: float
    text_ngram_weight: float
    local_embedding_weight: float
    medium_threshold: float
    strong_threshold: float
    risk_multiplier: float
    disqualifier_multiplier: float


@dataclass(frozen=True)
class LogisticEvidenceConfig:
    feature_names: tuple[str, ...]
    coefficients: tuple[float, ...]
    intercept: float
    medium_threshold: float
    strong_threshold: float


@dataclass(frozen=True)
class TfIdfNgramConfig:
    ngram_min: int
    ngram_max: int
    similarity_weight: float
    evidence_weight: float
    medium_threshold: float
    strong_threshold: float
    risk_multiplier: float
    disqualifier_multiplier: float


@dataclass(frozen=True)
class LocalEmbeddingConfig:
    provider: str
    model_name: str
    semantic_weight: float
    evidence_weight: float
    medium_threshold: float
    strong_threshold: float
    risk_multiplier: float
    disqualifier_multiplier: float


class WeightedEvidenceStrategy:
    strategy_id = "weighted_evidence_v1"

    def __init__(self, config: WeightedEvidenceConfig) -> None:
        self.config = config

    def decide(self, features: PlaceMatchFeatures) -> MatcherDecision:
        config = self.config
        score = (
            config.name_weight * max(features.name_similarity, features.alias_similarity)
            + config.address_weight * features.address_similarity
            + config.city_weight * features.city_similarity
            + config.house_weight * features.house_number_match
            + config.category_weight * features.category_similarity
            + config.located_in_weight * features.located_in_similarity
            + config.subtitle_weight * features.subtitle_similarity
            + config.text_ngram_weight * features.text_ngram_similarity
            + config.local_embedding_weight * features.local_embedding_similarity
            - config.risk_multiplier * features.risk_score
            - config.disqualifier_multiplier * features.disqualifier_score
        )
        return _decision_from_score(
            strategy_id=self.strategy_id,
            score=score,
            medium_threshold=config.medium_threshold,
            strong_threshold=config.strong_threshold,
            explanation=features.risk_labels,
        )


class LogisticEvidenceStrategy:
    strategy_id = "logistic_evidence_v1"

    def __init__(self, config: LogisticEvidenceConfig) -> None:
        if len(config.feature_names) != len(config.coefficients):
            raise ValueError("Logistic feature names and coefficients must have the same length")
        self.config = config

    def decide(self, features: PlaceMatchFeatures) -> MatcherDecision:
        values = feature_vector_from_names(features, self.config.feature_names)
        z_value = self.config.intercept + sum(
            coefficient * value
            for coefficient, value in zip(self.config.coefficients, values, strict=True)
        )
        score = 100.0 / (1.0 + exp(-z_value))
        return _decision_from_score(
            strategy_id=self.strategy_id,
            score=score,
            medium_threshold=self.config.medium_threshold,
            strong_threshold=self.config.strong_threshold,
            explanation=features.risk_labels,
        )


class TfIdfNgramStrategy:
    strategy_id = "tfidf_ngram_v1"

    def __init__(
        self,
        config: TfIdfNgramConfig,
        evidence_config: WeightedEvidenceConfig,
    ) -> None:
        self.config = config
        self.evidence_strategy = WeightedEvidenceStrategy(evidence_config)

    def decide(self, features: PlaceMatchFeatures) -> MatcherDecision:
        evidence_score = self.evidence_strategy.decide(features).score
        config = self.config
        score = (
            config.similarity_weight * features.text_ngram_similarity
            + config.evidence_weight * evidence_score
            - config.risk_multiplier * features.risk_score
            - config.disqualifier_multiplier * features.disqualifier_score
        )
        return _decision_from_score(
            strategy_id=self.strategy_id,
            score=score,
            medium_threshold=config.medium_threshold,
            strong_threshold=config.strong_threshold,
            explanation=features.risk_labels,
        )


class LocalEmbeddingStrategy:
    strategy_id = "local_embedding_v1"

    def __init__(
        self,
        config: LocalEmbeddingConfig,
        evidence_config: WeightedEvidenceConfig,
    ) -> None:
        self.config = config
        self.evidence_strategy = WeightedEvidenceStrategy(evidence_config)

    def decide(self, features: PlaceMatchFeatures) -> MatcherDecision:
        evidence_score = self.evidence_strategy.decide(features).score
        config = self.config
        score = (
            config.semantic_weight * features.local_embedding_similarity
            + config.evidence_weight * evidence_score
            - config.risk_multiplier * features.risk_score
            - config.disqualifier_multiplier * features.disqualifier_score
        )
        return _decision_from_score(
            strategy_id=self.strategy_id,
            score=score,
            medium_threshold=config.medium_threshold,
            strong_threshold=config.strong_threshold,
            explanation=features.risk_labels,
        )


def build_name_inputs(*, row_names: Sequence[str], candidate_names: Sequence[str]) -> NameInputs:
    """Normalize and deduplicate row/candidate names for strategy evaluation."""

    return NameInputs(
        row_names=_deduplicate_names(row_names),
        candidate_names=_deduplicate_names(candidate_names),
    )


class BaselineNameStrategy:
    """Conservative exact, subset, and compact substring strategy."""

    strategy = "baseline"

    def score(self, inputs: NameInputs) -> NameEvidence:
        best = NameEvidence(score=0.0, strategy=self.strategy, row_name="", candidate_name="")
        for row_name in inputs.row_names:
            for candidate_name in inputs.candidate_names:
                score, reasons = _baseline_score(row_name, candidate_name)
                if score > best.score:
                    best = NameEvidence(
                        score=score,
                        strategy=self.strategy,
                        row_name=row_name,
                        candidate_name=candidate_name,
                        reasons=tuple(reasons),
                    )
        return best


class RapidFuzzNameStrategy:
    """RapidFuzz strategy for local fuzzy similarity."""

    strategy = "rapidfuzz"

    def score(self, inputs: NameInputs) -> NameEvidence:
        best = NameEvidence(score=0.0, strategy=self.strategy, row_name="", candidate_name="")
        for row_name in inputs.row_names:
            for candidate_name in inputs.candidate_names:
                score = max(
                    fuzz.WRatio(row_name, candidate_name),
                    fuzz.token_set_ratio(row_name, candidate_name),
                    fuzz.partial_ratio(row_name, candidate_name),
                    fuzz.ratio(_compact_mixed_name(row_name), _compact_mixed_name(candidate_name)),
                )
                score = _cap_single_latin_token_overmatch(row_name, candidate_name, float(score))
                if score > best.score:
                    best = NameEvidence(
                        score=float(score),
                        strategy=self.strategy,
                        row_name=row_name,
                        candidate_name=candidate_name,
                        reasons=("rapidfuzz_similarity",),
                    )
        return best


class CharacterNgramNameStrategy:
    """Character n-gram strategy for CJK and compact mixed names."""

    strategy = "character-ngram"

    def score(self, inputs: NameInputs) -> NameEvidence:
        best = NameEvidence(score=0.0, strategy=self.strategy, row_name="", candidate_name="")
        for row_name in inputs.row_names:
            for candidate_name in inputs.candidate_names:
                row_compact = _compact_mixed_name(row_name)
                candidate_compact = _compact_mixed_name(candidate_name)
                score = max(
                    _dice_score(_ngrams(row_compact, 2), _ngrams(candidate_compact, 2)),
                    _dice_score(_ngrams(row_compact, 3), _ngrams(candidate_compact, 3)),
                    _containment_score(_ngrams(row_compact, 2), _ngrams(candidate_compact, 2)),
                    _containment_score(_ngrams(row_compact, 3), _ngrams(candidate_compact, 3)),
                )
                if score >= 65.0 and _has_shared_compact_prefix(row_compact, candidate_compact):
                    score = max(score, 72.0)
                score = _cap_single_latin_token_overmatch(row_name, candidate_name, score)
                if score > best.score:
                    best = NameEvidence(
                        score=score,
                        strategy=self.strategy,
                        row_name=row_name,
                        candidate_name=candidate_name,
                        reasons=("character_ngram_overlap",),
                    )
        return best


class DescriptorAwareNameStrategy:
    """Descriptor-aware strategy that discounts conflicting food names."""

    strategy = "descriptor-aware"

    def score(self, inputs: NameInputs) -> NameEvidence:
        best_base = max(
            (RapidFuzzNameStrategy().score(inputs), CharacterNgramNameStrategy().score(inputs)),
            key=lambda evidence: evidence.score,
        )
        row_terms = _food_descriptor_terms(best_base.row_name)
        candidate_terms = _food_descriptor_terms(best_base.candidate_name)
        if row_terms and candidate_terms and row_terms.isdisjoint(candidate_terms):
            return NameEvidence(
                score=min(best_base.score, 45.0),
                strategy=self.strategy,
                row_name=best_base.row_name,
                candidate_name=best_base.candidate_name,
                reasons=best_base.reasons + ("food_descriptor_conflict",),
            )
        return NameEvidence(
            score=best_base.score,
            strategy=self.strategy,
            row_name=best_base.row_name,
            candidate_name=best_base.candidate_name,
            reasons=best_base.reasons,
        )


def evaluate_name_evidence(inputs: NameInputs) -> NameEvidence:
    """Run all production name strategies and return the best safe evidence."""

    strategy_results = (
        BaselineNameStrategy().score(inputs),
        RapidFuzzNameStrategy().score(inputs),
        CharacterNgramNameStrategy().score(inputs),
        DescriptorAwareNameStrategy().score(inputs),
    )
    descriptor_result = strategy_results[-1]
    if "food_descriptor_conflict" in descriptor_result.reasons:
        return descriptor_result
    return max(strategy_results[:-1], key=lambda evidence: evidence.score)


def extract_name_alternatives(name: str) -> tuple[str, ...]:
    """Extract primary name and aliases without applying place-specific rules."""

    raw_parts = [part.strip() for part in _ALIAS_SEPARATOR_PATTERN.split(name) if part.strip()]
    alternatives: list[str] = []
    seen: set[str] = set()
    for part in (name.strip(), *raw_parts):
        key = normalize_name(part)
        if not key or key in seen:
            continue
        alternatives.append(" ".join(part.split()))
        seen.add(key)
    for part in raw_parts:
        cjk_only = "".join(_CJK_SEQUENCE_PATTERN.findall(part))
        if len(cjk_only) < 2:
            continue
        key = normalize_name(cjk_only)
        if key not in seen:
            alternatives.append(cjk_only)
            seen.add(key)
    return tuple(alternatives)


def extract_place_match_features(
    row: Mapping[str, Any],
    candidate: PlaceCandidateLike,
) -> PlaceMatchFeatures:
    """Extract cross-column place evidence for continuous-score matchers."""

    source_names = extract_name_alternatives(str(row.get("Name") or ""))
    aliases = row.get("Aliases", ())
    if isinstance(aliases, str):
        source_names = (*source_names, *extract_name_alternatives(aliases))
    elif isinstance(aliases, Sequence):
        for alias in aliases:
            source_names = (*source_names, *extract_name_alternatives(str(alias)))
    candidate_names = extract_name_alternatives(candidate.name)
    if candidate.subtitle:
        candidate_names = (*candidate_names, *extract_name_alternatives(candidate.subtitle))

    name_similarity = _best_name_similarity(source_names[:1], candidate_names)
    alias_similarity = _best_name_similarity(source_names, candidate_names)
    address_similarity = _score_address_similarity(row, candidate)
    city_similarity = _score_city_similarity(row, candidate)
    house_number_match = _score_house_number_evidence(row, candidate)
    category_similarity = _score_category_similarity(row, candidate)
    located_in_similarity = _best_text_similarity(source_names, (candidate.located_in,))
    subtitle_similarity = _best_text_similarity(source_names, (candidate.subtitle,))
    text_ngram_similarity = _text_ngram_similarity(
        _combined_source_text(row, source_names),
        _combined_candidate_text(candidate, candidate_names),
        min_size=2,
        max_size=4,
    )
    risk_labels_list = _detect_generic_risk_labels(row, candidate)
    if (
        max(name_similarity, alias_similarity) >= 95.0
        and address_similarity < 30.0
        and house_number_match == 0.0
        and category_similarity < 90.0
    ):
        risk_labels_list.append("exact_name_with_weak_address")
    if (
        max(name_similarity, alias_similarity) >= 95.0
        and address_similarity < 30.0
        and house_number_match == 0.0
        and text_ngram_similarity < 15.0
    ):
        risk_labels_list.append("exact_name_with_weak_context")
    if (
        max(name_similarity, alias_similarity) < 50.0
        and not normalize_text(str(row.get("Cuisine") or ""))
        and not (
            address_similarity >= 45.0
            and city_similarity >= 70.0
            and house_number_match >= 90.0
        )
    ):
        risk_labels_list.append("no_name_evidence_without_source_cuisine")
    if (
        max(name_similarity, alias_similarity) < 10.0
        and category_similarity >= 90.0
        and not normalize_text(str(row.get("Cuisine") or ""))
        and text_ngram_similarity < 15.0
    ):
        risk_labels_list.append("address_only_with_named_category")
    if _has_short_latin_fuzzy_name_without_place_anchor(
        source_names=source_names,
        candidate_names=candidate_names,
        name_similarity=max(name_similarity, alias_similarity),
        address_similarity=address_similarity,
        house_number_match=house_number_match,
    ):
        risk_labels_list.append("short_latin_fuzzy_name_without_place_anchor")
    if (
        "house_number_conflict" in risk_labels_list
        and _has_precise_identity_for_nearby_house_number(
            row=row,
            candidate=candidate,
            source_names=source_names,
            subtitle_similarity=subtitle_similarity,
        )
    ):
        risk_labels_list.append("strong_nearby_house_number_identity")
    risk_labels = tuple(risk_labels_list)
    return PlaceMatchFeatures(
        name_similarity=name_similarity,
        alias_similarity=alias_similarity,
        address_similarity=address_similarity,
        city_similarity=city_similarity,
        house_number_match=house_number_match,
        category_similarity=category_similarity,
        located_in_similarity=located_in_similarity,
        subtitle_similarity=subtitle_similarity,
        text_ngram_similarity=text_ngram_similarity,
        local_embedding_similarity=0.0,
        risk_score=_score_soft_risks(
            risk_labels,
            name_similarity=name_similarity,
            alias_similarity=alias_similarity,
            address_similarity=address_similarity,
            city_similarity=city_similarity,
            category_similarity=category_similarity,
            subtitle_similarity=subtitle_similarity,
            text_ngram_similarity=text_ngram_similarity,
        ),
        disqualifier_score=_score_disqualifiers(risk_labels),
        risk_labels=risk_labels,
    )


def feature_vector_from_names(
    features: PlaceMatchFeatures,
    feature_names: Sequence[str],
) -> tuple[float, ...]:
    values: list[float] = []
    for name in feature_names:
        value = getattr(features, name)
        if not isinstance(value, (int, float)):
            raise TypeError(f"Feature {name} is not numeric")
        values.append(float(value) / 100.0)
    return tuple(values)


def _decision_from_score(
    *,
    strategy_id: str,
    score: float,
    medium_threshold: float,
    strong_threshold: float,
    explanation: tuple[str, ...],
) -> MatcherDecision:
    bounded_score = max(0.0, min(100.0, score))
    if bounded_score >= strong_threshold:
        strength: MatchStrength = "strong"
    elif bounded_score >= medium_threshold:
        strength = "medium"
    else:
        strength = "weak"
    return MatcherDecision(
        accept=strength != "weak",
        strength=strength,
        score=bounded_score,
        strategy_id=strategy_id,
        explanation=explanation,
    )


def _best_name_similarity(
    source_names: Sequence[str],
    candidate_names: Sequence[str],
) -> float:
    inputs = build_name_inputs(row_names=source_names, candidate_names=candidate_names)
    if not inputs.row_names or not inputs.candidate_names:
        return 0.0
    return evaluate_name_evidence(inputs).score


def _best_text_similarity(left_values: Sequence[str], right_values: Sequence[str]) -> float:
    best = 0.0
    for left in left_values:
        for right in right_values:
            if not left or not right:
                continue
            best = max(best, float(fuzz.WRatio(normalize_text(left), normalize_text(right))))
    return best


def _score_address_similarity(row: Mapping[str, Any], candidate: PlaceCandidateLike) -> float:
    row_address = normalize_text(str(row.get("Address") or ""))
    candidate_address = normalize_text(candidate.address)
    row_tokens = _significant_address_tokens(row_address)
    candidate_tokens = _significant_address_tokens(candidate_address)
    score = _token_overlap_score(row_tokens, candidate_tokens, maximum=55.0)
    if _score_city_similarity(row, candidate) >= 70.0:
        score += 20.0
    if _score_house_number_evidence(row, candidate) >= 90.0:
        score += 25.0
    return min(100.0, score)


def _score_city_similarity(row: Mapping[str, Any], candidate: PlaceCandidateLike) -> float:
    city = normalize_text(str(row.get("City") or ""))
    if not city:
        return 0.0
    row_address = normalize_text(str(row.get("Address") or ""))
    candidate_address = normalize_text(candidate.address)
    city_tokens = tokenize(city)
    candidate_tokens = tokenize(candidate_address)
    if city_tokens.intersection(candidate_tokens):
        return 100.0
    compact_city = _compact_mixed_name(city)
    compact_candidate = _compact_mixed_name(candidate_address)
    compact_row_address = _compact_mixed_name(row_address)
    if compact_city and (
        compact_city in compact_candidate
        or (compact_city in compact_row_address and compact_row_address[:2] in compact_candidate)
    ):
        return 90.0
    return 0.0


def _score_house_number_evidence(row: Mapping[str, Any], candidate: PlaceCandidateLike) -> float:
    row_numbers = _extract_house_number_tokens(str(row.get("Address") or ""))
    candidate_numbers = _extract_house_number_tokens(candidate.address)
    if row_numbers and candidate_numbers and row_numbers.intersection(candidate_numbers):
        return 100.0
    if row_numbers and candidate_numbers:
        row_roots = {_house_number_root(number) for number in row_numbers}
        candidate_roots = {_house_number_root(number) for number in candidate_numbers}
        if None not in row_roots and row_roots.intersection(candidate_roots):
            return 70.0
    row_numeric_tokens = _numeric_address_tokens(str(row.get("Address") or ""))
    candidate_numeric_tokens = _numeric_address_tokens(candidate.address)
    if len(row_numeric_tokens.intersection(candidate_numeric_tokens)) >= 2:
        return 70.0
    return 0.0


def _score_category_similarity(row: Mapping[str, Any], candidate: PlaceCandidateLike) -> float:
    category = normalize_text(candidate.category)
    cuisine = normalize_text(str(row.get("Cuisine") or ""))
    if _is_food_service_category(category):
        return 100.0
    if cuisine and tokenize(cuisine).intersection(tokenize(category)):
        return 90.0
    if not category:
        return 45.0
    return 0.0


def _detect_generic_risk_labels(
    row: Mapping[str, Any],
    candidate: PlaceCandidateLike,
) -> list[str]:
    labels: list[str] = []
    if (
        _COORDINATE_DECIMAL_PATTERN.match(candidate.name.strip())
        or _COORDINATE_DMS_PATTERN.match(candidate.name.strip())
    ):
        labels.append("coordinate_like_candidate_name")
    if _ADDRESS_LIKE_PLACE_NAME_PATTERN.search(candidate.name.strip()):
        labels.append("address_like_candidate_name")
    if _has_house_number_conflict(str(row.get("Address") or ""), candidate.address):
        labels.append("house_number_conflict")
    category = normalize_text(candidate.category)
    category_is_food_service = _is_food_service_category(category)
    if category and not category_is_food_service and _is_non_food_category(category):
        labels.append("non_food_category")
    name_inputs = build_name_inputs(
        row_names=extract_name_alternatives(str(row.get("Name") or "")),
        candidate_names=extract_name_alternatives(candidate.name),
    )
    name_evidence = evaluate_name_evidence(name_inputs) if name_inputs.row_names else None
    if (
        category
        and not category_is_food_service
        and _is_generic_commerce_category(category)
        and (name_evidence is None or name_evidence.score < 50.0)
    ):
        labels.append("generic_commerce_category_without_name_evidence")
    if name_evidence and "food_descriptor_conflict" in name_evidence.reasons:
        labels.append("food_descriptor_conflict")
    if (
        name_evidence
        and name_evidence.matched
        and has_cjk_proper_prefix_conflict(name_evidence.row_name, name_evidence.candidate_name)
    ):
        labels.append("cjk_proper_prefix_conflict")
    return labels


def _has_precise_identity_for_nearby_house_number(
    *,
    row: Mapping[str, Any],
    candidate: PlaceCandidateLike,
    source_names: Sequence[str],
    subtitle_similarity: float,
) -> bool:
    if subtitle_similarity >= 95.0:
        return True
    source_compact_names = {_compact_mixed_name(name) for name in source_names if _compact_mixed_name(name)}
    candidate_compact_name = _compact_mixed_name(candidate.name)
    if candidate_compact_name in source_compact_names:
        return _has_nearby_or_landmark_anchor(row)
    branch_labels = _candidate_branch_labels(candidate.name)
    if not branch_labels:
        return False
    row_context = _compact_mixed_name(
        " ".join(
            (
                str(row.get("Name") or ""),
                str(row.get("NameLocal") or ""),
                str(row.get("Address") or ""),
                str(row.get("City") or ""),
            )
        )
    )
    return any(_compact_mixed_name(label) in row_context for label in branch_labels if _compact_mixed_name(label))


def _has_nearby_or_landmark_anchor(row: Mapping[str, Any]) -> bool:
    row_address = str(row.get("Address") or "")
    if _has_low_precision_source_address(row_address):
        return True
    row_name_context = " ".join((str(row.get("Name") or ""), str(row.get("NameLocal") or "")))
    return bool(_NEARBY_OR_LANDMARK_NAME_PATTERN.search(row_name_context))


def _candidate_branch_labels(candidate_name: str) -> tuple[str, ...]:
    labels: list[str] = []
    for content in re.findall(r"[（(]([^）)]{1,12})[）)]", candidate_name):
        label = _normalize_branch_label(content)
        if label:
            labels.append(label)
    for content in re.findall(r"[-－—]\s*([\u3400-\u9fff]{1,8})(?:直營)?店", candidate_name):
        label = _normalize_branch_label(content)
        if label:
            labels.append(label)
    return tuple(labels)


def _normalize_branch_label(value: str) -> str:
    label = re.sub(r"(?:直營)?(?:本|分)?店$", "", value.strip())
    label = label.strip()
    if label in {"", "本", "總", "直營"}:
        return ""
    return label


def _has_strong_identity_for_nearby_house_number(
    *,
    name_similarity: float,
    alias_similarity: float,
    category_similarity: float,
    address_similarity: float,
    city_similarity: float,
    subtitle_similarity: float,
    text_ngram_similarity: float,
) -> bool:
    identity_score = max(name_similarity, alias_similarity, subtitle_similarity)
    if identity_score < 95.0:
        return False
    if category_similarity < 90.0:
        return False
    if city_similarity < 70.0 and address_similarity < 25.0:
        return False
    if address_similarity < 25.0 and text_ngram_similarity < 20.0:
        return False
    return True


def _score_soft_risks(
    risk_labels: Sequence[str],
    *,
    name_similarity: float,
    alias_similarity: float,
    address_similarity: float,
    city_similarity: float,
    category_similarity: float,
    subtitle_similarity: float,
    text_ngram_similarity: float,
) -> float:
    score = 0.0
    identity_similarity = max(name_similarity, alias_similarity, subtitle_similarity)
    has_precise_nearby_identity = "strong_nearby_house_number_identity" in risk_labels
    for label in risk_labels:
        if label == "house_number_conflict":
            if has_precise_nearby_identity and _has_strong_identity_for_nearby_house_number(
                name_similarity=name_similarity,
                alias_similarity=alias_similarity,
                category_similarity=category_similarity,
                address_similarity=address_similarity,
                city_similarity=city_similarity,
                subtitle_similarity=subtitle_similarity,
                text_ngram_similarity=text_ngram_similarity,
            ):
                score += 12.0
            elif identity_similarity >= 95.0 and address_similarity < 45.0:
                score += 85.0
            elif address_similarity < 45.0:
                score += 28.0
            else:
                score += 4.0
        elif label == "cjk_proper_prefix_conflict":
            score += 12.0
        elif label == "food_descriptor_conflict":
            if identity_similarity < 70.0:
                score += 75.0
            elif identity_similarity >= 95.0 and address_similarity >= 30.0:
                score += 8.0
            else:
                score += 35.0
        elif label == "exact_name_with_weak_address":
            score += 18.0
        elif label == "exact_name_with_weak_context":
            score += 40.0
        elif label == "no_name_evidence_without_source_cuisine":
            score += 30.0
        elif label == "address_only_with_named_category":
            score += 35.0
        elif label == "generic_commerce_category_without_name_evidence":
            score += 30.0
        elif label == "short_latin_fuzzy_name_without_place_anchor":
            score += 75.0
    return score


def _score_disqualifiers(risk_labels: Sequence[str]) -> float:
    disqualifiers = {
        "coordinate_like_candidate_name",
        "address_like_candidate_name",
        "non_food_category",
    }
    return 100.0 if any(label in disqualifiers for label in risk_labels) else 0.0


def _has_short_latin_fuzzy_name_without_place_anchor(
    *,
    source_names: Sequence[str],
    candidate_names: Sequence[str],
    name_similarity: float,
    address_similarity: float,
    house_number_match: float,
) -> bool:
    if name_similarity < 70.0 or name_similarity >= 95.0:
        return False
    if house_number_match >= 70.0 or address_similarity >= 85.0:
        return False
    if not source_names:
        return False

    source_tokens = _latin_identity_tokens(source_names[0])
    if len(source_tokens) < 2 or len(source_tokens) > 3:
        return False

    for candidate_name in candidate_names:
        candidate_tokens = _latin_identity_tokens(candidate_name)
        if not candidate_tokens:
            continue
        if source_tokens.issubset(candidate_tokens):
            return False
        if len(candidate_tokens - source_tokens) > 1:
            return True
    return False


def _latin_identity_tokens(value: str) -> set[str]:
    if contains_cjk_characters(value):
        return set()
    return {
        token
        for token in _ASCII_WORD_OR_NUMBER_PATTERN.findall(normalize_text(value))
        if len(token) > 1
        and token not in _LATIN_NAME_DESCRIPTOR_TOKENS
        and token not in _LATIN_NAME_CONTEXT_TOKENS
    }


def _combined_source_text(row: Mapping[str, Any], names: Sequence[str]) -> str:
    return " ".join(
        (
            *names,
            str(row.get("Address") or ""),
            str(row.get("City") or ""),
            str(row.get("Cuisine") or ""),
        )
    )


def _combined_candidate_text(
    candidate: PlaceCandidateLike,
    names: Sequence[str],
) -> str:
    return " ".join(
        (
            *names,
            candidate.address,
            candidate.category,
            candidate.subtitle,
            candidate.located_in,
        )
    )


def _text_ngram_similarity(
    left: str,
    right: str,
    *,
    min_size: int,
    max_size: int,
) -> float:
    left_counter = _text_ngram_counter(left, min_size=min_size, max_size=max_size)
    right_counter = _text_ngram_counter(right, min_size=min_size, max_size=max_size)
    if not left_counter or not right_counter:
        return 0.0
    dot = sum(count * right_counter.get(token, 0) for token, count in left_counter.items())
    left_norm = sqrt(sum(count * count for count in left_counter.values()))
    right_norm = sqrt(sum(count * count for count in right_counter.values()))
    if not left_norm or not right_norm:
        return 0.0
    return 100.0 * dot / (left_norm * right_norm)


def _text_ngram_counter(
    value: str,
    *,
    min_size: int,
    max_size: int,
) -> Counter[str]:
    compact = _compact_mixed_name(value)
    counter: Counter[str] = Counter()
    for size in range(min_size, max_size + 1):
        counter.update(_ngrams(compact, size))
    return counter


def _significant_address_tokens(value: str) -> set[str]:
    tokens = tokenize(value)
    tokens.update(_cjk_address_pinyin_tokens(value))
    return {
        token
        for token in tokens
        if len(token) > 1 and token not in _GENERIC_LOCATION_TOKENS
    }


def _cjk_address_pinyin_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for match in _CJK_ADDRESS_COMPONENT_PATTERN.finditer(value):
        component = _CJK_ADDRESS_COMPONENT_PREFIX_PATTERN.sub("", match.group(0))
        stem = _CJK_ADDRESS_SUFFIX_PATTERN.sub("", component)
        for part in (component, stem):
            compact = "".join(lazy_pinyin(part))
            if len(compact) > 1:
                tokens.add(compact)
    return tokens


def _token_overlap_score(left: set[str], right: set[str], *, maximum: float) -> float:
    if not left or not right:
        return 0.0
    overlap = left.intersection(right)
    return min(maximum, maximum * len(overlap) / max(1, min(len(left), len(right))))


def _is_food_service_category(value: str) -> bool:
    return any(keyword in value for keyword in _FOOD_SERVICE_CATEGORY_KEYWORDS)


def _is_non_food_category(value: str) -> bool:
    return any(keyword in value for keyword in _NON_FOOD_CATEGORY_KEYWORDS)


def _is_generic_commerce_category(value: str) -> bool:
    return any(keyword in value for keyword in _GENERIC_COMMERCE_CATEGORY_KEYWORDS)


def _extract_house_number_tokens(value: str) -> set[str]:
    normalized_value = normalize_text(value).replace("之", "-")
    return {
        match.group(1).replace("之", "-")
        for pattern in (_CJK_HOUSE_NUMBER_PATTERN, _LATIN_HOUSE_NUMBER_PATTERN)
        for match in pattern.finditer(normalized_value)
    }


def _numeric_address_tokens(value: str) -> set[str]:
    return {
        token
        for token in _ASCII_WORD_OR_NUMBER_PATTERN.findall(normalize_text(value))
        if token.isdigit()
    }


def _house_number_root(value: str) -> int | None:
    root = value.split("-", 1)[0]
    if not root.isdigit():
        return None
    return int(root)


def _has_house_number_conflict(row_address: str, candidate_address: str) -> bool:
    if _has_low_precision_source_address(row_address):
        return False
    row_house_numbers = _extract_house_number_tokens(row_address)
    candidate_house_numbers = _extract_house_number_tokens(candidate_address)
    if not row_house_numbers or not candidate_house_numbers:
        return False
    if row_house_numbers.intersection(candidate_house_numbers):
        return False
    row_roots = {
        root for token in row_house_numbers if (root := _house_number_root(token)) is not None
    }
    candidate_roots = {
        root
        for token in candidate_house_numbers
        if (root := _house_number_root(token)) is not None
    }
    if not row_roots or not candidate_roots:
        return True
    return all(abs(row_root - candidate_root) > 1 for row_root in row_roots for candidate_root in candidate_roots)


def _has_low_precision_source_address(row_address: str) -> bool:
    return bool(_LOW_PRECISION_SOURCE_ADDRESS_PATTERN.search(normalize_text(row_address)))


def has_house_number_conflict(row_address: str, candidate_address: str) -> bool:
    return _has_house_number_conflict(row_address, candidate_address)


def normalize_name(value: str) -> str:
    """Normalize a place name for local comparisons."""

    normalized = normalize_text(value).translate(_CJK_NAME_VARIANT_TRANSLATION)
    for source, replacement in _CJK_NAME_PHRASE_REPLACEMENTS:
        normalized = normalized.replace(source, replacement)
    return normalized


def normalize_text(value: str) -> str:
    """Casefold, strip accents, and transliterate supported scripts."""

    folded = value.strip().casefold()
    decomposed = unicodedata.normalize("NFKD", folded)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    transliterated = "".join(
        _GREEK_TRANSLITERATION.get(character, character)
        for character in without_marks
    )
    return " ".join(transliterated.split())


def tokenize(value: str) -> set[str]:
    """Tokenize words and numbers after text normalization."""

    normalized_value = normalize_text(value)
    return {token for token in _WORD_OR_NUMBER_PATTERN.findall(normalized_value) if token}


def contains_cjk_characters(value: str) -> bool:
    """Return True when text contains CJK or kana characters."""

    return bool(_CJK_CHARACTER_PATTERN.search(value))


def has_cjk_proper_prefix_conflict(row_name: str, candidate_name: str) -> bool:
    """Return True when CJK names share descriptors but identify different prefixes."""

    row_compact = _compact_cjk_name(row_name)
    candidate_compact = _compact_cjk_name(candidate_name)
    if min(len(row_compact), len(candidate_compact)) < 4:
        return False
    if row_compact[0] == candidate_compact[0]:
        return False
    row_prefix = row_compact[:2]
    candidate_prefix = candidate_compact[:2]
    return candidate_prefix not in row_compact and row_prefix not in candidate_compact


def _deduplicate_names(values: Sequence[str]) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_name(str(value))
        if not normalized or normalized in seen:
            continue
        names.append(normalized)
        seen.add(normalized)
    return tuple(names)


def _baseline_score(row_name: str, candidate_name: str) -> tuple[float, list[str]]:
    if row_name == candidate_name:
        return 100.0, ["exact"]
    row_tokens = tokenize(row_name)
    candidate_tokens = tokenize(candidate_name)
    if len(row_tokens) >= 2 and row_tokens.issubset(candidate_tokens):
        return _cap_single_latin_token_overmatch(row_name, candidate_name, 96.0), ["row_tokens_subset"]
    if len(candidate_tokens) >= 2 and candidate_tokens.issubset(row_tokens):
        return 92.0, ["candidate_tokens_subset"]
    if len(row_tokens) == 1 and row_tokens.issubset(candidate_tokens):
        token = next(iter(row_tokens))
        meaningful_candidate_tokens = candidate_tokens - _LATIN_NAME_DESCRIPTOR_TOKENS
        if contains_cjk_characters(token) or len(meaningful_candidate_tokens) <= 2:
            return 88.0, ["single_token_subset"]
    if _primary_cjk_token_overlap(row_name, candidate_name):
        return 84.0, ["primary_cjk_token_overlap"]
    if _compact_substring_score(row_name, candidate_name):
        return _cap_single_latin_token_overmatch(row_name, candidate_name, 82.0), ["compact_substring"]
    if _latin_substring_score(row_name, candidate_name):
        return _cap_single_latin_token_overmatch(row_name, candidate_name, 78.0), ["latin_substring"]
    return 0.0, []


def _primary_cjk_token_overlap(row_name: str, candidate_name: str) -> bool:
    row_tokens = _primary_cjk_name_tokens(row_name)
    if not row_tokens:
        return False
    candidate_text = normalize_text(candidate_name)
    candidate_tokens = _primary_cjk_name_tokens(candidate_name)
    return any(row_token in candidate_tokens or row_token in candidate_text for row_token in row_tokens)


def _primary_cjk_name_tokens(value: str) -> tuple[str, ...]:
    stripped_value = _PARENTHETICAL_SEGMENT_PATTERN.sub(" ", value).strip()
    return tuple(
        token
        for token in _CJK_SEQUENCE_PATTERN.findall(stripped_value)
        if len(token) >= 3 and token not in _GENERIC_CJK_NAME_TOKENS
    )


def _compact_substring_score(row_name: str, candidate_name: str) -> bool:
    row_compact = _compact_mixed_name(row_name)
    candidate_compact = _compact_mixed_name(candidate_name)
    if not row_compact or not candidate_compact:
        return False
    shorter_name, longer_name = sorted((row_compact, candidate_compact), key=len)
    return len(shorter_name) >= 4 and shorter_name in longer_name


def _latin_substring_score(row_name: str, candidate_name: str) -> bool:
    shorter_name, longer_name = sorted((normalize_text(row_name), normalize_text(candidate_name)), key=len)
    if len(shorter_name) < 4:
        return False
    longer_tokens = _WORD_OR_NUMBER_PATTERN.findall(longer_name)
    return any(shorter_name in token and shorter_name != token for token in longer_tokens)


def _compact_mixed_name(value: str) -> str:
    normalized_value = _PARENTHETICAL_BRACKET_PATTERN.sub(" ", normalize_name(value))
    return "".join(_NAME_COMPACT_TOKEN_PATTERN.findall(normalized_value))


def _compact_cjk_name(value: str) -> str:
    normalized_value = _PARENTHETICAL_BRACKET_PATTERN.sub(" ", normalize_name(value))
    return "".join(_CJK_SEQUENCE_PATTERN.findall(normalized_value))


def _ngrams(value: str, size: int) -> set[str]:
    if len(value) < size:
        return {value} if value else set()
    return {value[index : index + size] for index in range(0, len(value) - size + 1)}


def _dice_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return 200.0 * len(left.intersection(right)) / (len(left) + len(right))


def _containment_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return 100.0 * len(left.intersection(right)) / min(len(left), len(right))


def _has_shared_compact_prefix(left: str, right: str) -> bool:
    if min(len(left), len(right)) < 3:
        return False
    return left[:3] == right[:3]


def _food_descriptor_terms(value: str) -> set[str]:
    normalized = normalize_name(value)
    terms: set[str] = set()
    for term in _FOOD_DESCRIPTOR_TERMS:
        if term in normalized:
            terms.add(term)
    ascii_tokens = set(_ASCII_WORD_OR_NUMBER_PATTERN.findall(normalized))
    return terms.union(ascii_tokens.intersection(_FOOD_DESCRIPTOR_TERMS))


def _cap_single_latin_token_overmatch(row_name: str, candidate_name: str, score: float) -> float:
    row_tokens = set(_ASCII_WORD_OR_NUMBER_PATTERN.findall(row_name))
    candidate_tokens = set(_ASCII_WORD_OR_NUMBER_PATTERN.findall(candidate_name))
    if len(row_tokens) != 1 or not row_tokens.issubset(candidate_tokens):
        return score
    meaningful_candidate_tokens = candidate_tokens - _LATIN_NAME_DESCRIPTOR_TOKENS
    if len(meaningful_candidate_tokens) > 2:
        return min(score, 55.0)
    return score
