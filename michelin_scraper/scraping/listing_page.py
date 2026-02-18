"""Listing page scraping and card-level parsing."""


import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from ..config import MICHELIN_BASE_URL
from .fetcher import fetch_page_soup
from .models import ItemHandler, ScrapeProgressReporter, ScrapeResultsPage
from .pagination import (
    extract_next_page_url,
    extract_page_number,
    extract_total_items,
    extract_total_pages,
)
from .parsers import parse_price_cuisine, parse_rating
from .restaurant_details import (
    build_empty_data,
    build_restaurant_record,
    extract_href,
    scrape_restaurant_page,
)

_LISTING_PAGE_TYPE = "listing"
_LISTING_CARD_SELECTOR = "div.card__menu"
_RESTAURANT_TITLE_SELECTOR = "h3.card__menu-content--title"
_CARD_FOOTER_SCORE_SELECTOR = "div.card__menu-footer--score"
_DISTINCTION_ICON_SELECTOR = "span.distinction-icon"
_CARD_LINK_SELECTOR = "a"


def _extract_region_segment(url: str) -> str:
    """Extract the region path segment (e.g. 'tainan-region') from a listing URL."""
    for segment in urlparse(url).path.split("/"):
        if segment.endswith("-region"):
            return segment
    return ""


def scrape_results_single_page(
    *,
    session: requests.Session,
    url: str,
    headers: dict[str, str],
    tls_verify: bool | str,
    page_number: int,
    estimated_total_pages: int | None,
    total_restaurants_so_far: int,
    progress_reporter: ScrapeProgressReporter,
    item_sleep_seconds: float = 0.0,
    on_item: ItemHandler | None = None,
    local_language: str | None = None,
    local_country_code: str | None = None,
    requested_language: str | None = None,
) -> ScrapeResultsPage:
    """Scrape one listing page and return rows and next-page metadata."""

    scraped_rows: list[dict[str, Any]] = []
    listing_page_url = url
    current_page_label = extract_page_number(listing_page_url, page_number)
    progress_reporter.update(f"Fetching listing page {current_page_label}.")

    listing_page_soup = fetch_page_soup(
        session=session,
        url=listing_page_url,
        headers=headers,
        tls_verify=tls_verify,
        progress_reporter=progress_reporter,
        page_type=_LISTING_PAGE_TYPE,
    )
    if listing_page_soup.soup is None:
        return ScrapeResultsPage(
            restaurant_rows=[],
            next_url=None,
            estimated_total_pages=estimated_total_pages,
            total_restaurants=None,
            fetch_failed=listing_page_soup.fetch_failed,
        )

    restaurant_cards = listing_page_soup.soup.select(_LISTING_CARD_SELECTOR)
    estimated_total_pages = _resolve_estimated_total_pages(
        listing_page_soup=listing_page_soup.soup,
        previous_estimate=estimated_total_pages,
    )
    total_restaurants_expected = extract_total_items(listing_page_soup.soup)

    _log_missing_cards_if_needed(
        restaurant_cards=restaurant_cards,
        page_label=current_page_label,
        progress_reporter=progress_reporter,
    )

    listing_region = _extract_region_segment(url)
    cards_total = len(restaurant_cards)

    # Pre-filter cards and extract metadata needed for parallel fetch.
    eligible_cards: list[tuple[int, Any, str]] = []  # (card_index, card, name)
    for card_index, listing_card in enumerate(restaurant_cards, start=1):
        restaurant_name = _extract_restaurant_name(listing_card)
        if listing_region:
            restaurant_url_for_check = _extract_restaurant_url(listing_card)
            if restaurant_url_for_check and listing_region not in urlparse(restaurant_url_for_check).path.split("/"):
                progress_reporter.log(
                    f"Skipping out-of-region card '{restaurant_name}' "
                    f"(expected region: {listing_region}, url: {restaurant_url_for_check})"
                )
                continue
        eligible_cards.append((card_index, listing_card, restaurant_name))

    # Submit detail-page fetches in parallel (up to 4 concurrent requests).
    _PREFETCH_WORKERS = min(4, len(eligible_cards)) if eligible_cards else 1
    with ThreadPoolExecutor(max_workers=_PREFETCH_WORKERS) as pool:
        futures: list[tuple[int, str, Future[dict[str, Any]]]] = []
        for card_index, listing_card, restaurant_name in eligible_cards:
            fut = pool.submit(
                _build_restaurant_row_from_card,
                session=session,
                listing_card=listing_card,
                restaurant_name=restaurant_name,
                headers=headers,
                tls_verify=tls_verify,
                progress_reporter=progress_reporter,
                local_language=local_language,
                local_country_code=local_country_code,
                requested_language=requested_language,
            )
            futures.append((card_index, restaurant_name, fut))

        # Collect results in order and call on_item as each completes.
        for i, (card_index, restaurant_name, fut) in enumerate(futures):
            _report_listing_card_progress(
                page_label=current_page_label,
                estimated_total_pages=estimated_total_pages,
                card_index=card_index,
                cards_total=cards_total,
                total_restaurants_so_far=total_restaurants_so_far,
                scraped_rows_count=len(scraped_rows),
                restaurant_name=restaurant_name,
                page_number=page_number,
                total_restaurants_expected=total_restaurants_expected,
                progress_reporter=progress_reporter,
            )
            row = fut.result()
            scraped_rows.append(row)
            if on_item is not None:
                on_item(page_number, estimated_total_pages, total_restaurants_expected, row)
            if i < len(futures) - 1 and item_sleep_seconds > 0:
                time.sleep(item_sleep_seconds)

    return ScrapeResultsPage(
        restaurant_rows=scraped_rows,
        next_url=extract_next_page_url(listing_page_soup.soup, listing_page_url),
        estimated_total_pages=estimated_total_pages,
        total_restaurants=total_restaurants_expected,
        fetch_failed=False,
    )


