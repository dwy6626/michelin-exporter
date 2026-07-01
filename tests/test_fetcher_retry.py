"""Tests for fetch_page_soup retry and exponential backoff behavior."""

import unittest
from unittest.mock import MagicMock, Mock, call, patch

import requests

from michelin_scraper.config import HTTP_MAX_FETCH_RETRIES, HTTP_RETRY_BASE_DELAY_SECONDS
from michelin_scraper.scraping.fetcher import fetch_page_soup


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


if __name__ == "__main__":
    unittest.main()
