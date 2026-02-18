"""Pagination helpers for Michelin listing pages."""

from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup


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
    return fallback


def extract_next_page_url(soup: BeautifulSoup, current_url: str) -> str | None:
    """Resolve next page URL from pagination links."""

    pagination_links = soup.select("ul.pagination li a")

    for link in pagination_links:
        if not link.select_one("i.fa-angle-right"):
            continue

        href_value = link.get("href")
        relative_link = href_value if isinstance(href_value, str) else None
        if not relative_link or relative_link == "#":
            return None

        new_full_url = urljoin(current_url, relative_link)
        if new_full_url == current_url:
            return None
        return new_full_url

    return None