def _resolve_estimated_total_pages(
    *, listing_page_soup: Any, previous_estimate: int | None
) -> int | None:
    """Resolve total page estimate using current page pagination metadata."""

    discovered_total_pages = extract_total_pages(listing_page_soup)
    if discovered_total_pages is None:
        return previous_estimate
    return max(previous_estimate or 0, discovered_total_pages)


def _log_missing_cards_if_needed(
    *,
    restaurant_cards: list[Any],
    page_label: int,
    progress_reporter: ScrapeProgressReporter,
) -> None:
    """Log a message when the listing page contains no restaurant cards."""

    if restaurant_cards:
        return
    progress_reporter.log(f"No restaurant cards found on listing page {page_label}.")


def _report_listing_card_progress(
    *,
    page_label: int,
    estimated_total_pages: int | None,
    card_index: int,
    cards_total: int,
    total_restaurants_so_far: int,
    scraped_rows_count: int,
    restaurant_name: str,
    page_number: int,
    total_restaurants_expected: int | None,
    progress_reporter: ScrapeProgressReporter,
) -> None:
    """Report progress for one listing card before detail-page scraping."""

    estimated_progress = estimate_progress(
        current_page=page_number,
        card_index=card_index,
        cards_total=cards_total,
        total_pages=estimated_total_pages,
        total_restaurants_expected=total_restaurants_expected,
        total_restaurants_so_far=total_restaurants_so_far,
        scraped_rows_count=scraped_rows_count,
    )
    progress_reporter.update(
        (
            f"page {page_label}/{estimated_total_pages or '?'} "
            f"| item {card_index}/{cards_total} "
            f"| collected {total_restaurants_so_far + scraped_rows_count} "
            f"| {restaurant_name or '(no name)'}"
        ),
        progress=estimated_progress,
    )


