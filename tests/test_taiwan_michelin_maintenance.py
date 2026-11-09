"""Acceptance boundaries for the official Taiwan Michelin maintenance workflow."""

import copy
import hashlib
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import requests
from playwright.sync_api import Error as PlaywrightError

from michelin_scraper.adapters.google_maps_driver import GoogleMapsDriverConfig
from michelin_scraper.application.taiwan_michelin_maintenance import (
    _MaintenanceDriver,
    applied_from_acceptance,
    audit_memberships,
    bootstrap_applied,
)
from michelin_scraper.scraping.models import ScrapeResultsPage
from michelin_scraper.sources.taiwan_michelin import (
    SOURCE_JOB_ID,
    SOURCE_URL,
    _FreshReaderSession,
    discover_taiwan_michelin,
    semantic_fingerprint,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures/michelin/taiwan-maintenance-accepted-2026-11-08.json").read_text())


class FreshReaderTests(unittest.TestCase):
    def _response(self, status: int, text: str) -> requests.Response:
        response = requests.Response()
        response.status_code = status
        response.url = SOURCE_URL
        response._content = text.encode()
        response.encoding = "utf-8"
        return response

    def _html(self) -> str:
        return (Path(__file__).parent / "fixtures/michelin/taiwan-region-2026-11-07.html").read_text()

    @patch.object(_FreshReaderSession, "_browser_get")
    @patch("requests.Session.get")
    def test_direct_http_success_does_not_use_browser(self, direct, browser) -> None:
        direct.return_value = self._response(200, self._html())
        with _FreshReaderSession() as session:
            self.assertIs(session.get(SOURCE_URL), direct.return_value)
            self.assertEqual(session.provenance[0]["transport"], "direct-http")
            self.assertEqual(session.provenance[0]["resolved_url"], SOURCE_URL)
        browser.assert_not_called()

    @patch.object(_FreshReaderSession, "_browser_get")
    @patch("requests.Session.get")
    def test_challenge_or_redirect_uses_original_source_html(self, direct, browser) -> None:
        browser.return_value = self._html(), "cron-source-dom"
        for status, resolved in ((202, SOURCE_URL), (403, SOURCE_URL), (200, "https://example.com/")):
            direct.return_value = self._response(status, "")
            direct.return_value.url = resolved
            with self.subTest(status=status), _FreshReaderSession() as session:
                response = session.get(SOURCE_URL)
                self.assertEqual(response.text, self._html())
                self.assertEqual(response.url, SOURCE_URL)
                self.assertEqual(session.provenance[0]["source_status_code"], 200)
                self.assertEqual(session.provenance[0]["direct_status_code"], status)
        self.assertEqual(browser.call_count, 3)

    @patch.object(_FreshReaderSession, "_browser_get")
    @patch("requests.Session.get")
    def test_http_failure_keeps_remaining_collection_on_source_transport(self, direct, browser) -> None:
        direct.return_value = self._response(403, "")
        browser.return_value = self._html(), "cron-source-fetch"
        with _FreshReaderSession() as session:
            session.get(SOURCE_URL)
            session.get(SOURCE_URL + "/page/2")
        direct.assert_called_once()
        self.assertEqual(browser.call_count, 2)

    @patch.object(_FreshReaderSession, "_assert_admitted")
    @patch("michelin_scraper.sources.taiwan_michelin.sync_playwright")
    @patch("requests.Session.get")
    def test_parallel_details_use_one_owner_thread_one_page_and_cleanup(self, direct, playwright, admitted) -> None:
        direct.return_value = self._response(202, "")
        thread_ids = []
        client = playwright.return_value.start.return_value
        browser = client.chromium.connect_over_cdp.return_value
        context = Mock()
        browser.contexts = [context]
        page = context.new_page.return_value
        page.url = "about:blank"

        def goto(url, **kwargs):
            thread_ids.append(threading.get_ident())
            page.url = url
            return Mock(status=200, url=url, headers={})

        def fetch(*args):
            thread_ids.append(threading.get_ident())
            return {"status": 200, "url": SOURCE_URL, "html": self._html(), "challenge": None}

        page.goto.side_effect = goto
        page.content.return_value = self._html()
        page.evaluate.side_effect = fetch
        page.close.side_effect = lambda: thread_ids.append(threading.get_ident())
        with _FreshReaderSession() as session, ThreadPoolExecutor(max_workers=4) as callers:
            responses = list(callers.map(lambda _: session.get(SOURCE_URL), range(4)))
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(len(set(thread_ids)), 1)
        self.assertNotEqual(thread_ids[0], threading.get_ident())
        context.new_page.assert_called_once()
        page.goto.assert_called_once()
        self.assertEqual(page.evaluate.call_count, 3)
        page.close.assert_called_once()
        client.stop.assert_called_once()
        browser.close.assert_not_called()
        context.close.assert_not_called()
        self.assertEqual(admitted.call_count, 4)

    @patch.object(_FreshReaderSession, "_assert_admitted")
    @patch("michelin_scraper.sources.taiwan_michelin.sync_playwright")
    @patch("requests.Session.get")
    def test_wrong_source_status_redirect_or_fetch_failure_rejects_and_cleans_up(self, direct, playwright, admitted) -> None:
        direct.return_value = self._response(202, "")
        client = playwright.return_value.start.return_value
        page = client.chromium.connect_over_cdp.return_value.contexts[0].new_page.return_value
        page.url = "about:blank"
        for status, resolved in ((403, SOURCE_URL), (200, "https://example.com/")):
            page.goto.return_value = Mock(status=status, url=resolved)
            with self.subTest(status=status), _FreshReaderSession() as session, self.assertRaises(ValueError):
                session.get(SOURCE_URL)
        self.assertEqual(page.close.call_count, 2)
        self.assertEqual(client.stop.call_count, 2)

    @patch("michelin_scraper.sources.taiwan_michelin.sync_playwright")
    def test_no_matching_held_lease_makes_no_browser_connection(self, playwright) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "HERMES_HOME": temp, "HERMES_CRON_BROWSER_LANE_TOKEN": "test-lease",
        }):
            lock = Path(temp) / "cron/locks/browser-cdp.lock"
            lock.parent.mkdir(parents=True)
            for proof in ({"job_id": "another-job", "token": "test-lease"},
                          {"job_id": SOURCE_JOB_ID, "token": "wrong-token"},
                          {"job_id": SOURCE_JOB_ID, "token": "test-lease"}):
                lock.write_text(json.dumps(proof))
                with self.subTest(proof=proof), _FreshReaderSession() as session, self.assertRaises(ValueError):
                    session._browser_get(SOURCE_URL)
        playwright.assert_not_called()

    @patch.object(_FreshReaderSession, "_assert_admitted")
    @patch("michelin_scraper.sources.taiwan_michelin.sync_playwright")
    @patch("requests.Session.get")
    def test_transient_202_has_bounded_normal_navigation_backoff_and_still_requires_200(self, direct, playwright, admitted) -> None:
        direct.return_value = self._response(403, "")
        client = playwright.return_value.start.return_value
        page = client.chromium.connect_over_cdp.return_value.contexts[0].new_page.return_value
        page.url = SOURCE_URL
        page.evaluate.return_value = {"status": 202, "url": SOURCE_URL, "html": "", "challenge": "challenge"}
        page.content.return_value = self._html()
        page.goto.side_effect = [Mock(status=202), Mock(status=200, url=SOURCE_URL, headers={})]
        with _FreshReaderSession() as session:
            self.assertEqual(session.get(SOURCE_URL).text, self._html())
        self.assertEqual(page.goto.call_count, 2)
        page.wait_for_timeout.assert_called_once_with(3000)
        page.goto.reset_mock(side_effect=True)
        page.goto.return_value = Mock(status=202)
        with _FreshReaderSession() as session, self.assertRaisesRegex(ValueError, "status=202"):
            session.get(SOURCE_URL)
        self.assertEqual(page.goto.call_count, 3)

    @patch.object(_FreshReaderSession, "_assert_admitted")
    @patch("michelin_scraper.sources.taiwan_michelin.sync_playwright")
    @patch("requests.Session.get")
    def test_normal_fetch_error_falls_back_to_navigation_on_same_owned_page(self, direct, playwright, admitted) -> None:
        direct.return_value = self._response(403, "")
        client = playwright.return_value.start.return_value
        page = client.chromium.connect_over_cdp.return_value.contexts[0].new_page.return_value
        page.url = SOURCE_URL
        page.evaluate.side_effect = PlaywrightError("Failed to fetch")
        page.goto.return_value = Mock(status=200, url=SOURCE_URL, headers={})
        page.content.return_value = self._html()
        with _FreshReaderSession() as session:
            self.assertEqual(session.get(SOURCE_URL).text, self._html())
            self.assertEqual(session.provenance[0]["transport"], "cron-source-dom")
        page.goto.assert_called_once()
        page.close.assert_called_once()

    @patch.object(_FreshReaderSession, "_browser_get")
    @patch("requests.Session.get")
    def test_foreign_url_or_expired_budget_makes_no_network_request(self, direct, browser) -> None:
        with _FreshReaderSession() as session:
            with self.assertRaises(ValueError):
                session.get("https://example.com/")
            session._deadline = 0
            with self.assertRaises(ValueError):
                session.get(SOURCE_URL)
        direct.assert_not_called()
        browser.assert_not_called()


