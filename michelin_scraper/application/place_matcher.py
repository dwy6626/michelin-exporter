"""Heuristics for matching Google Maps candidate places to Michelin rows."""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from .place_matcher_strategies import (
    NameEvidence,
    NameInputs,
    WeightedEvidenceConfig,
    WeightedEvidenceStrategy,
    build_name_inputs,
    evaluate_name_evidence,
    extract_place_match_features,
    has_cjk_proper_prefix_conflict,
)
from .place_matcher_strategies import (
    has_house_number_conflict as _strategy_has_house_number_conflict,
)

MatchStrength = Literal["strong", "medium", "weak"]
_POSTAL_CODE_PATTERN = re.compile(r"\b\d{3}-\d{4}\b|\b\d{7}\b")
_WORD_OR_NUMBER_PATTERN = re.compile(r"[^\W\d_]+|\d+")
_ASCII_WORD_OR_NUMBER_PATTERN = re.compile(r"[a-z0-9]+")
_CJK_CHARACTER_PATTERN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CJK_SEQUENCE_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_NAME_COMPACT_TOKEN_PATTERN = re.compile(r"[a-z0-9\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_PARENTHETICAL_SEGMENT_PATTERN = re.compile(r"\s*[（(][^）)]{1,24}[）)]\s*")
_PARENTHETICAL_BRACKET_PATTERN = re.compile(r"[（）()]")
_ADDRESS_LIKE_PLACE_NAME_PATTERN = re.compile(
    r"^\s*(?:no\.?\s*\d|\d+\s*(?:f|floor|樓)\s*$)",
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
_GENERIC_LOCATION_TOKENS = {
    "japan",
    "city",
    "ku",
    "ward",
    "prefecture",
    "tokyo",
}
_NON_CATEGORY_PLACEHOLDERS = {
    "add a label",
}
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
    "vegan",
    "vegetarian",
    "hot pot",
    "bbq",
    "grill",
    "dining",
    "kitchen",
    "eatery",
    "congee",
    "food",
    "dim sum",
    "steak",
    "seafood",
    "breakfast",
    "deli",
    "thai",
    "japanese",
    "chinese",
    "taiwanese",
    "cantonese",
    "french",
    "italian",
    "asian",
    "餐廳",
    "菜館",
    "中菜",
    "料理",
    "食堂",
    "居酒屋",
    "壽司",
    "拉麵",
    "麵",
    "面",
    "火鍋",
    "素食",
    "蔬食",
    "咖啡",
    "酒吧",
    "小吃",
    "點心",
    "牛排",
    "海鮮",
    "早餐",
    "茶館",
    "燒肉",
    "披薩",
    "薄餅",
    "扒房",
    "熟食",
    "麵食",
    "餃子",
    "湯包",
    "包點",
    "包子",
    "糕餅",
    "糕",
    "餅",
    "豆腐",
    "小食",
    "零食",
    "甜點",
    "甜品",
)
_GENERIC_CJK_NAME_TOKENS = {
    "小料理",
    "料理",
    "餐廳",
    "牛排館",
    "牛排",
    "小吃",
}
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
    }
)
_CJK_NAME_PHRASE_REPLACEMENTS = (
    ("專賣", "專門"),
    ("麵攤", "麵店"),
)
PRODUCTION_MATCHER_STRATEGY_ID = "weighted_evidence_v1"
PRODUCTION_MATCHER_CONFIG = WeightedEvidenceConfig(
    name_weight=0.26,
    address_weight=0.18,
    city_weight=0.14,
    house_weight=0.08,
    category_weight=0.08,
    located_in_weight=0.04,
    subtitle_weight=0.04,
    text_ngram_weight=0.16,
    local_embedding_weight=0.00,
    medium_threshold=35.0,
    strong_threshold=66.0,
    risk_multiplier=0.50,
    disqualifier_multiplier=1.00,
)
PRODUCTION_DISQUALIFIER_THRESHOLD = 100.0


