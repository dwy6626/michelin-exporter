"""Pagination engine for Michelin listing scraping."""


import time

import requests

from ..config import DEFAULT_SLEEP_SECONDS, HEADERS
from ..domain import ScrapeRunMetrics
from .listing_page import scrape_results_single_page
from .models import (
    InterruptHandler,
    ItemHandler,
    NullProgressReporter,
    PageHandler,
    ScrapeProgressReporter,
)
from .pagination import extract_page_number


def crawl(
    start_url: str,
    on_page: PageHandler,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    headers: dict[str, str] | None = None,
    tls_verify: bool | str = True,
    start_page_number: int = 1,
    start_estimated_total_pages: int | None = None,
    initial_total_restaurants: int = 0,
    progress_reporter: ScrapeProgressReporter | None = None,
    max_pages: int = 0,
    on_item: ItemHandler | None = None,
    on_interrupt: InterruptHandler | None = None,
    local_language: str | None = None,
    local_country_code: str | None = None,
    requested_language: str | None = None,
) -> ScrapeRunMetrics:
    """Stream Michelin scrape results page-by-page through a callback."""

    request_headers = headers or HEADERS
    effective_reporter = progress_reporter or NullProgressReporter()
    return run_scrape_loop(
        start_url=start_url,
        sleep_seconds=sleep_seconds,
        headers=request_headers,
        tls_verify=tls_verify,
        on_page=on_page,
        on_item=on_item,
        on_interrupt=on_interrupt,
        start_page_number=start_page_number,
        start_estimated_total_pages=start_estimated_total_pages,
        initial_total_restaurants=initial_total_restaurants,
        progress_reporter=effective_reporter,
        max_pages=max_pages,
        local_language=local_language,
        local_country_code=local_country_code,
        requested_language=requested_language,
    )


def run_scrape_loop(
    *,
    start_url: str,
    sleep_seconds: float,
    headers: dict[str, str],
    tls_verify: bool | str,
    on_page: PageHandler | None,
    on_item: ItemHandler | None = None,
    on_interrupt: InterruptHandler | None = None,
    start_page_number: int,
    start_estimated_total_pages: int | None,
    initial_total_restaurants: int,
    progress_reporter: ScrapeProgressReporter,
    max_pages: int,
    local_language: str | None = None,
    local_country_code: str | None = None,
    requested_language: str | None = None,
) -> ScrapeRunMetrics:
    """Run the main pagination loop and dispatch page events."""

    progress_reporter.log(f"Starting scrape run from {start_url}")

    current_url = start_url
    page_count = start_page_number
    estimated_total_pages = start_estimated_total_pages
    visited_urls: set[str] = set()
    processed_pages = 0
    collected_restaurants = initial_total_restaurants
    failed_fetches = 0

    with requests.Session() as session:
        while current_url:
            if current_url in visited_urls:
                progress_reporter.log(
                    f"Detected a pagination loop at {current_url}. Stopping this run safely."
                )
                break
            visited_urls.add(current_url)

            try:
                page_result = scrape_results_single_page(
                    session=session,
                    url=current_url,
                    headers=headers,
                    tls_verify=tls_verify,
                    page_number=page_count,
                    estimated_total_pages=estimated_total_pages,
                    total_restaurants_so_far=collected_restaurants,
                    progress_reporter=progress_reporter,
                    item_sleep_seconds=sleep_seconds,
                    on_item=on_item,
                    local_language=local_language,
                    local_country_code=local_country_code,
                    requested_language=requested_language,
                )
            except KeyboardInterrupt:
                if on_interrupt is not None:
                    on_interrupt(current_url, page_count, estimated_total_pages, collected_restaurants)
                raise
            estimated_total_pages = page_result.estimated_total_pages
            if page_result.fetch_failed:
                failed_fetches += 1
                progress_reporter.log(
                    f"Stopping run because listing page fetch failed at page {page_count}."
                )
                break

            restaurant_list = page_result.restaurant_rows
            next_url = page_result.next_url
            processed_pages += 1
            collected_restaurants += len(restaurant_list)

            effective_next_url, next_page_number = resolve_next_page(
                next_url=next_url,
                page_count=page_count,
                estimated_total_pages=estimated_total_pages,
                progress_reporter=progress_reporter,
            )

            try:
                if on_page is not None:
                    on_page(
                        page_count,
                        current_url,
                        restaurant_list,
                        effective_next_url,
                        next_page_number,
                        estimated_total_pages,
                        collected_restaurants,
                    )
            except KeyboardInterrupt:
                if on_interrupt is not None:
                    on_interrupt(
                        current_url,
                        page_count,
                        estimated_total_pages,
                        collected_restaurants,
                    )
                raise

            if max_pages > 0 and processed_pages >= max_pages:
                progress_reporter.log(
                    f"Reached page limit ({processed_pages}/{max_pages}). Stopping this run."
                )
                break

            if not effective_next_url:
                if (
                    collected_restaurants == initial_total_restaurants
                    and processed_pages == 1
                    and start_page_number == 1
                ):
                    progress_reporter.log(
                        "No data was extracted from the first page. Run cannot continue."
                    )
                break

            try:
                progress_reporter.update(f"Loading listing page {next_page_number}.")
                time.sleep(sleep_seconds)
            except KeyboardInterrupt:
                if on_interrupt is not None:
                    on_interrupt(
                        effective_next_url,
                        next_page_number,
                        estimated_total_pages,
                        collected_restaurants,
                    )
                raise

            current_url = effective_next_url
            page_count = next_page_number

    progress_reporter.finish()
    return ScrapeRunMetrics(
        total_restaurants=collected_restaurants,
        processed_pages=processed_pages,
        fetch_failures=failed_fetches,
    )


def resolve_next_page(
    *,
    next_url: str | None,
    page_count: int,
    estimated_total_pages: int | None,
    progress_reporter: ScrapeProgressReporter,
) -> tuple[str | None, int]:
    """Validate next-page metadata before continuing pagination."""

    if not next_url:
        return None, page_count

    if estimated_total_pages is not None and page_count >= estimated_total_pages:
        progress_reporter.log(
            
                "Reached the maximum page reported by the site "
                f"({estimated_total_pages}). Stopping."
            
        )
        return None, page_count

    next_page_number = extract_page_number(next_url, page_count + 1)
    if estimated_total_pages is not None and next_page_number > estimated_total_pages:
        progress_reporter.log(
            
                "The next page exceeds the site-reported maximum "
                f"({next_page_number} > {estimated_total_pages}). Stopping."
            
        )
        return None, page_count

    return next_url, next_page_number
