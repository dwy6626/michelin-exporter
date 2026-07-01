"""HTML fetching utilities for Michelin scraping."""

import time
from dataclasses import dataclass
from threading import local
from typing import Any

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


_BROWSER_FETCH_TIMEOUT_MS = 60_000
_BROWSER_FETCH_SETTLE_MS = 5_000
_THREAD_LOCAL_BROWSER_FETCHER = local()


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

        if _is_browser_challenge_response(response):
            progress_reporter.log(
                f"Michelin {page_type} page returned a browser challenge; "
                "retrying with browser-rendered fetch."
            )
            try:
                browser_html = _fetch_html_with_browser(url=url, headers=headers)
            except Exception as exc:  # noqa: BLE001
                progress_reporter.log(
                    f"Failed browser-rendered fetch for {page_type} page: {exc}"
                )
                return FetchPageResult(soup=None, fetch_failed=True)
            if not browser_html.strip():
                progress_reporter.log(
                    f"Failed browser-rendered fetch for {page_type} page: "
                    "browser-rendered fetch returned empty HTML."
                )
                return FetchPageResult(soup=None, fetch_failed=True)
            return FetchPageResult(
                soup=BeautifulSoup(browser_html, "html.parser"),
                fetch_failed=False,
            )

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


def _is_browser_challenge_response(response: requests.Response) -> bool:
    headers = getattr(response, "headers", {})
    waf_action = str(headers.get("x-amzn-waf-action", "")).casefold()
    if waf_action == "challenge":
        return True
    return response.status_code == 202 and not response.text.strip()


def _fetch_html_with_browser(*, url: str, headers: dict[str, str]) -> str:
    fetcher = _get_browser_html_fetcher()
    return fetcher.fetch(url=url, headers=headers)


def _get_browser_html_fetcher() -> _BrowserHtmlFetcher:
    fetcher = getattr(_THREAD_LOCAL_BROWSER_FETCHER, "fetcher", None)
    if not isinstance(fetcher, _BrowserHtmlFetcher):
        fetcher = _BrowserHtmlFetcher()
        _THREAD_LOCAL_BROWSER_FETCHER.fetcher = fetcher
    return fetcher


class _BrowserHtmlFetcher:
    def __init__(self) -> None:
        self._playwright: Any | None = None
        self._browser: Any | None = None

    def fetch(self, *, url: str, headers: dict[str, str]) -> str:
        browser = self._ensure_browser()
        context = browser.new_context(
            user_agent=headers.get("User-Agent"),
            extra_http_headers={
                key: value
                for key, value in headers.items()
                if key.casefold() != "user-agent"
            },
        )
        try:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=_BROWSER_FETCH_TIMEOUT_MS)
            page.wait_for_timeout(_BROWSER_FETCH_SETTLE_MS)
            return str(page.content())
        finally:
            context.close()

    def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run `uv sync` and "
                "`uv run playwright install chromium`."
            ) from exc

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        return self._browser

    def close(self) -> None:
        browser = self._browser
        playwright = self._playwright
        self._browser = None
        self._playwright = None
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()
