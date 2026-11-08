"""Tests for fetch_page_soup retry and exponential backoff behavior."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import requests
from playwright.sync_api import Error

from michelin_scraper.config import HTTP_MAX_FETCH_RETRIES, HTTP_RETRY_BASE_DELAY_SECONDS
from michelin_scraper.scraping.fetcher import _BrowserHtmlFetcher, fetch_page_soup


class _NoOpReporter:
    def __init__(self) -> None:
        self.logged: list[str] = []

    def update(self, message: str, progress: float | None = None) -> None:
        del message, progress

    def log(self, message: str) -> None:
        self.logged.append(message)

    def finish(self, message: str | None = None) -> None:
        del message


def _make_response(status_code: int, text: str = "<html></html>") -> Mock:
    response = Mock()
    response.status_code = status_code
    response.text = text
    response.headers = {}
    response.raise_for_status = Mock()
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            f"HTTP {status_code}", response=response
        )
    return response


class FetchPageSoupRetryTests(unittest.TestCase):
    @patch("michelin_scraper.scraping.fetcher.time.sleep")
    def test_succeeds_on_first_attempt_without_sleep(self, mock_sleep: Mock) -> None:
        session = MagicMock()
        session.get.return_value = _make_response(200)
        reporter = _NoOpReporter()

        result = fetch_page_soup(
            session=session,
            url="https://example.com",
            headers={},
            tls_verify=True,
            progress_reporter=reporter,
            page_type="test",
        )

        self.assertFalse(result.fetch_failed)
        self.assertIsNotNone(result.soup)
        mock_sleep.assert_not_called()
        self.assertEqual(session.get.call_count, 1)

    @patch("michelin_scraper.scraping.fetcher.time.sleep")
    def test_retries_on_connection_error_then_succeeds(self, mock_sleep: Mock) -> None:
        session = MagicMock()
        session.get.side_effect = [
            requests.exceptions.ConnectionError("connection refused"),
            _make_response(200),
        ]
        reporter = _NoOpReporter()

        result = fetch_page_soup(
            session=session,
            url="https://example.com",
            headers={},
            tls_verify=True,
            progress_reporter=reporter,
            page_type="listing",
        )

        self.assertFalse(result.fetch_failed)
        self.assertIsNotNone(result.soup)
        self.assertEqual(session.get.call_count, 2)
        mock_sleep.assert_called_once_with(HTTP_RETRY_BASE_DELAY_SECONDS)

    @patch("michelin_scraper.scraping.fetcher.time.sleep")
    def test_retries_on_retryable_status_codes(self, mock_sleep: Mock) -> None:
        session = MagicMock()
        session.get.side_effect = [
            _make_response(429),
            _make_response(503),
            _make_response(200),
        ]
        reporter = _NoOpReporter()

        result = fetch_page_soup(
            session=session,
            url="https://example.com",
            headers={},
            tls_verify=True,
            progress_reporter=reporter,
            page_type="restaurant",
        )

        self.assertFalse(result.fetch_failed)
        self.assertIsNotNone(result.soup)
        self.assertEqual(session.get.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        mock_sleep.assert_has_calls([
            call(HTTP_RETRY_BASE_DELAY_SECONDS),
            call(HTTP_RETRY_BASE_DELAY_SECONDS * 2),
        ])

    @patch("michelin_scraper.scraping.fetcher.time.sleep")
    def test_fails_after_max_retries_exhausted(self, mock_sleep: Mock) -> None:
        session = MagicMock()
        session.get.side_effect = requests.exceptions.Timeout("timed out")
        reporter = _NoOpReporter()

        result = fetch_page_soup(
            session=session,
            url="https://example.com",
            headers={},
            tls_verify=True,
            progress_reporter=reporter,
            page_type="listing",
        )

        self.assertTrue(result.fetch_failed)
        self.assertIsNone(result.soup)
        self.assertEqual(session.get.call_count, HTTP_MAX_FETCH_RETRIES)
        self.assertTrue(any("after" in msg and "attempts" in msg for msg in reporter.logged))

    @patch("michelin_scraper.scraping.fetcher.time.sleep")
    def test_does_not_retry_on_client_error(self, mock_sleep: Mock) -> None:
        session = MagicMock()
        session.get.return_value = _make_response(404)
        reporter = _NoOpReporter()

        result = fetch_page_soup(
            session=session,
            url="https://example.com",
            headers={},
            tls_verify=True,
            progress_reporter=reporter,
            page_type="listing",
        )

        self.assertTrue(result.fetch_failed)
        mock_sleep.assert_not_called()
        self.assertEqual(session.get.call_count, 1)

    @patch("michelin_scraper.scraping.fetcher.time.sleep")
    def test_does_not_retry_on_non_transient_request_exception(self, mock_sleep: Mock) -> None:
        session = MagicMock()
        session.get.side_effect = requests.exceptions.TooManyRedirects("redirects")
        reporter = _NoOpReporter()

        result = fetch_page_soup(
            session=session,
            url="https://example.com",
            headers={},
            tls_verify=True,
            progress_reporter=reporter,
            page_type="listing",
        )

        self.assertTrue(result.fetch_failed)
        mock_sleep.assert_not_called()
        self.assertEqual(session.get.call_count, 1)

    @patch("michelin_scraper.scraping.fetcher._fetch_html_with_browser")
    @patch("michelin_scraper.scraping.fetcher.time.sleep")
    def test_uses_browser_fallback_for_michelin_waf_challenge(
        self,
        mock_sleep: Mock,
        mock_fetch_html_with_browser: Mock,
    ) -> None:
        session = MagicMock()
        response = _make_response(202, "")
        response.headers = {"x-amzn-waf-action": "challenge", "Content-Length": "0"}
        session.get.return_value = response
        mock_fetch_html_with_browser.return_value = (
            '<html><body><div class="card__menu">Alpha</div></body></html>'
        )
        reporter = _NoOpReporter()

        result = fetch_page_soup(
            session=session,
            url="https://guide.michelin.com/en/tw/taipei-region/taipei/restaurants",
            headers={},
            tls_verify=True,
            progress_reporter=reporter,
            page_type="listing",
        )

        self.assertFalse(result.fetch_failed)
        self.assertIsNotNone(result.soup)
        assert result.soup is not None
        self.assertEqual(result.soup.select_one(".card__menu").get_text(strip=True), "Alpha")
        mock_fetch_html_with_browser.assert_called_once()
        mock_sleep.assert_not_called()
        self.assertTrue(any("browser-rendered fetch" in message for message in reporter.logged))

    @patch("michelin_scraper.scraping.fetcher._fetch_html_with_browser")
    @patch("michelin_scraper.scraping.fetcher.time.sleep")
    def test_waf_challenge_fails_when_browser_fallback_returns_empty_html(
        self,
        mock_sleep: Mock,
        mock_fetch_html_with_browser: Mock,
    ) -> None:
        session = MagicMock()
        response = _make_response(202, "")
        response.headers = {"x-amzn-waf-action": "challenge", "Content-Length": "0"}
        session.get.return_value = response
        mock_fetch_html_with_browser.return_value = ""
        reporter = _NoOpReporter()

        result = fetch_page_soup(
            session=session,
            url="https://guide.michelin.com/en/tw/taipei-region/taipei/restaurants",
            headers={},
            tls_verify=True,
            progress_reporter=reporter,
            page_type="listing",
        )

        self.assertTrue(result.fetch_failed)
        self.assertIsNone(result.soup)
        mock_fetch_html_with_browser.assert_called_once()
        mock_sleep.assert_not_called()
        self.assertTrue(any("browser-rendered fetch returned empty HTML" in message for message in reporter.logged))


class BrowserHtmlFetcherRuntimeTests(unittest.TestCase):
    @patch("playwright.sync_api.sync_playwright")
    def test_transient_browser_network_change_retries_same_owned_page(self, start: Mock) -> None:
        runtime = start.return_value.start.return_value
        page = runtime.chromium.launch.return_value.new_context.return_value.new_page.return_value
        html = (Path(__file__).parent / "fixtures/michelin/brise-detail-2026-11-07.html").read_text()
        page.content.return_value = html
        page.goto.side_effect = [Error("Page.goto: net::ERR_NETWORK_CHANGED"), None]
        fetcher = _BrowserHtmlFetcher()
        try:
            self.assertEqual(fetcher.fetch(
                url="https://guide.michelin.com/tw/zh_TW/taipei-region/taipei/restaurant/brise",
                headers={}, required_selector="div.data-sheet__block--text",
            ), html)
            self.assertEqual(page.goto.call_count, 2)
            self.assertEqual(runtime.chromium.launch.call_count, 1)
        finally:
            fetcher.close()
        runtime.stop.assert_called_once()

    @patch("playwright.sync_api.sync_playwright")
    def test_browser_navigation_retry_is_bounded_and_transient_only(self, start: Mock) -> None:
        page = start.return_value.start.return_value.chromium.launch.return_value.new_context.return_value.new_page.return_value
        for message, count in (("net::ERR_NETWORK_CHANGED", HTTP_MAX_FETCH_RETRIES), ("net::ERR_NAME_NOT_RESOLVED", 1)):
            with self.subTest(message=message):
                page.goto.reset_mock()
                page.goto.side_effect = Error(message)
                fetcher = _BrowserHtmlFetcher()
                try:
                    with self.assertRaisesRegex(Error, message):
                        fetcher.fetch(url="https://guide.michelin.com", headers={})
                    self.assertEqual(page.goto.call_count, count)
                finally:
                    fetcher.close()

    @patch("playwright.sync_api.sync_playwright")
    def test_missing_bundled_browser_uses_installed_chrome(self, mock_sync_playwright: Mock) -> None:
        runtime = mock_sync_playwright.return_value.start.return_value
        browser = MagicMock()
        runtime.chromium.launch.side_effect = [
            Error("Executable doesn't exist at /missing/chromium/headless_shell"),
            browser,
        ]
        browser.new_context.return_value.new_page.return_value.content.return_value = "<html></html>"
        fetcher = _BrowserHtmlFetcher()
        try:
            self.assertEqual(fetcher.fetch(url="https://guide.michelin.com", headers={}), "<html></html>")
            runtime.chromium.launch.assert_has_calls([
                call(headless=True),
                call(headless=True, channel="chrome"),
            ])
            browser.new_context.return_value.close.assert_called_once()
        finally:
            fetcher.close()
        browser.close.assert_called_once()
        runtime.stop.assert_called_once()

    @patch("playwright.sync_api.sync_playwright")
    def test_available_bundled_browser_does_not_launch_chrome(self, mock_sync_playwright: Mock) -> None:
        runtime = mock_sync_playwright.return_value.start.return_value
        fetcher = _BrowserHtmlFetcher()
        try:
            self.assertIs(fetcher._ensure_browser(), runtime.chromium.launch.return_value)
            runtime.chromium.launch.assert_called_once_with(headless=True)
        finally:
            fetcher.close()

    @patch("playwright.sync_api.sync_playwright")
    def test_other_launch_failures_are_not_retried(self, mock_sync_playwright: Mock) -> None:
        runtime = mock_sync_playwright.return_value.start.return_value
        runtime.chromium.launch.side_effect = Error("Browser crashed during launch")
        fetcher = _BrowserHtmlFetcher()
        try:
            with self.assertRaisesRegex(Error, "Browser crashed during launch"):
                fetcher._ensure_browser()
            runtime.chromium.launch.assert_called_once_with(headless=True)
        finally:
            fetcher.close()


if __name__ == "__main__":
    unittest.main()
