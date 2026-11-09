"""Tests for Google Maps search-result freshness decisions."""

import json
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from michelin_scraper.adapters.google_maps_driver import GoogleMapsDriver, _SearchPanelState


class GoogleMapsSearchFreshnessTests(unittest.TestCase):
    def test_recorded_same_place_can_resolve_repeated_query_with_unchanged_title(self) -> None:
        path = Path(__file__).parent / "fixtures/500_awards/repeated-place-transition-2026-11-08.json"
        states = [_SearchPanelState(**state) for state in json.loads(path.read_text())["states"]]
        prior, current = next((left, right) for left, right in zip(states, states[1:], strict=False)
                              if left.title_text == right.title_text and right.title_text == "金山阿滿姨油飯蘿蔔糕"
                              and left.page_url != right.page_url and "/maps/place/" in right.page_url)
        self.assertTrue(GoogleMapsDriver._is_fresh_search_state(
            previous_state=prior, current_state=current, elapsed_ms=2000,
            settle_delay_ms=1200, observed_post_submit_loading=True,
        ))

    def test_recorded_query_url_change_does_not_accept_preceding_place_panel(self) -> None:
        fixtures = Path(__file__).parent / "fixtures"
        payload = json.loads((fixtures / "500_awards/search-transition-states-2026-11-08.json").read_text())
        states = [_SearchPanelState(**state) for state in payload["states"]]
        prior = next(state for state in states if state.title_text == "大碗油飯" and "/maps/place/" in state.page_url)
        stale = next(state for state in states if state.title_text == "大碗油飯" and "/maps/search/" in state.page_url
                     and not state.first_result_signature)
        html = BeautifulSoup((fixtures / "google_maps/search-stale-panel-2026-11-08.html").read_text(), "html.parser")
        self.assertEqual(html.select_one("h1.DUwDvf").get_text(strip=True), "大碗油飯")
        self.assertNotEqual(prior.page_url, stale.page_url)
        self.assertFalse(GoogleMapsDriver._is_fresh_search_state(
            previous_state=prior, current_state=stale, elapsed_ms=2000,
            settle_delay_ms=1200, observed_post_submit_loading=True,
        ))

    def test_stable_previous_place_is_not_fresh_for_different_query(self) -> None:
        previous = _SearchPanelState(
            page_url="https://www.google.com/maps/place/Previous",
            title_text="廟口鵝肉",
            first_result_signature="廟口鵝肉 no 36",
            no_results_visible=False,
            loading_visible=False,
        )
        current = _SearchPanelState(
            page_url="https://www.google.com/maps/place/Previous",
            title_text="廟口鵝肉",
            first_result_signature="廟口鵝肉 no 36",
            no_results_visible=False,
            loading_visible=False,
        )

        self.assertFalse(
            GoogleMapsDriver._is_fresh_search_state(
                previous_state=previous,
                current_state=current,
                elapsed_ms=2000,
                settle_delay_ms=1200,
                observed_post_submit_loading=True,
            )
        )

    def test_changed_result_signature_is_fresh(self) -> None:
        previous = _SearchPanelState(
            page_url="https://www.google.com/maps/search/foo",
            title_text="",
            first_result_signature="old",
            no_results_visible=False,
            loading_visible=False,
        )
        current = _SearchPanelState(
            page_url="https://www.google.com/maps/search/foo",
            title_text="",
            first_result_signature="new",
            no_results_visible=False,
            loading_visible=False,
        )

        self.assertTrue(
            GoogleMapsDriver._is_fresh_search_state(
                previous_state=previous,
                current_state=current,
                elapsed_ms=2000,
                settle_delay_ms=1200,
                observed_post_submit_loading=False,
            )
        )

    def test_changed_result_signature_is_fresh_even_while_loading_indicator_remains(self) -> None:
        previous = _SearchPanelState(
            page_url="https://www.google.com/maps/search/foo",
            title_text="",
            first_result_signature="old",
            no_results_visible=False,
            loading_visible=False,
        )
        current = _SearchPanelState(
            page_url="https://www.google.com/maps/search/foo",
            title_text="",
            first_result_signature="new",
            no_results_visible=False,
            loading_visible=True,
        )

        self.assertTrue(
            GoogleMapsDriver._is_fresh_search_state(
                previous_state=previous,
                current_state=current,
                elapsed_ms=2000,
                settle_delay_ms=1200,
                observed_post_submit_loading=True,
            )
        )

    def test_same_result_signature_is_not_fresh_while_loading_indicator_remains(self) -> None:
        previous = _SearchPanelState(
            page_url="https://www.google.com/maps/search/foo",
            title_text="",
            first_result_signature="same-result",
            no_results_visible=False,
            loading_visible=False,
        )
        current = _SearchPanelState(
            page_url="https://www.google.com/maps/search/foo",
            title_text="",
            first_result_signature="same-result",
            no_results_visible=False,
            loading_visible=True,
        )

        self.assertFalse(
            GoogleMapsDriver._is_fresh_search_state(
                previous_state=previous,
                current_state=current,
                elapsed_ms=2000,
                settle_delay_ms=1200,
                observed_post_submit_loading=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
