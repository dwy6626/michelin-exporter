"""Domain catalogs used by CLI and application workflow."""

from .levels import (
    LEVEL_BADGES,
    LEVEL_CHOICES_HELP,
    LEVEL_LABELS,
    LEVEL_SLUGS,
    parse_level_selection,
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
    "LEVEL_BADGES",
    "LEVEL_CHOICES_HELP",
    "LEVEL_LABELS",
    "LEVEL_SLUGS",
    "ResolvedTarget",
    "TARGET_VALUES_HELP",
    "parse_level_selection",
    "normalize_language",
    "normalize_target",
    "resolve_language",
    "resolve_target",
]
