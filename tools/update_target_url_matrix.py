"""Update live-validated Michelin target URL matrix entries."""

import argparse
import json
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from michelin_scraper.catalog.targets import COUNTRY_CODES, LANGUAGE_SITE_CODES, resolve_language
from michelin_scraper.config import HEADERS

DEFAULT_MATRIX_PATH = Path("michelin_scraper/catalog/target_url_matrix.json")
SUPPORTED_STATUS = "supported"
UNSUPPORTED_STATUS = "unsupported-or-undiscovered"

ALTERNATE_SELECTION_SLUGS = {
    "china-mainland": ("chinese-mainland", "mainland-china", "china"),
    "macau": ("macao",),
    "ireland": ("republic-of-ireland",),
    "czechia": ("czech-republic",),
}

BAD_BODY_MARKERS = (
    "JavaScript is disabled",
    "ERROR: The request could not be satisfied",
)
NO_RESTAURANTS_MARKERS = (
    "沒有精選餐廳",
    "No selected restaurants",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default="zh-tw", help="Michelin language alias or segment, for example zh-tw.")
    parser.add_argument(
        "--country",
        action="append",
        dest="countries",
        help="Country slug to update. Can be passed multiple times. Defaults to every catalog country.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_MATRIX_PATH, help="Matrix JSON path to update.")
    parser.add_argument("--delay", type=float, default=0.3, help="Delay between Michelin requests in seconds.")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout per Michelin request in seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Print the computed matrix without writing it.")
    return parser.parse_args()


def load_matrix(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw_data = json.loads(path.read_text(encoding="utf-8"))
    return raw_data if isinstance(raw_data, dict) else {}


def write_matrix(path: Path, matrix: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def selection_candidate_urls(country_slug: str, *, site_code: str, language: str) -> tuple[tuple[str, str], ...]:
    candidate_slugs = (country_slug, *ALTERNATE_SELECTION_SLUGS.get(country_slug, ()))
    return tuple(
        (
            candidate_slug,
            f"https://guide.michelin.com/{site_code}/{language}/selection/{candidate_slug}/restaurants",
        )
        for candidate_slug in candidate_slugs
    )


def validate_listing_url(
    *,
    session: requests.Session,
    url: str,
    timeout: float,
) -> tuple[bool, str, str]:
    response = session.get(url, headers=HEADERS, timeout=timeout)
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    h1_tag = soup.find("h1")
    h1 = h1_tag.get_text(" ", strip=True) if h1_tag else ""
    body_excerpt = soup.get_text(" ", strip=True)[:500]

    if response.status_code != 200:
        return False, f"HTTP {response.status_code}", title
    if title.startswith("null MICHELIN Restaurants"):
        return False, "null Michelin listing title", title
    if any(marker in h1 for marker in NO_RESTAURANTS_MARKERS):
        return False, "no selected restaurants marker", title
    if any(marker in body_excerpt for marker in BAD_BODY_MARKERS):
        return False, "blocked/challenge page marker", title
    if not (title or h1):
        return False, "missing title and h1", title
    return True, h1 or title, title


def discover_country_entry(
    *,
    country_slug: str,
    site_code: str,
    language: str,
    session: requests.Session,
    timeout: float,
    delay: float,
) -> dict[str, str]:
    failures: list[str] = []
    for candidate_slug, url in selection_candidate_urls(country_slug, site_code=site_code, language=language):
        ok, detail, title = validate_listing_url(session=session, url=url, timeout=timeout)
        if ok:
            source = (
                "validated-selection-candidate"
                if candidate_slug == country_slug
                else "validated-alternate-selection-candidate"
            )
            return {
                "status": SUPPORTED_STATUS,
                "source": source,
                "url": url,
            }
        failures.append(f"{candidate_slug}: {detail} ({title})")
        time.sleep(delay)

    return {
        "status": UNSUPPORTED_STATUS,
        "source": "live-validation",
        "url": "",
        "reason": "No validated country selection URL candidate. " + "; ".join(failures),
    }


def resolve_countries(raw_countries: list[str] | None) -> tuple[str, ...]:
    if not raw_countries:
        return tuple(COUNTRY_CODES)
    invalid_countries = sorted(set(raw_countries) - set(COUNTRY_CODES))
    if invalid_countries:
        joined_invalid = ", ".join(invalid_countries)
        raise SystemExit(f"Unsupported country slug(s): {joined_invalid}")
    return tuple(raw_countries)


def main() -> None:
    args = parse_args()
    language = resolve_language(args.language)
    site_code = LANGUAGE_SITE_CODES.get(language)
    if not site_code:
        raise SystemExit(f"No known Michelin localized site code for language: {language}")

    matrix = load_matrix(args.output)
    language_matrix = matrix.setdefault(language, {})
    country_matrix = language_matrix.setdefault("countries", {})
    countries = resolve_countries(args.countries)

    with requests.Session() as session:
        for country_slug in countries:
            entry = discover_country_entry(
                country_slug=country_slug,
                site_code=site_code,
                language=language,
                session=session,
                timeout=args.timeout,
                delay=args.delay,
            )
            country_matrix[country_slug] = entry
            status = entry.get("status", "")
            url = entry.get("url", "")
            print(f"{country_slug}: {status} {url}")
            time.sleep(args.delay)

    if args.dry_run:
        print(json.dumps(matrix, ensure_ascii=False, indent=2, sort_keys=True))
        return
    write_matrix(args.output, matrix)


if __name__ == "__main__":
    main()