@dataclass(frozen=True)
class PlaceCandidate:
    """Candidate place metadata extracted from Google Maps UI."""

    name: str
    address: str
    category: str
    subtitle: str = ""
    located_in: str = ""


@dataclass(frozen=True)
class PlaceMatchAssessment:
    """Scored explanation for one Michelin row and one Maps candidate."""

    strength: MatchStrength
    name_match: bool
    location_overlap_tokens: tuple[str, ...]
    cuisine_overlap_tokens: tuple[str, ...]
    postal_code_overlap_tokens: tuple[str, ...]
    street_overlap_tokens: tuple[str, ...]
    city_in_candidate_address: bool
    coordinate_like_candidate_name: bool
    address_like_candidate_name: bool
    house_number_conflict: bool
    located_in_match: bool
    informative_category: bool
    food_service_category: bool
    name_score: float = 0.0
    address_score: float = 0.0
    match_score: float = 0.0
    hard_veto: bool = False
    veto_reasons: tuple[str, ...] = ()
    name_strategy: str = ""


def _normalize_text(value: str) -> str:
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


def _normalize_name_variants(value: str) -> str:
    normalized = _normalize_text(value).translate(_CJK_NAME_VARIANT_TRANSLATION)
    for source, replacement in _CJK_NAME_PHRASE_REPLACEMENTS:
        normalized = normalized.replace(source, replacement)
    return normalized


def _tokenize(value: str) -> set[str]:
    normalized_value = _normalize_text(value)
    return {token for token in _WORD_OR_NUMBER_PATTERN.findall(normalized_value) if token}


def _normalize_category_text(value: str) -> str:
    normalized = _normalize_text(value)
    if normalized in _NON_CATEGORY_PLACEHOLDERS:
        return ""
    return normalized


def _is_food_service_category(value: str) -> bool:
    normalized = _normalize_category_text(value)
    if not normalized:
        return False
    return any(keyword in normalized for keyword in _FOOD_SERVICE_CATEGORY_KEYWORDS)


def _extract_postal_code_tokens(value: str) -> set[str]:
    normalized = _normalize_text(value)
    matches = _POSTAL_CODE_PATTERN.findall(normalized)
    tokens: set[str] = set()
    for match in matches:
        digits_only = "".join(character for character in match if character.isdigit())
        if len(digits_only) == 7:
            tokens.add(digits_only)
    return tokens


def is_coordinate_like_place_name(value: str) -> bool:
    """Return True when the candidate name is a coordinate label instead of a place name."""

    normalized = value.strip()
    if not normalized:
        return False
    return bool(
        _COORDINATE_DMS_PATTERN.match(normalized)
        or _COORDINATE_DECIMAL_PATTERN.match(normalized)
    )


def _is_address_like_place_name(value: str) -> bool:
    return bool(_ADDRESS_LIKE_PLACE_NAME_PATTERN.search(value.strip()))


def _significant_location_tokens(tokens: set[str]) -> set[str]:
    significant: set[str] = set()
    for token in tokens:
        if token in _GENERIC_LOCATION_TOKENS:
            continue
        if len(token) <= 1:
            continue
        significant.add(token)
    return significant


def _extract_row_name_aliases(row: dict[str, Any]) -> tuple[str, ...]:
    raw_aliases = row.get("Aliases", ())
    if isinstance(raw_aliases, str):
        alias_candidates = (raw_aliases,)
    elif isinstance(raw_aliases, (list, tuple, set)):
        alias_candidates = tuple(str(alias) for alias in raw_aliases)
    else:
        return ()

    aliases: list[str] = []
    seen: set[str] = set()
    for alias in alias_candidates:
        normalized_alias = _normalize_text(alias)
        if not normalized_alias or normalized_alias in seen:
            continue
        aliases.append(normalized_alias)
        seen.add(normalized_alias)
    return tuple(aliases)