class TaiwanMichelinSourceTests(unittest.TestCase):
    def test_semantics_ignore_order_and_transient_links_but_detect_rating_name_address(self) -> None:
        rows = copy.deepcopy(FIXTURE["source_rows"])
        baseline = semantic_fingerprint(rows)
        rows.reverse()
        rows[0]["Michelin Website"] += "?test=transient"
        self.assertEqual(semantic_fingerprint(rows), baseline)
        for field in ("Name", "Address", "Rating"):
            changed = copy.deepcopy(rows)
            changed[0][field] = rows[1][field]
            if field == "Rating" and changed[0][field] == rows[0][field]:
                changed[0][field] = "Selected"
            self.assertNotEqual(semantic_fingerprint(changed), baseline)

    @patch("michelin_scraper.sources.taiwan_michelin.scrape_results_single_page")
    def test_full_next_chain_uses_only_the_owned_source_transport(self, scrape) -> None:
        rows = copy.deepcopy(FIXTURE["source_rows"])
        scrape.side_effect = [
            ScrapeResultsPage(rows[:2], SOURCE_URL + "/page/2", 1, 3, False),
            ScrapeResultsPage(rows[2:], None, 2, 3, False),
        ]
        source, actual = discover_taiwan_michelin()
        self.assertEqual(actual, rows)
        self.assertEqual(source["source_point_count"], 3)
        self.assertEqual([p["count"] for p in source["pages"]], [2, 1])
        self.assertTrue(all(call.kwargs["browser_fallback"] is False for call in scrape.call_args_list))

    @patch("michelin_scraper.sources.taiwan_michelin.scrape_results_single_page")
    def test_empty_partial_loop_foreign_and_duplicate_sources_fail_closed(self, scrape) -> None:
        rows = copy.deepcopy(FIXTURE["source_rows"])
        for result in (
            ScrapeResultsPage([], None, 1, 0, False),
            ScrapeResultsPage(rows[:1], None, 1, 3, False),
            ScrapeResultsPage(rows[:1], SOURCE_URL, 1, 3, False),
            ScrapeResultsPage(rows[:1], "https://example.com/page/2", 1, 3, False),
            ScrapeResultsPage([rows[0], rows[0]], None, 1, 2, False),
            ScrapeResultsPage([], None, 1, None, True),
        ):
            with self.subTest(result=result):
                scrape.side_effect = None
                scrape.return_value = result
                with self.assertRaises(ValueError):
                    discover_taiwan_michelin()


