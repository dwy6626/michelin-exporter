"""HTML snippet parsing helpers for Michelin fields."""

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..catalog.levels import LEVEL_LABELS

_AWARD_ICON_CLASS = "michelin-award"
_STAR_ICON_TOKENS = ("1star", "michelin-star")
_BIB_GOURMAND_ICON_TOKENS = ("bib-gourmand", "bib_gourmand", "bibendum")
_BIB_GOURMAND_ATTRIBUTE_TOKENS = ("bib", "bib_gourmand", "bib-gourmand")
_ONE_STAR_ATTRIBUTE_TOKENS = ("one_star", "one-star", "1 star")
_TWO_STAR_ATTRIBUTE_TOKENS = ("two_stars", "two-stars", "2 star")
_THREE_STAR_ATTRIBUTE_TOKENS = ("three_stars", "three-stars", "3 star")
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

    rating_from_attributes = _parse_rating_from_card_attributes(distinction_icon_container)
    if rating_from_attributes:
        return rating_from_attributes

    award_icon_sources = _extract_award_icon_sources(distinction_icon_container)
    if any(
        any(token in icon_source.casefold() for token in _BIB_GOURMAND_ICON_TOKENS)
        for icon_source in award_icon_sources
    ):
        return _BIB_GOURMAND_RATING_LABEL

    star_icon_count = sum(
        any(star_icon_token in icon_source for star_icon_token in _STAR_ICON_TOKENS)
        for icon_source in award_icon_sources
    )
    return _RATING_LABEL_BY_STAR_ICON_COUNT.get(star_icon_count, _DEFAULT_RATING_LABEL)


def _parse_rating_from_card_attributes(distinction_icon_container: Any) -> str:
    """Parse rating from Michelin card metadata when available."""

    attribute_values = _extract_distinction_attribute_values(distinction_icon_container)
    if not attribute_values:
        return ""

    if _attribute_values_contain_any(attribute_values, _BIB_GOURMAND_ATTRIBUTE_TOKENS):
        return _BIB_GOURMAND_RATING_LABEL
    if _attribute_values_contain_any(attribute_values, _THREE_STAR_ATTRIBUTE_TOKENS):
        return _RATING_LABEL_BY_STAR_ICON_COUNT[3]
    if _attribute_values_contain_any(attribute_values, _TWO_STAR_ATTRIBUTE_TOKENS):
        return _RATING_LABEL_BY_STAR_ICON_COUNT[2]
    if _attribute_values_contain_any(attribute_values, _ONE_STAR_ATTRIBUTE_TOKENS):
        return _RATING_LABEL_BY_STAR_ICON_COUNT[1]
    return ""


def _extract_distinction_attribute_values(distinction_icon_container: Any) -> tuple[str, ...]:
    """Return distinction-like attributes from a card and its action controls."""

    values: list[str] = []
    for tag in _iter_distinction_context_tags(distinction_icon_container):
        attrs = getattr(tag, "attrs", {})
        if not isinstance(attrs, dict):
            continue
        for attribute_name in (
            "data-map-pin-name",
            "data-distinction",
            "data-dtm-distinction",
        ):
            attribute_value = attrs.get(attribute_name, "")
            if isinstance(attribute_value, str) and attribute_value.strip():
                values.append(attribute_value)
    return tuple(values)


def _iter_distinction_context_tags(distinction_icon_container: Any) -> tuple[Any, ...]:
    """Return the badge tag and nearest listing card descendants."""

    tags: list[Any] = [distinction_icon_container]
    card = distinction_icon_container.find_parent(class_="card__menu")
    if card is not None:
        tags.append(card)
        tags.extend(
            tag
            for tag in card.find_all(attrs={"data-dtm-distinction": True})
            if tag is not None
        )
        tags.extend(
            tag
            for tag in card.find_all(attrs={"data-distinction": True})
            if tag is not None
        )
    return tuple(tags)


def _attribute_values_contain_any(values: tuple[str, ...], tokens: tuple[str, ...]) -> bool:
    return any(token in value.casefold() for value in values for token in tokens)


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