def _has_confident_name_match(row_name: str, candidate_name: str) -> bool:
    if row_name == candidate_name:
        return True
    row_name_tokens = _tokenize(row_name)
    candidate_name_tokens = _tokenize(candidate_name)
    if len(row_name_tokens) >= 2 and row_name_tokens.issubset(candidate_name_tokens):
        return True
    if len(candidate_name_tokens) >= 2 and candidate_name_tokens.issubset(row_name_tokens):
        return True
    # Single-token name: accept Latin tokens only in short candidate names
    # (e.g. "Lin" -> "Lin Restaurant") to avoid generic-word over-matches.
    # Exact single-CJK-token names are common in Taiwan restaurant data and
    # remain precise when the token is space-separated in the Maps title.
    if (
        len(row_name_tokens) == 1
        and row_name_tokens.issubset(candidate_name_tokens)
    ):
        if _contains_cjk_characters(next(iter(row_name_tokens))):
            return True
        candidate_meaningful_tokens = candidate_name_tokens - _LATIN_NAME_DESCRIPTOR_TOKENS
        if len(candidate_meaningful_tokens) <= 2:
            return True
    if _is_confident_cjk_substring_match(row_name, candidate_name):
        return True
    if _is_confident_compact_cjk_substring_match(row_name, candidate_name):
        return True
    if _is_confident_compact_mixed_substring_match(row_name, candidate_name):
        return True
    if _has_confident_cjk_bigram_overlap(row_name, candidate_name):
        return True
    if _has_branch_stripped_cjk_name_match(row_name, candidate_name):
        return True
    if _has_branch_stripped_latin_name_match(row_name, candidate_name):
        return True
    if _is_confident_latin_substring_match(row_name, candidate_name):
        return True
    return False


def _contains_cjk_characters(value: str) -> bool:
    return bool(_CJK_CHARACTER_PATTERN.search(value))


def _strip_parenthetical_segments(value: str) -> str:
    return _PARENTHETICAL_SEGMENT_PATTERN.sub(" ", value).strip()


def _primary_cjk_name_tokens(value: str) -> tuple[str, ...]:
    stripped_value = _strip_parenthetical_segments(value)
    return tuple(
        token
        for token in _CJK_SEQUENCE_PATTERN.findall(stripped_value)
        if len(token) >= 3 and token not in _GENERIC_CJK_NAME_TOKENS
    )


def _has_branch_stripped_cjk_name_match(row_name: str, candidate_name: str) -> bool:
    row_tokens = _primary_cjk_name_tokens(row_name)
    if not row_tokens:
        return False
    candidate_text = _normalize_text(candidate_name)
    candidate_tokens = _primary_cjk_name_tokens(candidate_name)
    for row_token in row_tokens:
        if row_token in candidate_tokens or row_token in candidate_text:
            return True
    return False


def _branch_stripped_latin_name_tokens(value: str) -> set[str]:
    stripped_value = _strip_parenthetical_segments(value)
    return set(_ASCII_WORD_OR_NUMBER_PATTERN.findall(_normalize_text(stripped_value)))


def _has_branch_stripped_latin_name_match(row_name: str, candidate_name: str) -> bool:
    row_tokens = _branch_stripped_latin_name_tokens(row_name)
    candidate_tokens = _branch_stripped_latin_name_tokens(candidate_name)
    meaningful_row_tokens = {
        token
        for token in row_tokens
        if token.isdigit() or len(token) >= _LATIN_SUBSTRING_MIN_LENGTH
    }
    meaningful_candidate_tokens = {
        token
        for token in candidate_tokens
        if token not in _LATIN_NAME_DESCRIPTOR_TOKENS
    }
    has_alpha_anchor = any(
        token.isalpha() and len(token) >= _LATIN_SUBSTRING_MIN_LENGTH
        for token in meaningful_row_tokens
    )
    if len(meaningful_row_tokens) < 2 or not has_alpha_anchor:
        return False
    return meaningful_row_tokens.issubset(meaningful_candidate_tokens)


def _is_confident_cjk_substring_match(row_name: str, candidate_name: str) -> bool:
    if not (_contains_cjk_characters(row_name) and _contains_cjk_characters(candidate_name)):
        return False

    shorter_name, longer_name = sorted((row_name, candidate_name), key=len)
    if len(shorter_name) < 2:
        return False
    return shorter_name in longer_name