class TaiwanMichelinAcceptanceTests(unittest.TestCase):
    def _acceptance(self, directory: Path) -> dict:
        path = directory / "acceptance/source-434-rows.jsonl"
        path.parent.mkdir()
        path.write_text("".join(json.dumps(row) + "\n" for row in FIXTURE["source_rows"]))
        value = {
            "status": "complete", "completed_at_utc": "2026-11-08T05:50:54Z", "source": {"total": 3},
            "membership_audit": {"source_rows": 3, "physical_memberships": 3,
                                 "missing": 0, "duplicate_memberships": 0, "unidentified_memberships": 0},
            "lists": {"stars": {"source_count": 3, "signed_in_physical_count": 3,
                                "anonymous_physical_count": 3, "visibility": "anyone-with-link-view",
                                "url": "https://maps.app.goo.gl/F3V9wqZdyEK44ZiC7"}},
            "artifacts": {path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}},
        }
        (directory / "acceptance-manifest.json").write_text(json.dumps(value))
        return value

    def test_baseline_only_comes_from_bound_complete_accepted_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._acceptance(root)
            applied = bootstrap_applied(root)
            self.assertEqual(applied["source_fingerprint"], semantic_fingerprint(FIXTURE["source_rows"]))
            self.assertEqual(applied, applied_from_acceptance(root))
            (root / "acceptance/source-434-rows.jsonl").write_text("{}\n")
            with self.assertRaises(ValueError):
                applied_from_acceptance(root)

    def test_incomplete_anonymous_acceptance_and_duplicate_memberships_are_rejected(self) -> None:
        for target in ("anonymous", "duplicates"):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                acceptance = self._acceptance(root)
                if target == "anonymous":
                    acceptance["lists"]["stars"]["anonymous_physical_count"] = 2
                else:
                    acceptance["membership_audit"]["duplicate_memberships"] = 1
                (root / "acceptance-manifest.json").write_text(json.dumps(acceptance))
                with self.assertRaises(ValueError):
                    bootstrap_applied(root)

    def test_recorded_notes_reconcile_every_row_and_reject_duplicates_or_extras(self) -> None:
        rows = FIXTURE["source_rows"]
        inventory = FIXTURE["inventory"]
        self.assertEqual(len(audit_memberships(rows, inventory, {})), 3)
        bad = copy.deepcopy(inventory)
        bad["rows"].append(dict(bad["rows"][0], index=3))
        bad["physical_count"] = 4
        with self.assertRaises(ValueError):
            audit_memberships(rows, bad, {})


class MaintenanceBrowserLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_per_list_writer_start_reuses_one_owned_page(self) -> None:
        with patch("michelin_scraper.adapters.google_maps_driver.GoogleMapsDriver.start", new_callable=AsyncMock) as start:
            driver = _MaintenanceDriver(GoogleMapsDriverConfig(
                user_data_dir=Path("/unused-test-profile"), headless=True, sync_delay_seconds=0,
            ))
            await driver.start()
            await driver.start()
            start.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
