"""Michelin levels and helpers."""

LEVEL_LABELS = {
    "one-star": "1 Star",
    "two-star": "2 Stars",
    "three-star": "3 Stars",
    "bib-gourmand": "Bib Gourmand",
    "selected": "Selected",
}

LEVEL_BADGES = {
    "one-star": "⭐",
    "two-star": "⭐⭐",
    "three-star": "⭐⭐⭐",
    "bib-gourmand": "😋",
    "selected": "Selected",
}

LEVEL_SLUGS = tuple(LEVEL_LABELS.keys())
LEVEL_CHOICES_HELP = ", ".join(LEVEL_SLUGS)


def normalize_level_slug(value: str) -> str:
    """Normalize one level slug candidate."""

    return value.strip().lower()


def parse_level_selection(levels_text: str) -> tuple[str, ...]:
    """Parse comma-separated level slugs into canonical level order."""

    requested_values = [normalize_level_slug(part) for part in levels_text.split(",")]
    filtered_values = [value for value in requested_values if value]
    if not filtered_values:
        return LEVEL_SLUGS

    unsupported = [value for value in filtered_values if value not in LEVEL_LABELS]
    if unsupported:
        unsupported_values = ", ".join(sorted(set(unsupported)))
        raise ValueError(
            f"Unsupported levels: {unsupported_values}. Supported levels: {LEVEL_CHOICES_HELP}."
        )

    requested_set = set(filtered_values)
    return tuple(level_slug for level_slug in LEVEL_SLUGS if level_slug in requested_set)
