"""Probe complete Taiwan Michelin sources: HTTP first, then admitted cron source DOM."""

import argparse
import fcntl
import hashlib
import json
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from playwright.sync_api import Browser, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from ..config import HEADERS
from ..scraping.listing_page import scrape_results_single_page
from ..scraping.models import NullProgressReporter

SOURCE_BUDGET_SECONDS = 850
SOURCE_REQUEST_SECONDS = 45
SOURCE_INTERVAL_SECONDS = 1.0
SOURCE_JOB_ID = "44d6d0d58cbc"
SOURCE_CDP_URL = "http://127.0.0.1:9223"

SOURCE_ID = "taiwan-michelin"
SOURCE_URL = "https://guide.michelin.com/tw/zh_TW/selection/taiwan-region/restaurants"
LEVEL_BY_RATING = {"1 Star": "stars", "2 Stars": "stars", "3 Stars": "stars",
                   "Bib Gourmand": "bib-gourmand", "Selected": "selected"}
SEMANTIC_FIELDS = ("Name", "Rating", "Address", "City", "GuideYear")


class _FreshReaderSession(requests.Session):
    """HTTP first; one admitted, owned source page lives on one transport thread."""

    def __init__(self) -> None:
        super().__init__()
        self.provenance: list[dict[str, Any]] = []
        self._deadline = time.monotonic() + SOURCE_BUDGET_SECONDS
        self._owner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="michelin-source")
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._http_unavailable = False
        self._next_source_at = 0.0

    def _remaining(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Michelin source exceeded its bounded source-reader budget.")
        return remaining

    def _assert_admitted(self) -> None:
        home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
        token = os.environ.get("HERMES_CRON_BROWSER_LANE_TOKEN")
        with (home / "cron/locks/browser-cdp.lock").open() as lock:
            proof = json.load(lock)
            if not token or proof.get("token") != token or proof.get("job_id") != SOURCE_JOB_ID:
                raise ValueError("Michelin source requires its matching public job browser lease.")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            fcntl.flock(lock, fcntl.LOCK_UN)
            raise ValueError("Michelin source browser mutex is not held.")

    def _browser_get(self, url: str) -> tuple[str, str]:
        # This method and cleanup always execute in the single owner thread.
        self._assert_admitted()
        delay = max(0.0, self._next_source_at - time.monotonic())
        if delay >= self._remaining():
            raise ValueError("Michelin source pacing exceeds the source budget.")
        time.sleep(delay)
        self._next_source_at = time.monotonic() + SOURCE_INTERVAL_SECONDS
        timeout = max(1, int(min(SOURCE_REQUEST_SECONDS, self._remaining()) * 1000))
        if self._page is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(SOURCE_CDP_URL, timeout=timeout)
            if not self._browser.contexts:
                raise ValueError("Existing cron browser has no authorized context.")
            self._page = self._browser.contexts[0].new_page()
            self._page.set_viewport_size({"width": 1280, "height": 900})
        page = self._page
        # Normal same-origin fetch returns the original response HTML and avoids
        # repeatedly navigating the owned tab for the parser's detail requests.
        if page.url.startswith("https://guide.michelin.com/tw/zh_TW/"):
            try:
                result = page.evaluate("""async ({url, timeout}) => {
                    const response = await fetch(url, {signal: AbortSignal.timeout(timeout), cache: 'no-store'});
                    return {status: response.status, url: response.url, html: await response.text(),
                            challenge: response.headers.get('x-amzn-waf-action')};
                }""", {"url": url, "timeout": timeout})
                if (result["status"] == 200 and result["url"] == url and result["html"].strip()
                        and str(result["challenge"]).casefold() != "challenge"):
                    return result["html"], "cron-source-fetch"
            except PlaywrightError:
                pass
        timeout = max(1, int(min(SOURCE_REQUEST_SECONDS, self._remaining()) * 1000))
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        # A transient empty 202 is not restaurant HTML. Back off and perform
        # ordinary navigation again; never interact with challenges or cookies.
        for delay in (3, 6):
            if response is None or response.status != 202:
                break
            if delay >= self._remaining():
                raise ValueError("Michelin source navigation backoff exceeds the source budget.")
            page.wait_for_timeout(delay * 1000)
            timeout = max(1, int(min(SOURCE_REQUEST_SECONDS, self._remaining()) * 1000))
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        if (response is None or response.status != 200 or response.url != url or page.url != url
                or response.headers.get("x-amzn-waf-action", "").casefold() == "challenge"):
            raise ValueError(f"Michelin source navigation rejected at {url}: "
                             f"status={response.status if response else None}, resolved={page.url}.")
        html = page.content()
        if not html.strip():
            raise ValueError(f"Michelin source DOM is empty at {url}.")
        return html, "cron-source-dom"

    def _close_owned_page(self) -> None:
        try:
            if self._page is not None:
                self._page.close()
        finally:
            self._page = None
            # Stop this client's connection only; never close the browser/context.
            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None

    def close(self) -> None:
        try:
            self._owner.submit(self._close_owned_page).result()
        finally:
            self._owner.shutdown(wait=True, cancel_futures=True)
            super().close()

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.netloc != "guide.michelin.com"
                or not parsed.path.startswith("/tw/zh_TW/") or parsed.query or parsed.fragment):
            raise ValueError("Fresh Michelin reader refuses a URL outside the official Taiwan scope.")
        kwargs["timeout"] = min(float(kwargs.get("timeout", 30)), self._remaining())
        direct_status = None
        if not self._http_unavailable:
            try:
                response = super().get(url, **kwargs)
                direct_status = response.status_code
                if (response.status_code == 200 and response.url == url and response.text.strip()
                        and response.headers.get("x-amzn-waf-action", "").casefold() != "challenge"):
                    self.provenance.append({
                        "url": url, "transport": "direct-http", "status_code": 200, "resolved_url": response.url,
                        "checked_at_utc": datetime.now(UTC).isoformat(), "html_bytes": len(response.content),
                        "html_sha256": hashlib.sha256(response.content).hexdigest(),
                    })
                    return response
            except requests.RequestException:
                pass
            self._http_unavailable = True
        raw_html, transport = self._owner.submit(self._browser_get, url).result()
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.encoding = "utf-8"
        response._content = raw_html.encode("utf-8")
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        self.provenance.append({
            "url": url, "transport": transport, "direct_status_code": direct_status,
            "source_status_code": 200, "source_url": url, "resolved_url": url,
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "html_bytes": len(response.content), "html_sha256": hashlib.sha256(response.content).hexdigest(),
        })
        return response


