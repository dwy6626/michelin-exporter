"""HTML fetching utilities for Michelin scraping."""

import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from ..config import (
    HTTP_MAX_FETCH_RETRIES,
    HTTP_RETRY_BASE_DELAY_SECONDS,
    HTTP_RETRYABLE_STATUS_CODES,
    REQUEST_TIMEOUT_SECONDS,
)
from .models import ScrapeProgressReporter


@dataclass(frozen=True)
class FetchPageResult:
    """Result envelope for one HTML fetch attempt."""

    soup: BeautifulSoup | None
    fetch_failed: bool


def fetch_page_soup(
    *,
    session: requests.Session,
    url: str,
    headers: dict[str, str],
    tls_verify: bool | str,
    progress_reporter: ScrapeProgressReporter,
    page_type: str,
) -> FetchPageResult:
    """Fetch one HTML page and parse it into BeautifulSoup.

    Retries up to HTTP_MAX_FETCH_RETRIES times with exponential backoff on
    transient network errors and retryable HTTP status codes (429, 5xx).
    """

    last_error: str = ""
    for attempt in range(HTTP_MAX_FETCH_RETRIES):
        if attempt > 0:
            delay = HTTP_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            progress_reporter.log(
                f"Retrying {page_type} page (attempt {attempt + 1}/{HTTP_MAX_FETCH_RETRIES},"
                f" waiting {delay:.0f}s): {last_error}"
            )
            time.sleep(delay)

        try:
            response = session.get(
                url,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
                verify=tls_verify,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_error = str(exc)
            continue
        except requests.exceptions.RequestException as exc:
            progress_reporter.log(f"Failed to fetch {page_type} page: {exc}")
            return FetchPageResult(soup=None, fetch_failed=True)

        if response.status_code in HTTP_RETRYABLE_STATUS_CODES:
            last_error = f"HTTP {response.status_code}"
            continue

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            progress_reporter.log(f"Failed to fetch {page_type} page: {exc}")
            return FetchPageResult(soup=None, fetch_failed=True)

        return FetchPageResult(soup=BeautifulSoup(response.text, "html.parser"), fetch_failed=False)

    progress_reporter.log(
        f"Failed to fetch {page_type} page after {HTTP_MAX_FETCH_RETRIES} attempts: {last_error}"
    )
    return FetchPageResult(soup=None, fetch_failed=True)
