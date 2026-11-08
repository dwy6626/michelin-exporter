"""HTML fetching utilities for Michelin scraping."""

import time
from dataclasses import dataclass
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


def fetch_page_soup(
    *,
    session: requests.Session,
    url: str,
    headers: dict[str, str],
    tls_verify: bool | str,
    progress_reporter: ScrapeProgressReporter,
    page_type: str,
    required_selector: str | None = None,
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
        else:
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as exc:
                progress_reporter.log(f"Failed to fetch {page_type} page: {exc}")
                return FetchPageResult(soup=None, fetch_failed=True)
            soup = BeautifulSoup(response.text, "html.parser")
            if _has_required_content(soup, required_selector):
                return FetchPageResult(soup=soup, fetch_failed=False)
            progress_reporter.log(
                f"Michelin {page_type} page lacks required content {required_selector!r} at {url}; "
                "retrying with browser-rendered fetch."
            )

        try:
            browser_html = _fetch_html_with_browser(url=url, headers=headers, required_selector=required_selector)
        except Exception as exc:  # noqa: BLE001
            progress_reporter.log(f"Failed browser-rendered fetch for {page_type} page at {url}: {exc}")
            return FetchPageResult(soup=None, fetch_failed=True)
        if not browser_html.strip():
            progress_reporter.log(
                f"Failed browser-rendered fetch for {page_type} page: browser-rendered fetch returned empty HTML."
            )
            return FetchPageResult(soup=None, fetch_failed=True)
        soup = BeautifulSoup(browser_html, "html.parser")
        if not _has_required_content(soup, required_selector):
            progress_reporter.log(f"Failed browser-rendered fetch for {page_type} page at {url}: missing {required_selector!r}.")
            return FetchPageResult(soup=None, fetch_failed=True)
        return FetchPageResult(soup=soup, fetch_failed=False)

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


def _has_required_content(soup: BeautifulSoup, selector: str | None) -> bool:
    if selector is None:
        return True
    element = soup.select_one(selector)
    return element is not None and bool(element.get_text(strip=True))


def _fetch_html_with_browser(*, url: str, headers: dict[str, str], required_selector: str | None = None) -> str:
    fetcher = _BrowserHtmlFetcher()
    try:
        return fetcher.fetch(url=url, headers=headers, required_selector=required_selector)
    finally:
        fetcher.close()


class _BrowserHtmlFetcher:
    def __init__(self) -> None:
        self._playwright: Any | None = None
        self._browser: Any | None = None

    def fetch(self, *, url: str, headers: dict[str, str], required_selector: str | None = None) -> str:
        from playwright.sync_api import Error

        deadline = time.monotonic() + _BROWSER_FETCH_TIMEOUT_MS / 1000
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
            for attempt in range(HTTP_MAX_FETCH_RETRIES):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=max(1, int((deadline - time.monotonic()) * 1000)))
                    break
                except Error as exc:
                    if (
                        "net::ERR_NETWORK_CHANGED" not in str(exc)
                        or attempt == HTTP_MAX_FETCH_RETRIES - 1
                        or time.monotonic() >= deadline
                    ):
                        raise
            if required_selector is not None:
                page.wait_for_selector(
                    required_selector, state="attached", timeout=max(1, int((deadline - time.monotonic()) * 1000))
                )
            else:
                page.wait_for_timeout(_BROWSER_FETCH_SETTLE_MS)
            return str(page.content())
        finally:
            context.close()

    def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        try:
            from playwright.sync_api import Error, sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run `uv sync` and "
                "`uv run playwright install chromium`."
            ) from exc

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=True)
        except Error as exc:
            if "Executable doesn't exist" not in str(exc):
                raise
            self._browser = self._playwright.chromium.launch(headless=True, channel="chrome")
        return self._browser

    def close(self) -> None:
        browser = self._browser
        playwright = self._playwright
        self._browser = None
        self._playwright = None
        try:
            if browser is not None:
                browser.close()
        finally:
            if playwright is not None:
                playwright.stop()
