"""Contract tests for Google Maps driver core behavior."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from michelin_scraper.adapters.google_maps_driver import (
    GoogleMapsDriver,
    GoogleMapsDriverConfig,
    GoogleMapsNoteWriteError,
    GoogleMapsPlaceAlreadySavedError,
    GoogleMapsTransientError,
    _build_launch_options,
    _prepare_user_data_dir_for_automation,
    _sandbox_candidates_for_channel,
    _SearchPanelState,
)
from tests.fakes import FakeContext, FakePage


class _RaiseKeyboardInterruptContext:
    async def close(self) -> None:
        raise KeyboardInterrupt()


class _RaiseRuntimeErrorContext:
    async def close(self) -> None:
        raise RuntimeError("close failed")


class _NoopPlaywright:
    async def stop(self) -> None:
        return


class _BrokenContentPage:
    def is_closed(self) -> bool:
        return False

    async def content(self) -> str:
        raise RuntimeError("content unavailable")


class GoogleMapsDriverContractTests(unittest.IsolatedAsyncioTestCase):
    def _build_driver(self) -> GoogleMapsDriver:
        return GoogleMapsDriver(
            GoogleMapsDriverConfig(
                user_data_dir=Path("/tmp/michelin-driver-contract"),
                headless=True,
                sync_delay_seconds=0.1,
            )
        )

    def test_sandbox_candidates_for_chrome_on_macos(self) -> None:
        with patch("michelin_scraper.adapters.google_maps_driver.sys.platform", "darwin"):
            self.assertEqual(_sandbox_candidates_for_channel("chrome"), (True,))
            self.assertEqual(_sandbox_candidates_for_channel(None), (True, False))

    def test_resolve_search_outcome_timeout_uses_default_floor(self) -> None:
        driver = self._build_driver()

        timeout_ms = driver._resolve_search_outcome_timeout_ms()

        self.assertEqual(timeout_ms, driver._SEARCH_OUTCOME_TIMEOUT_MS)

    def test_resolve_search_outcome_timeout_caps_scaled_timeout(self) -> None:
        driver = GoogleMapsDriver(
            GoogleMapsDriverConfig(
                user_data_dir=Path("/tmp/michelin-driver-contract-slow"),
                headless=True,
                sync_delay_seconds=10.0,
            )
        )

        timeout_ms = driver._resolve_search_outcome_timeout_ms()

        self.assertEqual(timeout_ms, driver._SEARCH_OUTCOME_MAX_TIMEOUT_MS)

    async def test_wait_for_search_outcome_accepts_stable_visible_result_after_loading(self) -> None:
        driver = GoogleMapsDriver(
            GoogleMapsDriverConfig(
                user_data_dir=Path("/tmp/michelin-driver-contract-fast"),
                headless=True,
                sync_delay_seconds=0.0,
            )
        )
        driver._SEARCH_OUTCOME_MIN_SETTLE_MS = 0
        previous_state = _SearchPanelState(
            page_url="https://www.google.com/maps/search/foo",
            title_text="",
            first_result_signature="same-result",
            no_results_visible=False,
            loading_visible=False,
        )
        loading_state = _SearchPanelState(
            page_url="https://www.google.com/maps/search/foo",
            title_text="",
            first_result_signature="same-result",
            no_results_visible=False,
            loading_visible=True,
        )
        current_state = _SearchPanelState(
            page_url="https://www.google.com/maps/search/foo",
            title_text="",
            first_result_signature="same-result",
            no_results_visible=False,
            loading_visible=False,
        )

        with patch.object(
            driver,
            "_capture_search_panel_state",
            side_effect=[loading_state, current_state],
        ):
            with patch.object(driver, "_wait_for_timeout") as wait_for_timeout:
                outcome = await driver._wait_for_search_outcome(
                    page=object(),
                    query="foo",
                    previous_state=previous_state,
                )

        self.assertEqual(outcome, driver._SEARCH_OUTCOME_RESULT_READY)
        wait_for_timeout.assert_called_once()

    async def test_wait_for_search_outcome_does_not_accept_stale_previous_place_details(self) -> None:
        driver = GoogleMapsDriver(
            GoogleMapsDriverConfig(
                user_data_dir=Path("/tmp/michelin-driver-contract-stale-place"),
                headless=True,
                sync_delay_seconds=0.0,
            )
        )
        driver._SEARCH_OUTCOME_MIN_SETTLE_MS = 0
        previous_state = _SearchPanelState(
            page_url="https://www.google.com/maps/place/ya-ge",
            title_text="Ya Ge",
            first_result_signature="",
            no_results_visible=False,
            loading_visible=True,
        )
        stale_state = _SearchPanelState(
            page_url="https://www.google.com/maps/place/ya-ge",
            title_text="Ya Ge",
            first_result_signature="",
            no_results_visible=False,
            loading_visible=False,
        )
        fresh_state = _SearchPanelState(
            page_url="https://www.google.com/maps/place/impromptu",
            title_text="Impromptu by Paul Lee",
            first_result_signature="",
            no_results_visible=False,
            loading_visible=False,
        )

        with patch.object(
            driver,
            "_capture_search_panel_state",
            side_effect=[stale_state, fresh_state],
        ):
            with patch.object(driver, "_wait_for_timeout") as wait_for_timeout:
                outcome = await driver._wait_for_search_outcome(
                    page=object(),
                    query="Impromptu by Paul Lee Taipei, 臺灣",
                    previous_state=previous_state,
                )

        self.assertEqual(outcome, driver._SEARCH_OUTCOME_RESULT_READY)
        wait_for_timeout.assert_called_once()

    def test_build_launch_options_includes_automation_suppression_when_enabled(self) -> None:
        launch_options = _build_launch_options(
            user_data_dir=Path("/tmp/profile"),
            headless=False,
            suppress_automation_flags=True,
            base_args=("--disable-session-crashed-bubble",),
        )

        self.assertEqual(launch_options["user_data_dir"], "/tmp/profile")
        self.assertEqual(launch_options["headless"], False)
        self.assertEqual(launch_options["ignore_default_args"], ["--enable-automation"])
        self.assertIn("--disable-blink-features=AutomationControlled", launch_options["args"])

    def test_prepare_user_data_dir_removes_transient_lock_artifacts(self) -> None:
        with patch("michelin_scraper.adapters.google_maps_driver.Path.unlink") as mock_unlink:
            with patch("michelin_scraper.adapters.google_maps_driver.Path.exists", return_value=True):
                with patch("michelin_scraper.adapters.google_maps_driver.Path.mkdir"):
                    _prepare_user_data_dir_for_automation(Path("/tmp/profile"))

        self.assertGreater(mock_unlink.call_count, 0)

    async def test_require_page_recovers_with_existing_open_page(self) -> None:
        driver = self._build_driver()
        open_page = FakePage(closed=False)
        context = FakeContext(
            pages=[FakePage(closed=True), open_page],
            cookies_payload=[],
        )
        driver._context = context
        driver._page = FakePage(closed=True)

        resolved = await driver._require_page()

        self.assertIs(resolved, open_page)
        self.assertEqual(context.created_page_count, 0)

    async def test_is_authenticated_uses_google_session_cookie_fallback(self) -> None:
        driver = self._build_driver()
        page = FakePage(closed=False)
        context = FakeContext(
            pages=[page],
            cookies_payload=[{"name": "SID", "value": "cookie-value"}],
        )
        driver._context = context
        driver._page = page

        with patch.object(driver, "_is_any_selector_visible", return_value=False):
            self.assertTrue(await driver.is_authenticated(refresh=False))

    async def test_security_block_detection_is_content_based(self) -> None:
        driver = self._build_driver()
        blocked_page = FakePage(
            content="This browser or app may not be secure. Couldn't sign you in."
        )
        normal_page = FakePage(content="Google Maps Home")
        context = FakeContext(
            pages=[blocked_page],
            cookies_payload=[],
        )
        driver._context = context
        driver._page = blocked_page

        self.assertTrue(await driver._has_login_security_block())
        driver._page = normal_page
        context.pages = [normal_page]
        self.assertFalse(await driver._has_login_security_block())

    async def test_close_ignores_keyboard_interrupt_errors_from_context(self) -> None:
        driver = self._build_driver()
        driver._context = _RaiseKeyboardInterruptContext()
        driver._playwright = _NoopPlaywright()

        await driver.close(force=False)

    async def test_close_raises_non_interrupt_errors_from_context(self) -> None:
        driver = self._build_driver()
        driver._context = _RaiseRuntimeErrorContext()
        driver._playwright = _NoopPlaywright()

        with self.assertRaisesRegex(RuntimeError, "close failed"):
            await driver.close(force=False)

    @patch("michelin_scraper.adapters.google_maps_driver._force_kill_browser_process")
    @patch("signal.signal")
    @patch("signal.getsignal")
    def test_install_interrupt_handler_forces_quit_on_second_sigint(
        self,
        mock_getsignal,
        mock_signal,
        mock_force_kill_browser,
    ) -> None:
        import signal

        driver = self._build_driver()
        driver._context = object()
        original_handler = object()
        mock_getsignal.return_value = original_handler
        captured_handler: dict[str, object] = {}

        def _capture_signal_handler(_sig: int, handler: object) -> None:
            captured_handler["handler"] = handler

        mock_signal.side_effect = _capture_signal_handler

        driver._install_interrupt_handler()

        installed_handler = captured_handler.get("handler")
        self.assertIsNotNone(installed_handler)
        self.assertTrue(callable(installed_handler))

        installed_handler(signal.SIGINT, None)
        self.assertTrue(driver._interrupt_requested)

        with self.assertRaises(KeyboardInterrupt):
            installed_handler(signal.SIGINT, None)
        mock_force_kill_browser.assert_called_once_with(driver._context)
        self.assertTrue(
            any(
                call_args.args[0] == signal.SIGINT
                and call_args.args[1] == signal.default_int_handler
                for call_args in mock_signal.call_args_list
            )
        )

        driver._uninstall_interrupt_handler()
        self.assertFalse(driver._interrupt_requested)
        self.assertTrue(
            any(
                call_args.args[0] == signal.SIGINT and call_args.args[1] is original_handler
                for call_args in mock_signal.call_args_list
            )
        )

    async def test_dump_page_html_uses_current_page_when_context_is_unavailable(self) -> None:
        driver = self._build_driver()
        driver._context = None
        driver._page = FakePage(closed=False, content="<html><body>snapshot</body></html>")

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "snapshot.html"

            dumped = await driver.dump_page_html(output_path)

            self.assertTrue(dumped)
            self.assertIn("snapshot", output_path.read_text(encoding="utf-8"))

    async def test_dump_page_html_falls_back_to_context_pages_when_primary_page_content_fails(self) -> None:
        driver = self._build_driver()
        fallback_page = FakePage(closed=False, content="<html><body>fallback</body></html>")
        context = FakeContext(
            pages=[fallback_page],
            cookies_payload=[],
        )
        driver._context = context
        driver._page = _BrokenContentPage()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "snapshot.html"

            dumped = await driver.dump_page_html(output_path)

            self.assertTrue(dumped)
            self.assertIn("fallback", output_path.read_text(encoding="utf-8"))
            self.assertEqual(context.created_page_count, 0)

    async def test_dump_page_html_uses_cached_snapshot_when_live_page_is_unavailable(self) -> None:
        driver = self._build_driver()
        driver._context = None
        driver._page = _BrokenContentPage()
        driver._last_page_html_snapshot = "<html><body>cached</body></html>"

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "snapshot.html"

            dumped = await driver.dump_page_html(output_path)

            self.assertTrue(dumped)
            self.assertIn("cached", output_path.read_text(encoding="utf-8"))

    async def test_save_current_place_to_list_raises_already_saved_without_opening_dialog(self) -> None:
        driver = self._build_driver()
        page = object()

        with patch.object(driver, "_require_page", return_value=page):
            with patch.object(driver, "_is_add_place_input_visible", return_value=False):
                with patch.object(driver, "_resolve_panel_saved_list_name", return_value="Test List"):
                    with patch.object(driver, "_click_save_control_with_retry") as click_save:
                        with self.assertRaises(GoogleMapsPlaceAlreadySavedError):
                            await driver.save_current_place_to_list("Test List")

        click_save.assert_not_called()

    async def test_save_current_place_to_list_proceeds_when_saved_to_different_list(self) -> None:
        driver = self._build_driver()
        page = object()

        with patch.object(driver, "_require_page", return_value=page):
            with patch.object(driver, "_is_add_place_input_visible", return_value=False):
                with patch.object(driver, "_resolve_panel_saved_list_name", return_value="Other List"):
                    with patch.object(driver, "_click_save_control_with_retry", return_value="save-btn") as click_save:
                        with patch.object(driver, "_wait_for_save_dialog_list_selector", return_value=None):
                            with patch.object(driver, "_wait_for_save_dialog_open", return_value=False):
                                with patch.object(driver, "_summarize_search_controls_for_debug", return_value=""):
                                    with patch.object(driver, "_summarize_browser_state_for_debug", return_value=""):
                                        with self.assertRaises(GoogleMapsTransientError):
                                            await driver.save_current_place_to_list("Target List")

        # Must proceed to save flow (click save control) instead of raising AlreadySavedError.
        click_save.assert_called()

    async def test_save_current_place_to_list_applies_note_from_panel_saved_state_without_dialog(self) -> None:
        driver = self._build_driver()
        page = object()

        with patch.object(driver, "_require_page", return_value=page):
            with patch.object(driver, "_is_add_place_input_visible", return_value=False):
                with patch.object(driver, "_resolve_panel_saved_list_name", return_value="Test List"):
                    with patch.object(driver, "_first_visible_locator", return_value=object()):
                        with patch.object(driver, "_try_apply_note_to_saved_place", return_value=True):
                            with patch.object(driver, "_wait_for_note_text_applied", return_value=True):
                                with patch.object(driver, "_click_save_control_with_retry") as click_save:
                                    saved = await driver.save_current_place_to_list(
                                        "Test List",
                                        note_text="hello note",
                                    )

        self.assertTrue(saved)
        click_save.assert_not_called()

    async def test_save_current_place_to_list_retries_panel_expand_when_note_field_missing(self) -> None:
        driver = self._build_driver()
        page = object()

        with patch.object(driver, "_require_page", return_value=page):
            with patch.object(driver, "_is_add_place_input_visible", return_value=False):
                with patch.object(driver, "_resolve_panel_saved_list_name", return_value="Test List"):
                    with patch.object(driver, "_first_visible_locator", return_value=None):
                        with patch.object(
                            driver,
                            "_attempt_expand_place_panel_note_editor",
                            return_value=True,
                        ) as expand_panel:
                            with patch.object(driver, "_try_apply_note_to_saved_place", return_value=False):
                                with self.assertRaises(GoogleMapsNoteWriteError):
                                    await driver.save_current_place_to_list(
                                        "Test List",
                                        note_text="hello note",
                                    )

        # One initial expand + one per write attempt while the note field is still missing.
        self.assertGreaterEqual(expand_panel.call_count, 3)

    async def test_ensure_save_dialog_ready_for_note_returns_true_when_note_field_is_visible(self) -> None:
        driver = self._build_driver()
        page = object()

        with patch.object(driver, "_first_visible_locator", return_value=object()):
            with patch.object(driver, "_wait_for_save_surface_settled_for_note") as settle_waiter:
                with patch.object(driver, "_wait_for_loader_disappear_if_seen") as loader_waiter:
                    ready = await driver._ensure_save_dialog_ready_for_note(
                        page=page,
                        list_name="Test List",
                    )

        self.assertTrue(ready)
        settle_waiter.assert_not_called()
        loader_waiter.assert_not_called()

    async def test_ensure_save_dialog_ready_for_note_waits_for_settle_and_loader(self) -> None:
        driver = self._build_driver()
        page = object()
        list_selector = object()

        with patch.object(driver, "_first_visible_locator", side_effect=[None, None]):
            with patch.object(driver, "_is_save_dialog_visible", return_value=True):
                with patch.object(
                    driver,
                    "_wait_for_save_surface_settled_for_note",
                    return_value=True,
                ) as settle_waiter:
                    with patch.object(
                        driver,
                        "_wait_for_loader_disappear_if_seen",
                        return_value=True,
                    ) as loader_waiter:
                        with patch.object(
                            driver,
                            "_resolve_save_dialog_list_selector",
                            return_value=list_selector,
                        ):
                            with patch.object(
                                driver,
                                "_wait_for_note_editor_visible",
                                return_value=True,
                            ) as note_editor_waiter:
                                ready = await driver._ensure_save_dialog_ready_for_note(
                                    page=page,
                                    list_name="Test List",
                                )

        self.assertTrue(ready)
        settle_waiter.assert_called_once_with(page=page, list_name="Test List")
        loader_waiter.assert_called_once_with(page=page, list_name="Test List")
        note_editor_waiter.assert_called_once_with(
            page=page,
            list_name="Test List",
            list_selector=list_selector,
        )

    async def test_wait_for_save_surface_settled_for_note_returns_true_when_ready_immediately(self) -> None:
        driver = self._build_driver()
        page = object()

        with patch.object(
            driver,
            "_is_save_surface_ready_for_note",
            return_value=True,
        ):
            settled = await driver._wait_for_save_surface_settled_for_note(
                page=page,
                list_name="Test List",
            )

        self.assertTrue(settled)

    async def test_wait_for_save_surface_settled_for_note_clicks_save_when_not_ready(self) -> None:
        driver = self._build_driver()
        page = object()

        with patch.object(
            driver,
            "_is_save_surface_ready_for_note",
            side_effect=[False, True],
        ):
            with patch.object(driver, "_supports_dom_waits", return_value=True):
                with patch.object(driver, "_wait_for_condition", side_effect=[False, True]):
                    with patch.object(driver, "_click_save_control_with_retry") as click_save:
                        with patch.object(driver, "_wait_for_timeout"):
                            settled = await driver._wait_for_save_surface_settled_for_note(
                                page=page,
                                list_name="Test List",
                            )

        self.assertTrue(settled)
        click_save.assert_called_once()

    async def test_wait_for_save_surface_settled_for_note_returns_false_when_surface_never_settles(self) -> None:
        driver = self._build_driver()
        page = object()

        with patch.object(
            driver,
            "_is_save_surface_ready_for_note",
            return_value=False,
        ):
            with patch.object(driver, "_supports_dom_waits", return_value=True):
                with patch.object(driver, "_wait_for_condition", return_value=False):
                    with patch.object(
                        driver,
                        "_click_save_control_with_retry",
                        side_effect=GoogleMapsTransientError("save click failed"),
                    ):
                        settled = await driver._wait_for_save_surface_settled_for_note(
                            page=page,
                            list_name="Test List",
                        )

        self.assertFalse(settled)

    async def test_wait_for_loader_disappear_if_seen_waits_until_loader_clears(self) -> None:
        driver = self._build_driver()
        page = object()

        with patch.object(driver, "_supports_dom_waits", return_value=True):
            with patch.object(driver, "_is_save_dialog_visible", return_value=True):
                with patch.object(
                    driver,
                    "_is_any_selector_visible",
                    side_effect=[True, False],
                ):
                    with patch.object(
                        driver,
                        "_wait_for_condition",
                        return_value=True,
                    ) as wait_for_condition:
                        with patch.object(driver, "_wait_for_timeout") as wait_for_timeout:
                            ready = await driver._wait_for_loader_disappear_if_seen(
                                page=page,
                                list_name="Test List",
                            )

        self.assertTrue(ready)
        wait_for_condition.assert_called_once()
        wait_for_timeout.assert_called_once()

    async def test_wait_for_loader_disappear_if_seen_returns_false_when_loader_never_clears(self) -> None:
        driver = self._build_driver()
        page = object()

        with patch.object(driver, "_supports_dom_waits", return_value=True):
            with patch.object(driver, "_is_save_dialog_visible", return_value=True):
                with patch.object(driver, "_is_any_selector_visible", return_value=True):
                    with patch.object(
                        driver,
                        "_wait_for_condition",
                        return_value=False,
                    ) as wait_for_condition:
                        ready = await driver._wait_for_loader_disappear_if_seen(
                            page=page,
                            list_name="Test List",
                        )

        self.assertFalse(ready)
        wait_for_condition.assert_called_once()

    async def test_wait_for_loader_disappear_if_seen_returns_true_when_note_field_appears(self) -> None:
        driver = self._build_driver()
        page = object()

        with patch.object(driver, "_supports_dom_waits", return_value=True):
            with patch.object(driver, "_is_save_dialog_visible", return_value=True):
                with patch.object(driver, "_is_any_selector_visible", return_value=False):
                    with patch.object(driver, "_wait_for_condition", return_value=True):
                        with patch.object(driver, "_first_visible_locator", return_value=object()):
                            ready = await driver._wait_for_loader_disappear_if_seen(
                                page=page,
                                list_name="Test List",
                            )

        self.assertTrue(ready)

    async def test_wait_for_note_editor_visible_expands_selected_row_when_editor_is_missing(self) -> None:
        driver = self._build_driver()
        page = object()
        list_selector = object()

        with patch.object(driver, "_first_visible_locator", return_value=None):
            with patch.object(driver, "_supports_dom_waits", return_value=True):
                with patch.object(
                    driver,
                    "_wait_for_condition",
                    side_effect=[False, True],
                ) as waiter:
                    with patch.object(
                        driver,
                        "_attempt_expand_note_surface_for_note",
                        return_value=True,
                    ) as expand:
                        ready = await driver._wait_for_note_editor_visible(
                            page=page,
                            list_name="Test List",
                            list_selector=list_selector,
                        )

        self.assertTrue(ready)
        self.assertEqual(waiter.call_count, 2)
        expand.assert_called_once_with(
            page=page,
            list_name="Test List",
            list_selector=list_selector,
        )

    async def test_wait_for_note_editor_visible_uses_place_panel_expand_when_dialog_missing(self) -> None:
        driver = self._build_driver()
        page = object()

        with patch.object(driver, "_first_visible_locator", return_value=None):
            with patch.object(driver, "_supports_dom_waits", return_value=True):
                with patch.object(
                    driver,
                    "_wait_for_condition",
                    side_effect=[False, True],
                ) as wait_note_field:
                    with patch.object(
                        driver,
                        "_attempt_expand_note_surface_for_note",
                        return_value=True,
                    ) as expand_surface:
                        ready = await driver._wait_for_note_editor_visible(
                            page=page,
                            list_name="Test List",
                            list_selector=None,
                        )

        self.assertTrue(ready)
        self.assertEqual(wait_note_field.call_count, 2)
        expand_surface.assert_called_once_with(
            page=page,
            list_name="Test List",
            list_selector=None,
        )

    async def test_attempt_expand_note_surface_for_note_uses_place_panel_when_dialog_missing(self) -> None:
        driver = self._build_driver()
        page = object()
        list_selector = object()

        with patch.object(driver, "_is_save_dialog_visible", return_value=False):
            with patch.object(
                driver,
                "_attempt_expand_place_panel_note_editor",
                return_value=True,
            ) as expand_panel:
                with patch.object(
                    driver,
                    "_attempt_expand_selected_list_entry_for_note",
                ) as expand_selected:
                    expanded = await driver._attempt_expand_note_surface_for_note(
                        page=page,
                        list_name="Test List",
                        list_selector=list_selector,
                    )

        self.assertTrue(expanded)
        expand_panel.assert_called_once_with(page=page, list_name="Test List")
        expand_selected.assert_not_called()

    async def test_attempt_expand_place_panel_note_editor_clicks_expand_control(self) -> None:
        driver = self._build_driver()
        page = object()
        expand_control = object()

        with patch.object(driver, "_first_visible_locator", return_value=expand_control):
            with patch.object(driver, "_click_locator") as click_locator:
                with patch.object(driver, "_wait_for_timeout") as wait_timeout:
                    expanded = await driver._attempt_expand_place_panel_note_editor(
                        page=page,
                        list_name="Test List",
                    )

        self.assertTrue(expanded)
        click_locator.assert_called_once()
        wait_timeout.assert_called_once()

    async def test_try_apply_note_to_saved_place_prefers_locator_fill_before_dom_script(self) -> None:
        driver = self._build_driver()
        note_input = object()
        keyboard_mock = AsyncMock()
        page = MagicMock()
        page.keyboard = keyboard_mock

        with patch.object(driver, "_first_visible_locator", return_value=note_input):
            with patch.object(driver, "_click_locator") as click_locator:
                with patch.object(driver, "_press_keyboard") as press_keyboard:
                    with patch.object(driver, "_run_browser_step") as run_step:
                        with patch.object(driver, "_press_locator") as press_locator:
                            with patch.object(driver, "_wait_for_timeout"):
                                wrote = await driver._try_apply_note_to_saved_place(
                                    page=page,
                                    list_name="Test List",
                                    note_text="Chef's counter",
                                )

        self.assertTrue(wrote)
        click_locator.assert_called_once()
        press_keyboard.assert_called_once()
        # run_browser_step is called for keyboard.type()
        run_step.assert_called_once()
        press_locator.assert_called_once()


if __name__ == "__main__":
    unittest.main()
