"""Stable row identity helpers for idempotent sync behavior."""

import hashlib
from typing import Any


def _normalize_identity_value(value: Any) -> str:
    text = str(value or "").strip().lower()
    return " ".join(text.split())


def build_row_identity_key(level_slug: str, row: dict[str, Any]) -> str:
    """Build deterministic row key from level and location identity fields."""

    digest_input = "|".join(
        (
            _normalize_identity_value(level_slug),
            _normalize_identity_value(row.get("Name", "")),
            _normalize_identity_value(row.get("Address", "")),
            _normalize_identity_value(row.get("City", "")),
            _normalize_identity_value(row.get("Latitude", "")),
            _normalize_identity_value(row.get("Longitude", "")),
        )
    )
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
