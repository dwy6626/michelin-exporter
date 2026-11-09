"""Probe the complete current Taiwan Michelin selection over HTTP only."""

import argparse
import hashlib
import json
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from ..config import HEADERS
from ..scraping.listing_page import scrape_results_single_page
from ..scraping.models import NullProgressReporter

READER_URL = "https://api.firecrawl.dev/v2/scrape"
SOURCE_BUDGET_SECONDS = 850
READER_TIMEOUT_SECONDS = 75
READER_INTERVAL_SECONDS = 0.25

SOURCE_ID = "taiwan-michelin"
SOURCE_URL = "https://guide.michelin.com/tw/zh_TW/selection/taiwan-region/restaurants"
LEVEL_BY_RATING = {"1 Star": "stars", "2 Stars": "stars", "3 Stars": "stars",
                   "Bib Gourmand": "bib-gourmand", "Selected": "selected"}
SEMANTIC_FIELDS = ("Name", "Rating", "Address", "City", "GuideYear")


class _FreshReaderSession(requests.Session):
    """HTTP first, then the configured vendor's fresh original HTML, never a browser."""

    def __init__(self) -> None:
        super().__init__()
        self.provenance: list[dict[str, Any]] = []
        self._deadline = time.monotonic() + SOURCE_BUDGET_SECONDS
        self._reader_lock = threading.Lock()
        self._next_reader_at = 0.0

    def _remaining(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Michelin source exceeded its bounded fresh-reader budget.")
        return remaining

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.netloc != "guide.michelin.com"
                or not parsed.path.startswith("/tw/zh_TW/") or parsed.query or parsed.fragment):
            raise ValueError("Fresh Michelin reader refuses a URL outside the official Taiwan scope.")
        kwargs["timeout"] = min(float(kwargs.get("timeout", 30)), self._remaining())
        direct_status = None
        try:
            response = super().get(url, **kwargs)
            direct_status = response.status_code
            if (response.status_code == 200 and response.text.strip()
                    and response.headers.get("x-amzn-waf-action", "").casefold() != "challenge"):
                self.provenance.append({"url": url, "transport": "direct-http", "status_code": 200,
                                        "html_sha256": hashlib.sha256(response.content).hexdigest()})
                return response
        except requests.RequestException:
            pass
        # The production parser already bounds detail concurrency to four. Space
        # vendor request starts as well; no retries hide a vendor/source failure.
        with self._reader_lock:
            delay = max(0.0, self._next_reader_at - time.monotonic())
            if delay >= self._remaining():
                raise ValueError("Michelin reader rate wait exceeds the source budget.")
            time.sleep(delay)
            self._next_reader_at = time.monotonic() + READER_INTERVAL_SECONDS
        try:
            result = requests.post(
                READER_URL, json={"url": url, "formats": ["rawHtml"], "maxAge": 0},
                timeout=min(READER_TIMEOUT_SECONDS, self._remaining()),
            )
        except requests.RequestException as exc:
            raise ValueError(f"Michelin fresh reader request failed at {url}: {type(exc).__name__}.") from exc
        if result.status_code != 200:
            raise ValueError(
                f"Michelin fresh reader HTTP {result.status_code} at {url}; "
                f"retry_after={result.headers.get('Retry-After', 'unspecified')}."
            )
        try:
            payload = result.json()
        except ValueError as exc:
            raise ValueError(f"Michelin fresh reader returned invalid JSON at {url}.") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        metadata = data.get("metadata") if isinstance(data, dict) else None
        raw_html = data.get("rawHtml") if isinstance(data, dict) else None
        if (result.status_code != 200 or not isinstance(payload, dict)
                or payload.get("success") is not True or not isinstance(metadata, dict)
                or type(metadata.get("statusCode")) is not int or metadata["statusCode"] != 200
                or metadata.get("sourceURL") != url or metadata.get("url") != url
                or metadata.get("error") or not isinstance(raw_html, str) or not raw_html.strip()):
            raise ValueError(f"Fresh Michelin reader returned invalid original-source provenance at {url}.")
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.encoding = "utf-8"
        response._content = raw_html.encode("utf-8")
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        self.provenance.append({
            "url": url, "transport": "firecrawl-v2-scrape", "reader_url": READER_URL,
            "direct_status_code": direct_status, "reader_status_code": result.status_code,
            "source_status_code": metadata["statusCode"], "source_url": metadata["sourceURL"],
            "resolved_url": metadata["url"], "max_age": 0, "scrape_id": metadata.get("scrapeId"),
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
    """Write source-only evidence; this never changes applied or browser state."""

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
