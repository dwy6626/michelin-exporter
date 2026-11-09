"""Acceptance boundaries for the official Taiwan Michelin maintenance workflow."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import requests

from michelin_scraper.adapters.google_maps_driver import GoogleMapsDriverConfig
from michelin_scraper.application.taiwan_michelin_maintenance import (
    _MaintenanceDriver,
    applied_from_acceptance,
    audit_memberships,
    bootstrap_applied,
)
from michelin_scraper.scraping.models import ScrapeResultsPage
from michelin_scraper.sources.taiwan_michelin import (
    READER_URL,
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
        response._content = text.encode()
        response.encoding = "utf-8"
        return response

    def _payload(self) -> dict:
        html = (Path(__file__).parent / "fixtures/michelin/taiwan-region-2026-11-07.html").read_text()
        return {"success": True, "data": {"rawHtml": html, "metadata": {
            "sourceURL": SOURCE_URL, "url": SOURCE_URL, "statusCode": 200,
        }}}

    @patch("michelin_scraper.sources.taiwan_michelin.requests.post")
    @patch("requests.Session.get")
    def test_direct_http_success_does_not_use_vendor(self, direct, post) -> None:
        direct.return_value = self._response(200, self._payload()["data"]["rawHtml"])
        with _FreshReaderSession() as session:
            self.assertIs(session.get(SOURCE_URL), direct.return_value)
            self.assertEqual(session.provenance[0]["transport"], "direct-http")
        post.assert_not_called()

    @patch("michelin_scraper.scraping.fetcher._fetch_html_with_browser")
    @patch("michelin_scraper.sources.taiwan_michelin.requests.post")
    @patch("requests.Session.get")
    def test_challenge_uses_fresh_original_html_without_browser(self, direct, post, browser) -> None:
        direct.return_value = self._response(202, "")
        post.return_value = Mock(status_code=200, json=Mock(return_value=self._payload()))
        with _FreshReaderSession() as session:
            response = session.get(SOURCE_URL)
            self.assertEqual(response.text, self._payload()["data"]["rawHtml"])
            self.assertEqual(session.provenance[0]["source_status_code"], 200)
        post.assert_called_once()
        self.assertEqual(post.call_args.args, (READER_URL,))
        self.assertEqual(post.call_args.kwargs["json"], {"url": SOURCE_URL, "formats": ["rawHtml"], "maxAge": 0})
        browser.assert_not_called()

    @patch("michelin_scraper.sources.taiwan_michelin.requests.post")
    @patch("requests.Session.get")
    def test_vendor_failure_and_wrong_original_provenance_fail_closed(self, direct, post) -> None:
        direct.return_value = self._response(202, "")
        mutations = [lambda p: p.update(success=False), lambda p: p["data"].update(rawHtml=""),
                     lambda p: p["data"]["metadata"].update(statusCode=202),
                     lambda p: p["data"]["metadata"].update(sourceURL=SOURCE_URL + "/page/2"),
                     lambda p: p["data"]["metadata"].update(url=READER_URL)]
        for mutate in mutations:
            payload = self._payload()
            mutate(payload)
            post.return_value = Mock(status_code=200, json=Mock(return_value=payload))
            with self.subTest(payload=payload["data"]["metadata"]), _FreshReaderSession() as session, self.assertRaises(ValueError):
                session.get(SOURCE_URL)
        post.return_value = Mock(status_code=429, headers={"Retry-After": "82590"})
        with _FreshReaderSession() as session, self.assertRaisesRegex(ValueError, "HTTP 429.*retry_after=82590"):
            session.get(SOURCE_URL)
        post.side_effect = requests.HTTPError("vendor unavailable")
        with _FreshReaderSession() as session, self.assertRaisesRegex(ValueError, "reader request failed"):
            session.get(SOURCE_URL)

    @patch("michelin_scraper.sources.taiwan_michelin.requests.post")
    @patch("requests.Session.get")
    def test_foreign_url_or_expired_budget_makes_no_network_request(self, direct, post) -> None:
        with _FreshReaderSession() as session:
            with self.assertRaises(ValueError):
                session.get(READER_URL)
            session._deadline = 0
            with self.assertRaises(ValueError):
                session.get(SOURCE_URL)
        direct.assert_not_called()
        post.assert_not_called()


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
    def test_full_next_chain_is_required_and_http_only(self, scrape) -> None:
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
