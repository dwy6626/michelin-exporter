"""HTML snippet parsing helpers for Michelin fields."""

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..catalog.levels import LEVEL_LABELS

_AWARD_ICON_CLASS = "michelin-award"
_ONE_STAR_ICON_TOKEN = "1star"
_BIB_GOURMAND_ICON_TOKEN = "bib-gourmand"
_MENU_SEGMENT_SEPARATOR = "·"
_COORDINATE_QUERY_PARAM = "q"
_COORDINATE_SEPARATOR = ","

_DEFAULT_RATING_LABEL = LEVEL_LABELS["selected"]
_BIB_GOURMAND_RATING_LABEL = LEVEL_LABELS["bib-gourmand"]
_RATING_LABEL_BY_STAR_ICON_COUNT = {
    1: LEVEL_LABELS["one-star"],
    2: LEVEL_LABELS["two-star"],
    3: LEVEL_LABELS["three-star"],
}


def parse_rating(distinction_icon_container: Any) -> str:
    """Parse Michelin rating from card badge icons."""

    if not distinction_icon_container:
        return _DEFAULT_RATING_LABEL

    award_icon_sources = _extract_award_icon_sources(distinction_icon_container)
    if any(_BIB_GOURMAND_ICON_TOKEN in icon_source for icon_source in award_icon_sources):
        return _BIB_GOURMAND_RATING_LABEL

    star_icon_count = sum(
        _ONE_STAR_ICON_TOKEN in icon_source for icon_source in award_icon_sources
    )
    return _RATING_LABEL_BY_STAR_ICON_COUNT.get(star_icon_count, _DEFAULT_RATING_LABEL)


def _extract_award_icon_sources(distinction_icon_container: Any) -> tuple[str, ...]:
    """Return image src values from Michelin distinction icons."""

    award_images = distinction_icon_container.find_all("img", class_=_AWARD_ICON_CLASS)
    award_icon_sources: list[str] = []
    for award_image in award_images:
        icon_source = award_image.get("src", "")
        if isinstance(icon_source, str):
            award_icon_sources.append(icon_source)
    return tuple(award_icon_sources)


def parse_price_cuisine(footer: Any) -> tuple[str, str]:
    """Parse price and cuisine from listing card footer."""

    normalized_footer_text = _normalize_footer_text(footer)
    if not normalized_footer_text:
        return "", ""

    footer_segments = normalized_footer_text.split(_MENU_SEGMENT_SEPARATOR)
    price_range = footer_segments[0] if len(footer_segments) > 0 else ""
    cuisine_label = footer_segments[1] if len(footer_segments) > 1 else ""
    return price_range, cuisine_label


def parse_gm_iframe_url(url: str) -> tuple[float | str, float | str]:
    """Parse lat/lng values from Google Maps iframe URL."""

    parsed_iframe_url = urlparse(url)
    query_parameters = parse_qs(parsed_iframe_url.query)
    coordinate_query_value = _extract_first_query_value(
        query_parameters, _COORDINATE_QUERY_PARAM
    )
    if not coordinate_query_value:
        return "", ""
    return _parse_coordinates(coordinate_query_value)


def _normalize_footer_text(footer: Any) -> str:
    """Normalize footer text to compact parser-friendly segments."""

    raw_footer_text = footer.get_text() if footer else ""
    if not isinstance(raw_footer_text, str):
        return ""
    return re.sub(r"\s+", " ", raw_footer_text).strip().replace(" ", "")


def _extract_first_query_value(
    query_parameters: dict[str, list[str]], parameter_name: str
) -> str:
    """Return the first query parameter value, or an empty string when missing."""

    parameter_values = query_parameters.get(parameter_name, [])
    if not parameter_values:
        return ""
    first_value = parameter_values[0]
    return first_value if isinstance(first_value, str) else ""


def _parse_coordinates(coordinate_text: str) -> tuple[float | str, float | str]:
    """Parse a latitude/longitude pair from comma-separated coordinate text."""

    coordinate_segments = coordinate_text.split(_COORDINATE_SEPARATOR)
    if len(coordinate_segments) < 2:
        return "", ""

    try:
        latitude = float(coordinate_segments[0])
        longitude = float(coordinate_segments[1])
    except ValueError:
        return "", ""
    return latitude, longitude
