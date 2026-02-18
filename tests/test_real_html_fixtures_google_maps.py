"""Offline tests using de-identified real Google Maps HTML fixtures."""

import json
import re
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from michelin_scraper.adapters import (
    google_maps_driver_list_flow as list_flow,
)
from michelin_scraper.adapters import (
    google_maps_driver_save_flow as save_flow,
)
from michelin_scraper.adapters import (
    google_maps_driver_search_flow as search_flow,
)
from michelin_scraper.adapters import (
    google_maps_driver_selectors as selectors,
)
from michelin_scraper.adapters.google_maps_driver import GoogleMapsDriver, GoogleMapsDriverConfig
from michelin_scraper.application.html_redaction import find_unredacted_sensitive_markers

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "google_maps"
_HASTEXT_PATTERN = re.compile(r"^(?P<base>.+):has-text\((?P<quote>['\"])(?P<text>.+)(?P=quote)\)$")


def _read_fixture_text(filename: str) -> str:
    return (_FIXTURE_DIR / filename).read_text(encoding="utf-8")


def _find_matches_with_playwright_like_has_text(soup: BeautifulSoup, selector: str) -> list[object]:
    if ":has(" in selector and ":has-text(" not in selector:
        return []
    maybe_match = _HASTEXT_PATTERN.match(selector)
    if maybe_match is None:
        try:
            return list(soup.select(selector))
        except Exception:  # noqa: BLE001
            return []
    base_selector = maybe_match.group("base")
    text_snippet = maybe_match.group("text")
    try:
        candidates = soup.select(base_selector)
    except Exception:  # noqa: BLE001
        return []
    return [
        candidate
        for candidate in candidates
        if text_snippet.casefold() in candidate.get_text(" ", strip=True).casefold()
    ]


def _assert_any_selector_matches(soup: BeautifulSoup, selector_group: tuple[str, ...]) -> None:
    matched = any(_find_matches_with_playwright_like_has_text(soup, selector) for selector in selector_group)
    if not matched:
        raise AssertionError(f"No selector matched in fixture. selectors={selector_group}")


def _any_selector_matches(soup: BeautifulSoup, selector_group: tuple[str, ...]) -> bool:
    return any(_find_matches_with_playwright_like_has_text(soup, selector) for selector in selector_group)


class _StaticHtmlPage:
    def __init__(self, html_text: str) -> None:
        self._html_text = html_text

    def is_closed(self) -> bool:
        return False

    async def content(self) -> str:
        return self._html_text


class _StaticContext:
    def __init__(self, page: _StaticHtmlPage) -> None:
        self.pages = [page]

    async def new_page(self) -> _StaticHtmlPage:
        return self.pages[0]

    async def cookies(self, urls: list[str]) -> list[dict[str, str]]:
        del urls
        return []