def _compact_cjk_name(value: str) -> str:
    normalized_value = _PARENTHETICAL_BRACKET_PATTERN.sub(" ", _normalize_name_variants(value))
    return "".join(_CJK_SEQUENCE_PATTERN.findall(normalized_value))


def _compact_mixed_name(value: str) -> str:
    normalized_value = _PARENTHETICAL_BRACKET_PATTERN.sub(" ", _normalize_name_variants(value))
    return "".join(_NAME_COMPACT_TOKEN_PATTERN.findall(normalized_value))


def _is_confident_compact_cjk_substring_match(row_name: str, candidate_name: str) -> bool:
    row_compact = _compact_cjk_name(row_name)
    candidate_compact = _compact_cjk_name(candidate_name)
    if not row_compact or not candidate_compact:
        return False

    shorter_name, longer_name = sorted((row_compact, candidate_compact), key=len)
    if len(shorter_name) < 4:
        return False
    return shorter_name in longer_name


def _is_confident_compact_mixed_substring_match(row_name: str, candidate_name: str) -> bool:
    if not (_contains_cjk_characters(row_name) and _contains_cjk_characters(candidate_name)):
        return False
    row_compact = _compact_mixed_name(row_name)
    candidate_compact = _compact_mixed_name(candidate_name)
    if not row_compact or not candidate_compact:
        return False

    shorter_name, longer_name = sorted((row_compact, candidate_compact), key=len)
    if len(shorter_name) < 4:
        return False
    return shorter_name in longer_name


def _cjk_bigrams(value: str) -> set[str]:
    compact_name = _compact_cjk_name(value)
    if len(compact_name) < 4:
        return set()
    return {
        compact_name[index : index + 2]
        for index in range(0, len(compact_name) - 1)
    }


def _has_confident_cjk_bigram_overlap(row_name: str, candidate_name: str) -> bool:
    row_bigrams = _cjk_bigrams(row_name)
    candidate_bigrams = _cjk_bigrams(candidate_name)
    if not row_bigrams or not candidate_bigrams:
        return False

    overlap_count = len(row_bigrams.intersection(candidate_bigrams))
    shorter_bigram_count = min(len(row_bigrams), len(candidate_bigrams))
    return overlap_count >= 2 and overlap_count / shorter_bigram_count >= 0.7


_LATIN_SUBSTRING_MIN_LENGTH = 4


def _is_confident_latin_substring_match(row_name: str, candidate_name: str) -> bool:
    """Non-CJK substring match for concatenated place names.

    Handles cases like "Zaap" → "Zaaptaipei" where the candidate name
    is the row name concatenated with a city or descriptor (no spaces).
    Only matches when the shorter name is a *proper* substring within
    a single token of the longer name – not when it appears as a
    separate space-delimited token (which the token-based checks
    already handle).  This prevents false positives like
    "Mitsui" → "Mitsui Garden Hotel" where "Mitsui" is an exact token.
    """
    shorter_name, longer_name = sorted(
        (_normalize_text(row_name), _normalize_text(candidate_name)), key=len
    )
    if len(shorter_name) < _LATIN_SUBSTRING_MIN_LENGTH:
        return False
    longer_tokens = _WORD_OR_NUMBER_PATTERN.findall(longer_name)
    return any(shorter_name in token and shorter_name != token for token in longer_tokens)


def _has_location_anchor(tokens: set[str]) -> bool:
    has_numeric_anchor = any(token.isdigit() and len(token) >= 2 for token in tokens)
    has_named_location_anchor = any(not token.isdigit() for token in tokens)
    return has_numeric_anchor and has_named_location_anchor