def _extract_restaurant_name(listing_card: Any) -> str:
    """Extract restaurant name from one listing card."""

    title_tag = listing_card.select_one(_RESTAURANT_TITLE_SELECTOR)
    return title_tag.get_text(strip=True) if title_tag else ""


def _build_restaurant_row_from_card(
    *,
    session: requests.Session,
    listing_card: Any,
    restaurant_name: str,
    headers: dict[str, str],
    tls_verify: bool | str,
    progress_reporter: ScrapeProgressReporter,
    local_language: str | None = None,
    local_country_code: str | None = None,
    requested_language: str | None = None,
) -> dict[str, Any]:
    """Build one output row from listing card and detail page data."""

    city, price_range, cuisine_label = _extract_location_price_and_cuisine(listing_card)
    rating_label = parse_rating(listing_card.select_one(_DISTINCTION_ICON_SELECTOR))
    restaurant_url = _extract_restaurant_url(listing_card)
    restaurant_page_data = _fetch_restaurant_page_data(
        session=session,
        restaurant_url=restaurant_url,
        headers=headers,
        tls_verify=tls_verify,
        progress_reporter=progress_reporter,
        local_language=local_language,
        local_country_code=local_country_code,
        requested_language=requested_language,
    )

    return build_restaurant_record(
        name=restaurant_name,
        rating=rating_label,
        city=city,
        price=price_range,
        cuisine=cuisine_label,
        restaurant_url=restaurant_url,
        page_data=restaurant_page_data,
    )


def _extract_location_price_and_cuisine(listing_card: Any) -> tuple[str, str, str]:
    """Extract city, price range, and cuisine from card footer score blocks."""

    score_footer_tags = listing_card.select(_CARD_FOOTER_SCORE_SELECTOR)
    city_name = score_footer_tags[0].get_text(strip=True) if len(score_footer_tags) > 0 else ""
    price_range, cuisine_label = parse_price_cuisine(
        score_footer_tags[1] if len(score_footer_tags) > 1 else None
    )
    return city_name, price_range, cuisine_label


def _extract_restaurant_url(listing_card: Any) -> str:
    """Extract absolute Michelin restaurant URL from card link."""

    card_link_tag = listing_card.select_one(_CARD_LINK_SELECTOR)
    relative_restaurant_url = extract_href(card_link_tag)
    if not relative_restaurant_url:
        return ""
    return urljoin(MICHELIN_BASE_URL, relative_restaurant_url)


def _fetch_restaurant_page_data(
    *,
    session: requests.Session,
    restaurant_url: str,
    headers: dict[str, str],
    tls_verify: bool | str,
    progress_reporter: ScrapeProgressReporter,
    local_language: str | None = None,
    local_country_code: str | None = None,
    requested_language: str | None = None,
) -> dict[str, float | str]:
    """Fetch parsed detail fields for a restaurant page URL."""

    if not restaurant_url:
        return build_empty_data()
    return scrape_restaurant_page(
        session=session,
        url=restaurant_url,
        headers=headers,
        tls_verify=tls_verify,
        progress_reporter=progress_reporter,
        local_language=local_language,
        local_country_code=local_country_code,
        requested_language=requested_language,
    )


def estimate_progress(
    current_page: int,
    card_index: int,
    cards_total: int,
    total_pages: int | None,
    total_restaurants_expected: int | None = None,
    total_restaurants_so_far: int = 0,
    scraped_rows_count: int = 0,
) -> float | None:
    """Estimate overall run progress from page and card position."""

    # If we have total restaurant count, it's the most accurate way
    if total_restaurants_expected and total_restaurants_expected > 0:
        current_absolute_index = total_restaurants_so_far + scraped_rows_count + (card_index / max(1, cards_total))
        return min(0.99, current_absolute_index / total_restaurants_expected)

    # Fallback to page-based estimation
    if not total_pages or total_pages <= 0 or cards_total <= 0:
        return None
    current_page_fraction = card_index / cards_total
    return ((current_page - 1) + current_page_fraction) / total_pages