class RealHtmlGoogleMapsFixtureTests(unittest.IsolatedAsyncioTestCase):
    def test_fixture_metadata_marks_snapshots_as_sanitized(self) -> None:
        metadata_files = sorted(_FIXTURE_DIR.glob("*.metadata.json"))
        self.assertGreater(len(metadata_files), 0)
        for metadata_file in metadata_files:
            payload = json.loads(metadata_file.read_text(encoding="utf-8"))
            self.assertTrue(payload.get("sanitized"), f"Fixture metadata not sanitized: {metadata_file.name}")
            self.assertEqual(payload.get("source"), "real-page-snapshot")

    def test_search_surface_fixture_matches_driver_selector_groups(self) -> None:
        soup = BeautifulSoup(_read_fixture_text("search-surface.html"), "html.parser")
        _assert_any_selector_matches(soup, selectors.SEARCH_BOX_SELECTORS)
        _assert_any_selector_matches(soup, selectors.FIRST_RESULT_SELECTORS)
        _assert_any_selector_matches(soup, selectors.PLACE_TITLE_SELECTORS)
        _assert_any_selector_matches(soup, selectors.PLACE_ADDRESS_SELECTORS)
        _assert_any_selector_matches(soup, selectors.PLACE_CATEGORY_SELECTORS)
        _assert_any_selector_matches(soup, save_flow.SAVE_BUTTON_SELECTORS)

    def test_save_surface_fixture_matches_driver_selector_groups(self) -> None:
        soup = BeautifulSoup(_read_fixture_text("save-surface.html"), "html.parser")
        _assert_any_selector_matches(soup, list_flow.SAVED_TAB_SELECTORS)
        _assert_any_selector_matches(soup, list_flow.LISTS_TAB_SELECTORS)
        _assert_any_selector_matches(soup, list_flow.NEW_LIST_BUTTON_SELECTORS)
        _assert_any_selector_matches(soup, list_flow.LIST_CREATION_ENTRY_SELECTORS)
        _assert_any_selector_matches(soup, list_flow.LIST_NAME_INPUT_SELECTORS)
        _assert_any_selector_matches(soup, list_flow.CREATE_LIST_BUTTON_SELECTORS)
        _assert_any_selector_matches(soup, save_flow.SAVE_DIALOG_NOTE_FIELD_SELECTORS)
        _assert_any_selector_matches(soup, save_flow.SAVE_DIALOG_INTERACTIVE_SELECTORS)

    def test_note_write_failure_surface_fixture_does_not_look_like_save_surface(self) -> None:
        soup = BeautifulSoup(_read_fixture_text("note-write-failure-surface.html"), "html.parser")
        _assert_any_selector_matches(soup, selectors.SEARCH_BOX_SELECTORS)
        _assert_any_selector_matches(soup, selectors.PLACE_TITLE_SELECTORS)
        _assert_any_selector_matches(soup, save_flow.SAVE_BUTTON_SELECTORS)
        self.assertFalse(_any_selector_matches(soup, save_flow.SAVE_DIALOG_INTERACTIVE_SELECTORS))
        self.assertFalse(_any_selector_matches(soup, save_flow.SAVE_DIALOG_NOTE_FIELD_SELECTORS))

    def test_note_write_main_panel_surface_matches_note_editor_selectors(self) -> None:
        soup = BeautifulSoup(_read_fixture_text("note-write-main-panel-surface.html"), "html.parser")
        _assert_any_selector_matches(soup, selectors.SEARCH_BOX_SELECTORS)
        _assert_any_selector_matches(soup, selectors.PLACE_TITLE_SELECTORS)
        _assert_any_selector_matches(soup, save_flow.SAVE_BUTTON_SELECTORS)
        _assert_any_selector_matches(soup, save_flow.SAVE_PANEL_SAVED_STATE_SELECTORS)
        _assert_any_selector_matches(soup, save_flow.SAVE_DIALOG_NOTE_FIELD_SELECTORS)
        _assert_any_selector_matches(soup, save_flow.SAVE_PANEL_NOTE_EXPAND_SELECTORS)
        self.assertFalse(_any_selector_matches(soup, save_flow.SAVE_DIALOG_INTERACTIVE_SELECTORS))

    async def test_security_block_fixture_detects_login_block(self) -> None:
        driver = GoogleMapsDriver(
            GoogleMapsDriverConfig(
                user_data_dir=Path("/tmp/michelin-fixture-profile"),
                headless=True,
                sync_delay_seconds=0.1,
            )
        )
        blocked_page = _StaticHtmlPage(_read_fixture_text("security-block.html"))
        normal_page = _StaticHtmlPage(_read_fixture_text("search-surface.html"))
        context = _StaticContext(blocked_page)
        driver._context = context
        driver._page = blocked_page
        self.assertTrue(await driver._has_login_security_block())
        context.pages = [normal_page]
        driver._page = normal_page
        self.assertFalse(await driver._has_login_security_block())

    def test_fixture_html_has_no_unredacted_sensitive_markers(self) -> None:
        for html_file in sorted(_FIXTURE_DIR.glob("*.html")):
            html_text = html_file.read_text(encoding="utf-8")
            markers = find_unredacted_sensitive_markers(html_text)
            self.assertEqual(markers, (), f"Sensitive markers remained in fixture {html_file.name}: {markers}")

    def test_search_outcome_tokens_cover_fixture_no_results_phrase(self) -> None:
        security_html = _read_fixture_text("security-block.html").casefold()
        tokens = tuple(token.casefold() for token in search_flow.NO_RESULTS_TEXT_TOKENS)
        self.assertFalse(any(token in security_html for token in tokens))


if __name__ == "__main__":
    unittest.main()
