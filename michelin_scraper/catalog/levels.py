"""Michelin levels and helpers."""

COMBINED_STAR_LEVEL_SLUG = "stars"
STAR_LEVEL_SLUGS = (
    "one-star",
    "two-star",
    "three-star",
)
NON_STAR_LEVEL_SLUGS = (
    "selected",
    "bib-gourmand",
)
SOURCE_LEVEL_SLUGS = STAR_LEVEL_SLUGS + NON_STAR_LEVEL_SLUGS
LEVEL_SLUGS = (
    COMBINED_STAR_LEVEL_SLUG,
    "selected",
    "bib-gourmand",
)
SPLIT_LEVEL_SLUGS = STAR_LEVEL_SLUGS + NON_STAR_LEVEL_SLUGS
SUPPORTED_LEVEL_SLUGS = LEVEL_SLUGS + STAR_LEVEL_SLUGS

SOURCE_LEVEL_LABELS = {
    "one-star": "1 Star",
    "two-star": "2 Stars",
    "three-star": "3 Stars",
    "bib-gourmand": "Bib Gourmand",
    "selected": "Selected",
}

SOURCE_LEVEL_BADGES = {
    "one-star": "⭐",
    "two-star": "⭐⭐",
    "three-star": "⭐⭐⭐",
    "bib-gourmand": "😋",
    "selected": "Selected",
}

LEVEL_LABELS = {
    COMBINED_STAR_LEVEL_SLUG: "Stars",
    **SOURCE_LEVEL_LABELS,
}

LEVEL_BADGES = {
    COMBINED_STAR_LEVEL_SLUG: "Stars",
    "one-star": "⭐",
    "two-star": "⭐⭐",
    "three-star": "⭐⭐⭐",
    "bib-gourmand": "Bib Gourmand",
    "selected": "Selected",
}

LEVEL_CHOICES_HELP = ", ".join(SUPPORTED_LEVEL_SLUGS)


def normalize_level_slug(value: str) -> str:
    """Normalize one level slug candidate."""

    return value.strip().lower()


def resolve_output_level_slug(
    source_level_slug: str,
    selected_level_slugs: tuple[str, ...] | list[str],
) -> str | None:
    """Resolve one Michelin source level slug into the selected output bucket."""

    normalized_source_slug = normalize_level_slug(source_level_slug)
    selected_set = frozenset(selected_level_slugs)
    if normalized_source_slug in STAR_LEVEL_SLUGS:
        if COMBINED_STAR_LEVEL_SLUG in selected_set:
            return COMBINED_STAR_LEVEL_SLUG
        if normalized_source_slug in selected_set:
            return normalized_source_slug
        return None
    if normalized_source_slug in selected_set:
        return normalized_source_slug
    return None


def build_rating_to_output_level_slug_map(
    selected_level_slugs: tuple[str, ...] | list[str],
) -> dict[str, str]:
    """Build source-rating aliases mapped to the selected output buckets."""

    rating_to_level_slug: dict[str, str] = {}
    for source_level_slug in SOURCE_LEVEL_SLUGS:
        output_level_slug = resolve_output_level_slug(
            source_level_slug,
            selected_level_slugs,
        )
        if output_level_slug is None:
            output_level_slug = source_level_slug
        rating_to_level_slug[SOURCE_LEVEL_LABELS[source_level_slug]] = output_level_slug
        rating_to_level_slug[source_level_slug] = output_level_slug
        badge = SOURCE_LEVEL_BADGES.get(source_level_slug, "")
        if badge:
            rating_to_level_slug[badge] = output_level_slug
    return rating_to_level_slug


def parse_level_selection(levels_text: str) -> tuple[str, ...]:
    """Parse comma-separated level slugs into canonical level order."""

    requested_values = [normalize_level_slug(part) for part in levels_text.split(",")]
    filtered_values = [value for value in requested_values if value]
    if not filtered_values:
        return LEVEL_SLUGS

    unsupported = [value for value in filtered_values if value not in SUPPORTED_LEVEL_SLUGS]
    if unsupported:
        unsupported_values = ", ".join(sorted(set(unsupported)))
        raise ValueError(
            f"Unsupported levels: {unsupported_values}. Supported levels: {LEVEL_CHOICES_HELP}."
        )

    requested_set = set(filtered_values)
    if COMBINED_STAR_LEVEL_SLUG in requested_set and requested_set.intersection(STAR_LEVEL_SLUGS):
        split_values = ", ".join(STAR_LEVEL_SLUGS)
        raise ValueError(
            f"Unsupported levels: cannot combine '{COMBINED_STAR_LEVEL_SLUG}' with split star values "
            f"({split_values}). Supported levels: {LEVEL_CHOICES_HELP}."
        )

    output_order = LEVEL_SLUGS if COMBINED_STAR_LEVEL_SLUG in requested_set else SPLIT_LEVEL_SLUGS
    return tuple(level_slug for level_slug in output_order if level_slug in requested_set)