class _ProbeReporter(NullProgressReporter):
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log(self, message: str) -> None:
        self.messages.append(message)


def semantic_fingerprint(rows: list[dict[str, Any]]) -> str:
    """Ignore links and ordering while retaining actual restaurant and award identity."""

    normalized = sorted(tuple(" ".join(str(row.get(key, "")).split()) for key in SEMANTIC_FIELDS) for row in rows)
    if not normalized or any(not all(row[:4]) for row in normalized) or len(set(normalized)) != len(normalized):
        raise ValueError("Michelin source rows are empty, incomplete or duplicate.")
    if any(row["Rating"] not in LEVEL_BY_RATING for row in rows):
        raise ValueError("Michelin source contains an unrecognized distinction.")
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _validate_listing_url(url: str, page: int) -> None:
    expected = SOURCE_URL if page == 1 else f"{SOURCE_URL}/page/{page}"
    if url != expected:
        raise ValueError(f"Unexpected Michelin next-page URL: {url!r}; expected {expected!r}.")


def discover_taiwan_michelin() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reuse production listing/detail parsing, requiring the entire next-link chain."""

    rows: list[dict[str, Any]] = []
    pages = []
    url: str | None = SOURCE_URL
    expected_total = None
    seen_urls: set[str] = set()
    reporter = _ProbeReporter()
    with _FreshReaderSession() as session:
        while url:
            page_number = len(pages) + 1
            if page_number > 100:
                raise ValueError("Michelin pagination exceeded the bounded Taiwan scope.")
            _validate_listing_url(url, page_number)
            result = scrape_results_single_page(
                session=session, url=url, headers=HEADERS, tls_verify=True,
                page_number=page_number, estimated_total_pages=None,
                total_restaurants_so_far=len(rows), progress_reporter=reporter,
                local_country_code="tw", requested_language="zh_TW", browser_fallback=False,
            )
            if result.fetch_failed or not result.restaurant_rows or not result.total_restaurants or result.total_restaurants <= 0:
                detail = "; ".join(reporter.messages[-3:])
                raise ValueError(f"Incomplete Michelin official source page {page_number} at {url}: {detail}")
            if expected_total is None:
                expected_total = result.total_restaurants
            if result.total_restaurants != expected_total:
                raise ValueError("Michelin source total changed during pagination; retry a fresh complete revision.")
            for row in result.restaurant_rows:
                detail_url = str(row.get("Michelin Website", ""))
                parsed = urlparse(detail_url)
                if (parsed.scheme != "https" or parsed.netloc != "guide.michelin.com"
                        or not parsed.path.startswith("/tw/zh_TW/") or "/restaurant/" not in parsed.path
                        or detail_url in seen_urls):
                    raise ValueError("Michelin detail identity is duplicate or outside the official Taiwan scope.")
                seen_urls.add(detail_url)
                if row.get("Rating") not in LEVEL_BY_RATING:
                    raise ValueError("Michelin source contains an unrecognized distinction.")
                row["level_slug"] = LEVEL_BY_RATING[row["Rating"]]
                rows.append(row)
            pages.append({"page": page_number, "url": url, "count": len(result.restaurant_rows)})
            if len(rows) > expected_total:
                raise ValueError("Michelin source exceeds its declared complete count.")
            url = result.next_url
    if len(rows) != expected_total:
        raise ValueError(f"Incomplete Michelin pagination: expected={expected_total}, actual={len(rows)}.")
    metadata = {
        "source_id": SOURCE_ID, "complete": True, "checked_at_utc": datetime.now(UTC).isoformat(),
        "source_url": SOURCE_URL, "source_urls": [item["url"] for item in pages], "pages": pages,
        "source_point_count": len(rows), "unique_source_rows": len(seen_urls),
        "bucket_counts": dict(Counter(row["level_slug"] for row in rows)),
        "guide_years": sorted({row.get("GuideYear", "") for row in rows}),
        "source_fingerprint": semantic_fingerprint(rows),
        "fetch_provenance": session.provenance,
    }
    return metadata, rows


def write_probe(state_dir: Path) -> dict[str, Any]:
    """Write source-only evidence without changing applied state or existing tabs."""

    metadata, rows = discover_taiwan_michelin()
    state_dir.mkdir(parents=True, exist_ok=True)
    rows_path = state_dir.resolve() / "source-rows.jsonl"
    rows_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    metadata["rows_file"] = str(rows_path)
    metadata["rows_sha256"] = hashlib.sha256(rows_path.read_bytes()).hexdigest()
    (state_dir / "source-probe.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, help="Private scratch directory for fresh source-probe.json and source-rows.jsonl.")
    parser.epilog = "DOM fallback requires the matching public Michelin job lease on existing cron :9223; HTTP needs no lease."
    args = parser.parse_args()
    state_dir = Path(args.state_dir).expanduser()
    try:
        write_probe(state_dir)
    except Exception as exc:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "source-failure.json").write_text(json.dumps({
            "status": "source_failure", "source_id": SOURCE_ID, "source_url": SOURCE_URL,
            "checked_at_utc": datetime.now(UTC).isoformat(), "error": str(exc), "source_applied": False,
        }, ensure_ascii=False, indent=2) + "\n")
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
