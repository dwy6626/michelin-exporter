"""Search-query builder for Google Maps place lookups."""

import re
from typing import Any

_TAIWAN_LOCAL_AREA_PATTERNS = (
    re.compile(r"(?:\d{3,5})?((?:臺|台)?[\u4e00-\u9fff]{1,4}(?:縣|市)[\u4e00-\u9fff]{1,4}(?:區|鎮|鄉|市))"),
    re.compile(r"([\u4e00-\u9fff]{1,4}(?:區|鎮|鄉))"),
    re.compile(r"^([\u4e00-\u9fff]{1,4}市)"),
)
_TAIWAN_STREET_HOUSE_PATTERN = re.compile(
    r"([\u4e00-\u9fff\d]{1,12}(?:大道|路|街)"
    r"(?:[一二三四五六七八九十\d]+段)?"
    r"\d+(?:之\d+)?號(?:\d+樓)?)"
)


def _build_text(*parts: str) -> str:
    normalized_parts = [part.strip() for part in parts if part and part.strip()]
    return " ".join(normalized_parts)


def _format_coordinate(value: float) -> str:
    return f"{value:.7f}".rstrip("0").rstrip(".")


def _extract_coordinate_query(row: dict[str, Any]) -> str:
    raw_latitude = str(row.get("Latitude", "")).strip()
    raw_longitude = str(row.get("Longitude", "")).strip()
    if not raw_latitude or not raw_longitude:
        return ""
    try:
        latitude = float(raw_latitude)
        longitude = float(raw_longitude)
    except ValueError:
        return ""
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return ""
    return f"{_format_coordinate(latitude)},{_format_coordinate(longitude)}"


def _extract_local_area_hint(address: str) -> str:
    """Extract a district-level Taiwan address hint for narrower Maps searches."""

    for pattern in _TAIWAN_LOCAL_AREA_PATTERNS:
        match = pattern.search(address)
        if match is not None:
            return match.group(1)
    return ""


def _extract_street_house_hint(address: str) -> str:
    """Extract a street + house-number hint for branch-sensitive Maps searches."""

    search_text = address
    local_area_hint = _extract_local_area_hint(address)
    if local_area_hint and local_area_hint in search_text:
        search_text = search_text.split(local_area_hint, 1)[1]

    match = _TAIWAN_STREET_HOUSE_PATTERN.search(search_text)
    if match is None:
        return ""
    return match.group(1)


def _extract_aliases(row: dict[str, Any], *, excluded_names: tuple[str, ...]) -> tuple[str, ...]:
    raw_aliases = row.get("Aliases", ())
    if isinstance(raw_aliases, str):
        alias_candidates = (raw_aliases,)
    elif isinstance(raw_aliases, (list, tuple, set)):
        alias_candidates = tuple(str(alias) for alias in raw_aliases)
    else:
        return ()

    excluded = {name.strip().casefold() for name in excluded_names if name.strip()}
    aliases: list[str] = []
    seen: set[str] = set(excluded)
    for alias in alias_candidates:
        normalized_alias = " ".join(alias.split()).strip()
        if not normalized_alias:
            continue
        alias_key = normalized_alias.casefold()
        if alias_key in seen:
            continue
        aliases.append(normalized_alias)
        seen.add(alias_key)
    return tuple(aliases)


def build_place_query_attempts(row: dict[str, Any]) -> tuple[str, ...]:
    """Build ordered search query attempts for one Michelin row."""

    name = str(row.get("Name", "")).strip()
    city = str(row.get("City", "")).strip()
    address = str(row.get("Address", "")).strip()
    cuisine = str(row.get("Cuisine", "")).strip()
    name_local = str(row.get("NameLocal", "")).strip()
    local_area_hint = _extract_local_area_hint(address)
    street_house_hint = _extract_street_house_hint(address)
    aliases = _extract_aliases(row, excluded_names=(name, name_local))
    coordinate_query = _extract_coordinate_query(row)

    # Build attempts with priority: local name variants, then fallback to English name
    attempts = []

    # Only add local name combinations if local name exists and differs from primary name
    if name_local and name_local != name:
        if local_area_hint:
            attempts.append(_build_text(name_local, local_area_hint))
        if street_house_hint:
            attempts.append(_build_text(name_local, street_house_hint))
        attempts.append(_build_text(name_local, city))
        attempts.append(_build_text(name_local))
        if cuisine:
            if local_area_hint:
                attempts.append(_build_text(name_local, cuisine, local_area_hint))
            attempts.append(_build_text(name_local, cuisine, city))

    # Primary name combinations
    if name:
        if local_area_hint:
            attempts.append(_build_text(name, local_area_hint))
        if street_house_hint:
            attempts.append(_build_text(name, street_house_hint))
        attempts.append(_build_text(name, city))
        attempts.append(_build_text(name))

    for alias in aliases:
        if local_area_hint:
            attempts.append(_build_text(alias, local_area_hint))
        if street_house_hint:
            attempts.append(_build_text(alias, street_house_hint))
        attempts.append(_build_text(alias, city))
        attempts.append(_build_text(alias))

    has_identity_anchor = bool(name or name_local or aliases)

    attempts.append(coordinate_query)
    if not has_identity_anchor:
        attempts.append(_build_text(address))
    if cuisine:
        if local_area_hint:
            attempts.append(_build_text(name, cuisine, local_area_hint))
        attempts.append(_build_text(name, cuisine, city))
    deduplicated: list[str] = []
    seen: set[str] = set()
    for attempt in attempts:
        if not attempt:
            continue
        normalized_attempt = attempt.lower()
        if normalized_attempt in seen:
            continue
        deduplicated.append(attempt)
        seen.add(normalized_attempt)
    return tuple(deduplicated)
