"""Search-query builder for Google Maps place lookups."""

from typing import Any


def _build_text(*parts: str) -> str:
    normalized_parts = [part.strip() for part in parts if part and part.strip()]
    return " ".join(normalized_parts)


def build_place_query_attempts(row: dict[str, Any]) -> tuple[str, ...]:
    """Build ordered search query attempts for one Michelin row."""

    name = str(row.get("Name", "")).strip()
    city = str(row.get("City", "")).strip()
    address = str(row.get("Address", "")).strip()
    cuisine = str(row.get("Cuisine", "")).strip()
    name_local = str(row.get("NameLocal", "")).strip()

    # Build attempts with priority: local name variants, then fallback to English name
    attempts = []
    
    # Only add local name combinations if local name exists and differs from primary name
    if name_local and name_local != name:
        attempts.append(_build_text(name_local, city))
        attempts.append(_build_text(name_local))
        if cuisine:
            attempts.append(_build_text(name_local, cuisine, city))
    
    # Primary name combinations
    attempts.append(_build_text(name, city))
    attempts.append(_build_text(name))
    attempts.append(_build_text(address))
    if cuisine:
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