def _has_house_number_anchor(
    *,
    row_address: str,
    candidate_address: str,
    location_overlap_tokens: set[str],
) -> bool:
    row_text = _normalize_text(row_address)
    candidate_text = _normalize_text(candidate_address)
    numeric_overlap_tokens = [token for token in location_overlap_tokens if token.isdigit()]
    if not numeric_overlap_tokens:
        return False

    for token in numeric_overlap_tokens:
        if len(token) >= 2:
            return True

        row_has_house_number = (
            f"{token}號" in row_text
            or re.search(rf"\bno\.?\s*{re.escape(token)}\b", row_text) is not None
        )
        candidate_has_house_number = (
            f"{token}號" in candidate_text
            or re.search(rf"\bno\.?\s*{re.escape(token)}\b", candidate_text) is not None
        )
        if row_has_house_number and candidate_has_house_number:
            return True
    return False


def _has_house_number_conflict(row_address: str, candidate_address: str) -> bool:
    return _strategy_has_house_number_conflict(row_address, candidate_address)


def assess_place_match(
    row: dict[str, Any],
    candidate: PlaceCandidate,
    *,
    name_evaluator: Callable[[NameInputs], NameEvidence] = evaluate_name_evidence,
) -> PlaceMatchAssessment:
    """Assess candidate confidence and return signals for debugging."""

    row_name = _normalize_text(str(row.get("Name", "")))
    row_city = _normalize_text(str(row.get("City", "")))
    row_address = _normalize_text(str(row.get("Address", "")))
    row_cuisine = _normalize_text(str(row.get("Cuisine", "")))
    row_name_local = _normalize_text(str(row.get("NameLocal", "")))
    row_aliases = _extract_row_name_aliases(row)

    candidate_name = _normalize_text(candidate.name)
    candidate_subtitle = _normalize_text(candidate.subtitle)
    candidate_address = _normalize_text(candidate.address)
    candidate_category = _normalize_category_text(candidate.category)
    candidate_located_in = _normalize_text(candidate.located_in)
    informative_category = bool(candidate_category)
    food_service_category = _is_food_service_category(candidate_category)
    coordinate_like_candidate_name = is_coordinate_like_place_name(candidate.name)
    address_like_candidate_name = _is_address_like_place_name(candidate.name)

    if not row_name or not candidate_name:
        return PlaceMatchAssessment(
            strength="weak",
            name_match=False,
            location_overlap_tokens=(),
            cuisine_overlap_tokens=(),
            postal_code_overlap_tokens=(),
            street_overlap_tokens=(),
            city_in_candidate_address=False,
            coordinate_like_candidate_name=coordinate_like_candidate_name,
            address_like_candidate_name=address_like_candidate_name,
            house_number_conflict=False,
            located_in_match=False,
            informative_category=informative_category,
            food_service_category=food_service_category,
        )

    row_names = [row_name]
    if row_name_local:
        row_names.append(row_name_local)
    row_names.extend(alias for alias in row_aliases if alias not in row_names)

    candidate_names = [candidate_name]
    if candidate_subtitle:
        candidate_names.append(candidate_subtitle)
    name_evidence = name_evaluator(
        build_name_inputs(row_names=row_names, candidate_names=candidate_names)
    )
    name_match = name_evidence.matched
    located_in_match = bool(candidate_located_in) and any(
        _has_confident_name_match(row_name_candidate, candidate_located_in)
        for row_name_candidate in row_names
    )
    location_tokens = _tokenize(" ".join((row_city, row_address)))
    candidate_tokens = _tokenize(candidate_address)
    city_tokens = _tokenize(row_city)
    location_overlap_tokens_set = location_tokens.intersection(candidate_tokens)
    location_overlap_tokens = tuple(sorted(location_overlap_tokens_set))
    significant_location_overlap = _significant_location_tokens(location_overlap_tokens_set)
    street_overlap_tokens_set = location_overlap_tokens_set - city_tokens
    significant_street_overlap = _significant_location_tokens(street_overlap_tokens_set)

    cuisine_tokens = _tokenize(row_cuisine)
    category_tokens = _tokenize(candidate_category)
    cuisine_overlap_tokens_set = cuisine_tokens.intersection(category_tokens)
    cuisine_overlap_tokens = tuple(sorted(cuisine_overlap_tokens_set))

    city_in_candidate_address = bool(city_tokens.intersection(candidate_tokens))
    has_house_number_anchor = _has_house_number_anchor(
        row_address=row_address,
        candidate_address=candidate_address,
        location_overlap_tokens=location_overlap_tokens_set,
    )
    house_number_conflict = _has_house_number_conflict(
        row_address=row_address,
        candidate_address=candidate_address,
    )

    row_postal_codes = _extract_postal_code_tokens(row_address)
    candidate_postal_codes = _extract_postal_code_tokens(candidate_address)
    postal_code_overlap_tokens = tuple(sorted(row_postal_codes.intersection(candidate_postal_codes)))

    address_score = 0.0
    if city_in_candidate_address:
        address_score += 20.0
    if has_house_number_anchor:
        address_score += 40.0
    if significant_street_overlap:
        address_score += min(30.0, 8.0 * len(significant_street_overlap))
    elif significant_location_overlap:
        address_score += min(20.0, 5.0 * len(significant_location_overlap))
    if cuisine_overlap_tokens:
        address_score += 10.0
    if food_service_category:
        address_score += 5.0

    veto_reasons: list[str] = []
    if coordinate_like_candidate_name:
        veto_reasons.append("coordinate_like_candidate_name")
    if address_like_candidate_name:
        veto_reasons.append("address_like_candidate_name")
    if house_number_conflict and (name_match or address_score >= 30.0):
        veto_reasons.append("house_number_conflict")
    if located_in_match and not name_match:
        veto_reasons.append("located_in_without_title_name")
    if informative_category and not food_service_category and (not name_match or name_evidence.score < 95.0):
        veto_reasons.append("non_food_category")
    if "food_descriptor_conflict" in name_evidence.reasons:
        veto_reasons.append("food_descriptor_conflict")
    if name_match and has_cjk_proper_prefix_conflict(name_evidence.row_name, name_evidence.candidate_name):
        veto_reasons.append("cjk_proper_prefix_conflict")

    features = extract_place_match_features(row, candidate)
    decision = _get_production_matcher_strategy().decide(features)
    strength: MatchStrength = decision.strength
    name_score = max(features.name_similarity, features.alias_similarity)
    name_match = name_score >= 70.0
    address_score = features.address_similarity
    match_score = decision.score
    hard_veto = features.disqualifier_score >= PRODUCTION_DISQUALIFIER_THRESHOLD
    veto_reasons = list(features.risk_labels)

    return PlaceMatchAssessment(
        strength=strength,
        name_match=name_match,
        location_overlap_tokens=location_overlap_tokens,
        cuisine_overlap_tokens=cuisine_overlap_tokens,
        postal_code_overlap_tokens=postal_code_overlap_tokens,
        street_overlap_tokens=tuple(sorted(street_overlap_tokens_set)),
        city_in_candidate_address=city_in_candidate_address,
        coordinate_like_candidate_name=coordinate_like_candidate_name,
        address_like_candidate_name=address_like_candidate_name,
        house_number_conflict=house_number_conflict,
        located_in_match=located_in_match,
        informative_category=informative_category,
        food_service_category=food_service_category,
        name_score=name_score,
        address_score=address_score,
        match_score=match_score,
        hard_veto=hard_veto,
        veto_reasons=tuple(veto_reasons),
        name_strategy=decision.strategy_id,
    )


def classify_place_match(
    row: dict[str, Any],
    candidate: PlaceCandidate,
) -> MatchStrength:
    """Classify candidate confidence for one Michelin row."""
    return assess_place_match(row, candidate).strength


def _get_production_matcher_strategy() -> WeightedEvidenceStrategy:
    if PRODUCTION_MATCHER_STRATEGY_ID != WeightedEvidenceStrategy.strategy_id:
        raise ValueError(f"Unknown production matcher strategy: {PRODUCTION_MATCHER_STRATEGY_ID}")
    return WeightedEvidenceStrategy(PRODUCTION_MATCHER_CONFIG)
