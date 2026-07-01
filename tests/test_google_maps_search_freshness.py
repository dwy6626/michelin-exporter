"""Tests for Google Maps search-result freshness decisions."""

import unittest

from michelin_scraper.adapters.google_maps_driver import GoogleMapsDriver, _SearchPanelState


class GoogleMapsSearchFreshnessTests(unittest.TestCase):
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
