"""Pagination helpers for Michelin listing pages."""

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

_PAGE_PATH_PATTERN = re.compile(r"(?:^|/)page/(\d+)(?:/)?$")
_ARROW_RIGHT_IMAGE_PATTERN = re.compile(r"arrow[-_]?right|right[-_]?arrow", re.IGNORECASE)


def extract_total_pages(soup: BeautifulSoup) -> int | None:
    """Extract max page number from pagination links."""

    page_numbers: list[int] = []
    for link in soup.select("ul.pagination li a"):
        text = link.get_text(strip=True)
        if text.isdigit():
            page_numbers.append(int(text))
    return max(page_numbers) if page_numbers else None


def extract_total_items(soup: BeautifulSoup) -> int | None:
    """Extract total restaurant count from the results header."""

    import re

    header = soup.select_one("h1.flex-fill")
    candidate_headers = [header] if header else []
    if not candidate_headers:
        candidate_headers = list(soup.select("h1"))
    if not candidate_headers:
        return None

    text = " ".join(
        header_tag.get_text(strip=True) for header_tag in candidate_headers if header_tag
    )
    # English Pattern: "1-48 of 18,802 Restaurants"
    match = re.search(r"of\s+([\d,]+)\s+restaurants", text, re.IGNORECASE)
    if match:
        count_str = match.group(1).replace(",", "")
        return int(count_str)

    # Traditional Chinese Pattern: "共 46 個餐廳" or "日本: 1-48 共 1,106 個餐廳"
    match = re.search(r"共\s*([\d,]+)\s*個餐廳", text)
    if match:
        count_str = match.group(1).replace(",", "")
        return int(count_str)

    return None


def extract_page_number(url: str, fallback: int) -> int:
    """Extract page number from URL query params."""

    parsed = urlparse(url)
    query_page = parse_qs(parsed.query).get("page", [])
    if query_page:
        candidate = query_page[0].strip()
        if candidate.isdigit():
            return int(candidate)
    path_match = _PAGE_PATH_PATTERN.search(parsed.path)
    if path_match:
        return int(path_match.group(1))
    return fallback


def extract_next_page_url(soup: BeautifulSoup, current_url: str) -> str | None:
    """Resolve next page URL from pagination links."""

    pagination_links = soup.select("ul.pagination li a")

    for link in pagination_links:
        if not link.select_one("i.fa-angle-right"):
            continue

        if next_url := _resolve_link_url(link, current_url):
            return next_url

    current_page_number = _extract_active_page_number(pagination_links, current_url)
    if current_page_number is not None:
        target_page_number = current_page_number + 1
        for link in pagination_links:
            if _extract_link_page_number(link, current_url) != target_page_number:
                continue
            if next_url := _resolve_link_url(link, current_url):
                return next_url

    for link in pagination_links:
        if not _is_right_arrow_link(link):
            continue

        if next_url := _resolve_link_url(link, current_url):
            return next_url

    return None


def _resolve_link_url(link: Any, current_url: str) -> str | None:
    href_value = link.get("href") if hasattr(link, "get") else None
    relative_link = href_value if isinstance(href_value, str) else None
    if not relative_link or relative_link == "#":
        return None

    new_full_url = urljoin(current_url, relative_link)
    if new_full_url == current_url:
        return None
    return new_full_url


def _extract_active_page_number(links: Sequence[Any], current_url: str) -> int | None:
    for link in links:
        class_values = link.get("class", []) if hasattr(link, "get") else []
        classes = set(class_values if isinstance(class_values, list) else [])
        if link.get("aria-current") == "page" or "active" in classes:
            return _extract_link_page_number(link, current_url)
    return extract_page_number(current_url, 0) or None


def _extract_link_page_number(link: Any, current_url: str) -> int | None:
    text = link.get_text(strip=True) if hasattr(link, "get_text") else ""
    if text.isdigit():
        return int(text)
    href_value = link.get("href") if hasattr(link, "get") else None
    if isinstance(href_value, str):
        page_number = extract_page_number(urljoin(current_url, href_value), 0)
        if page_number:
            return page_number
    return None


def _is_right_arrow_link(link: Any) -> bool:
    image = link.select_one("img") if hasattr(link, "select_one") else None
    if image is None:
        return False
    image_source = image.get("src", "")
    image_alt = image.get("alt", "")
    return any(
        _ARROW_RIGHT_IMAGE_PATTERN.search(value)
        for value in (str(image_source), str(image_alt))
    )
