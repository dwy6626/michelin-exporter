"""Heuristics for matching Google Maps candidate places to Michelin rows."""

import re
from dataclasses import dataclass
from typing import Any, Literal

MatchStrength = Literal["strong", "medium", "weak"]
_POSTAL_CODE_PATTERN = re.compile(r"\b\d{3}-\d{4}\b|\b\d{7}\b")
_WORD_OR_NUMBER_PATTERN = re.compile(r"[^\W\d_]+|\d+")
_CJK_CHARACTER_PATTERN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
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


@dataclass(frozen=True)
class PlaceCandidate:
    """Candidate place metadata extracted from Google Maps UI."""

    name: str
    address: str
    category: str
    subtitle: str = ""


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


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _tokenize(value: str) -> set[str]:
    normalized_value = _normalize_text(value)
    return {token for token in _WORD_OR_NUMBER_PATTERN.findall(normalized_value) if token}


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


def _significant_location_tokens(tokens: set[str]) -> set[str]:
    significant: set[str] = set()
    for token in tokens:
        if token in _GENERIC_LOCATION_TOKENS:
            continue
        if len(token) <= 1:
            continue
        significant.add(token)
    return significant


def _has_confident_name_match(row_name: str, candidate_name: str) -> bool:
    if row_name == candidate_name:
        return True
    row_name_tokens = _tokenize(row_name)
    candidate_name_tokens = _tokenize(candidate_name)
    if len(row_name_tokens) >= 2 and row_name_tokens.issubset(candidate_name_tokens):
        return True
    if len(candidate_name_tokens) >= 2 and candidate_name_tokens.issubset(row_name_tokens):
        return True
    # Single-token name: accept when the token appears in a short candidate name
    # (e.g. "Lin" → "Lin Restaurant") to avoid over-matching generic words.
    # Limit to 2 candidate tokens to prevent false positives like
    # "Lin" → "Dr. Lin Dermatology".
    if (
        len(row_name_tokens) == 1
        and row_name_tokens.issubset(candidate_name_tokens)
        and len(candidate_name_tokens) <= 2
    ):
        return True
    if _is_confident_cjk_substring_match(row_name, candidate_name):
        return True
    if _is_confident_latin_substring_match(row_name, candidate_name):
        return True
    return False


def _contains_cjk_characters(value: str) -> bool:
    return bool(_CJK_CHARACTER_PATTERN.search(value))


def _is_confident_cjk_substring_match(row_name: str, candidate_name: str) -> bool:
    if not (_contains_cjk_characters(row_name) and _contains_cjk_characters(candidate_name)):
        return False

    shorter_name, longer_name = sorted((row_name, candidate_name), key=len)
    if len(shorter_name) < 2:
        return False
    return shorter_name in longer_name


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


def assess_place_match(
    row: dict[str, Any],
    candidate: PlaceCandidate,
) -> PlaceMatchAssessment:
    """Assess candidate confidence and return signals for debugging."""

    row_name = _normalize_text(str(row.get("Name", "")))
    row_city = _normalize_text(str(row.get("City", "")))
    row_address = _normalize_text(str(row.get("Address", "")))
    row_cuisine = _normalize_text(str(row.get("Cuisine", "")))
    row_name_local = _normalize_text(str(row.get("NameLocal", "")))

    candidate_name = _normalize_text(candidate.name)
    candidate_subtitle = _normalize_text(candidate.subtitle)
    candidate_address = _normalize_text(candidate.address)
    candidate_category = _normalize_text(candidate.category)
    coordinate_like_candidate_name = is_coordinate_like_place_name(candidate.name)

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
        )

    candidate_names = [candidate_name]
    if candidate_subtitle:
        candidate_names.append(candidate_subtitle)
    name_match = any(
        _has_confident_name_match(row_name, cn) or (
            bool(row_name_local) and _has_confident_name_match(row_name_local, cn)
        )
        for cn in candidate_names
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

    row_postal_codes = _extract_postal_code_tokens(row_address)
    candidate_postal_codes = _extract_postal_code_tokens(candidate_address)
    postal_code_overlap_tokens = tuple(sorted(row_postal_codes.intersection(candidate_postal_codes)))

    if coordinate_like_candidate_name:
        strength: MatchStrength = "weak"
    elif name_match:
        has_street_signal = bool(significant_street_overlap) or has_house_number_anchor
        if city_in_candidate_address and (has_street_signal or postal_code_overlap_tokens):
            strength = "strong"
        elif city_in_candidate_address or postal_code_overlap_tokens or has_street_signal:
            strength = "medium"
        elif cuisine_overlap_tokens:
            strength = "medium"
        else:
            strength = "weak"
    else:
        if postal_code_overlap_tokens and (
            cuisine_overlap_tokens or city_in_candidate_address or significant_location_overlap
        ):
            strength = "medium"
        elif city_in_candidate_address and has_house_number_anchor:
            # Accept translated/localized names when city + house-number anchors match.
            strength = "medium"
        elif cuisine_overlap_tokens and (
            len(significant_location_overlap) >= 2
            or (significant_location_overlap and city_in_candidate_address)
        ):
            strength = "medium"
        elif _has_location_anchor(significant_location_overlap):
            strength = "medium"
        else:
            strength = "weak"

    return PlaceMatchAssessment(
        strength=strength,
        name_match=name_match,
        location_overlap_tokens=location_overlap_tokens,
        cuisine_overlap_tokens=cuisine_overlap_tokens,
        postal_code_overlap_tokens=postal_code_overlap_tokens,
        street_overlap_tokens=tuple(sorted(street_overlap_tokens_set)),
        city_in_candidate_address=city_in_candidate_address,
        coordinate_like_candidate_name=coordinate_like_candidate_name,
    )


def classify_place_match(
    row: dict[str, Any],
    candidate: PlaceCandidate,
) -> MatchStrength:
    """Classify candidate confidence for one Michelin row."""
    return assess_place_match(row, candidate).strength
