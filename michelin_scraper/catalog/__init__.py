"""Domain catalogs used by CLI and application workflow."""

from .levels import (
    COMBINED_STAR_LEVEL_SLUG,
    LEVEL_BADGES,
    LEVEL_CHOICES_HELP,
    LEVEL_LABELS,
    LEVEL_SLUGS,
    STAR_LEVEL_SLUGS,
    build_rating_to_output_level_slug_map,
    parse_level_selection,
    resolve_output_level_slug,
)
from .targets import (
    LANGUAGE_VALUES_HELP,
    TARGET_VALUES_HELP,
    ResolvedTarget,
    normalize_language,
    normalize_target,
    resolve_language,
    resolve_target,
)

__all__ = [
    "LANGUAGE_VALUES_HELP",
    "COMBINED_STAR_LEVEL_SLUG",
    "LEVEL_BADGES",
    "LEVEL_CHOICES_HELP",
    "LEVEL_LABELS",
    "LEVEL_SLUGS",
    "ResolvedTarget",
    "STAR_LEVEL_SLUGS",
    "TARGET_VALUES_HELP",
    "build_rating_to_output_level_slug_map",
    "parse_level_selection",
    "normalize_language",
    "normalize_target",
    "resolve_output_level_slug",
    "resolve_language",
    "resolve_target",
]
