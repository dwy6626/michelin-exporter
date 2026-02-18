"""Helpers for resolving language-specific scope names from Michelin pages."""

import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup, Tag

from .fetcher import fetch_page_soup
from .models import NullProgressReporter

_BREADCRUMB_SCOPE_SELECTORS = (
    "nav[aria-label='Breadcrumb'] li:last-child",
    "nav[aria-label='breadcrumb'] li:last-child",
    "nav ol li:last-child",
    "ol.breadcrumb li:last-child",
    ".breadcrumb li:last-child",
)
_HEADING_SCOPE_SELECTORS = (
    "main h1",
    "h1",
    "header h1",
)
_META_SCOPE_SELECTORS = (
    "meta[property='og:title']",
    "meta[name='twitter:title']",
    "title",
)
_SCOPE_NAME_SPLITTERS = (
    " | ",
    " - ",
    " : ",
    " – ",
    " — ",
)
_GENERIC_SCOPE_NAMES = frozenset(
    {
        "restaurants",
        "restaurant",
        "michelin guide",
        "the michelin guide",
    }
)
_LISTING_SCOPE_PAGE_TYPE = "listing-scope"
_MAX_SCOPE_NAME_LENGTH = 30


@dataclass(frozen=True)
class _ScopeNameCandidate:
    text: str
    priority: int


def resolve_listing_scope_name(
    *,
    url: str,
    headers: dict[str, str],
    tls_verify: bool | str,
) -> str:
    """Fetch listing page HTML and resolve a human-readable scope name."""

    with requests.Session() as session:
        listing_page_soup = fetch_page_soup(
            session=session,
            url=url,
            headers=headers,
            tls_verify=tls_verify,
            progress_reporter=NullProgressReporter(),
            page_type=_LISTING_SCOPE_PAGE_TYPE,
        )
    if listing_page_soup.soup is None:
        return ""
    return extract_scope_name_from_listing_soup(listing_page_soup.soup)


def extract_scope_name_from_listing_soup(listing_page_soup: BeautifulSoup) -> str:
    """Extract the best scope candidate from listing-page soup."""

    candidates: list[_ScopeNameCandidate] = []

    for selector in _BREADCRUMB_SCOPE_SELECTORS:
        scope_candidate = _extract_text_candidate(listing_page_soup.select_one(selector))
        if scope_candidate:
            candidates.append(_ScopeNameCandidate(text=scope_candidate, priority=3))

    for selector in _HEADING_SCOPE_SELECTORS:
        scope_candidate = _extract_text_candidate(listing_page_soup.select_one(selector))
        if scope_candidate:
            candidates.append(_ScopeNameCandidate(text=scope_candidate, priority=2))

    for selector in _META_SCOPE_SELECTORS:
        selected_tag = listing_page_soup.select_one(selector)
        if selected_tag is None:
            continue
        raw_text = ""
        if selected_tag.name == "meta":
            raw_text = str(selected_tag.get("content", ""))
        else:
            raw_text = selected_tag.get_text(" ", strip=True)
        scope_candidate = _normalize_scope_candidate(raw_text)
        if scope_candidate:
            candidates.append(_ScopeNameCandidate(text=scope_candidate, priority=1))

    if not candidates:
        return ""

    deduplicated_candidates: list[_ScopeNameCandidate] = []
    seen_candidates: set[str] = set()
    for candidate in candidates:
        canonical_text = candidate.text.casefold()
        if canonical_text in seen_candidates:
            continue
        seen_candidates.add(canonical_text)
        deduplicated_candidates.append(candidate)

    ordered_candidates = sorted(
        deduplicated_candidates,
        key=lambda candidate: (-candidate.priority, len(candidate.text)),
    )
    return ordered_candidates[0].text


def _extract_text_candidate(selected_tag: Tag | None) -> str:
    if selected_tag is None:
        return ""
    return _normalize_scope_candidate(selected_tag.get_text(" ", strip=True))


_EMOJI_PATTERN = re.compile(
    "["
    "\U0001f600-\U0001f64f"  # emoticons
    "\U0001f300-\U0001f5ff"  # symbols & pictographs
    "\U0001f680-\U0001f6ff"  # transport & map symbols
    "\U0001f1e0-\U0001f1ff"  # flags
    "\U00002702-\U000027b0"  # dingbats
    "\U0000fe00-\U0000fe0f"  # variation selectors
    "\U0001f900-\U0001f9ff"  # supplemental symbols
    "\U0001fa00-\U0001fa6f"  # chess symbols / extended-A
    "\U0001fa70-\U0001faff"  # symbols extended-A
    "\U00002600-\U000026ff"  # misc symbols
    "\U0000200d"             # zero-width joiner
    "]+",
)


def _normalize_scope_candidate(value: str) -> str:
    compact_value = " ".join(value.split()).strip()
    if not compact_value:
        return ""

    # Strip emoji characters before further processing.
    compact_value = _EMOJI_PATTERN.sub("", compact_value).strip()
    if not compact_value:
        return ""

    normalized_value = compact_value
    for splitter in _SCOPE_NAME_SPLITTERS:
        if splitter not in normalized_value:
            continue
        left_segment = normalized_value.split(splitter, maxsplit=1)[0].strip()
        if left_segment:
            normalized_value = left_segment
            break

    if normalized_value.casefold() in _GENERIC_SCOPE_NAMES:
        return ""
    if len(normalized_value) <= _MAX_SCOPE_NAME_LENGTH:
        return normalized_value
    truncated_value = normalized_value[:_MAX_SCOPE_NAME_LENGTH].rstrip(" -|:,")
    last_space_index = truncated_value.rfind(" ")
    if last_space_index > 0:
        return truncated_value[:last_space_index].rstrip(" -|:,")
    return truncated_value
