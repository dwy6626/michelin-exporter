"""Playwright-backed Google Maps UI driver."""

import asyncio
import re
import shutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Protocol, cast

from ..application.place_matcher import PlaceCandidate
from ..config import GOOGLE_MAPS_HOME_URL
from . import (
    google_maps_driver_list_flow as list_flow,
)
from . import (
    google_maps_driver_save_flow as save_flow,
)
from . import (
    google_maps_driver_search_flow as search_flow,
)
from . import (
    google_maps_driver_selectors as selectors,
)


class GoogleMapsError(RuntimeError):
    """Base class for Google Maps adapter failures."""


class GoogleMapsDependencyError(GoogleMapsError):
    """Raised when Playwright dependency is missing at runtime."""


class GoogleMapsAuthRequiredError(GoogleMapsError):
    """Raised when an authenticated Google session is required."""


class GoogleMapsLoginBlockedError(GoogleMapsError):
    """Raised when Google blocks sign-in due to browser security checks."""


class GoogleMapsListAlreadyExistsError(GoogleMapsError):
    """Raised when a required startup list already exists."""


class GoogleMapsListMissingDuringRunError(GoogleMapsError):
    """Raised when a target list disappears during an active run."""


class GoogleMapsSelectorError(GoogleMapsError):
    """Raised when required UI selectors cannot be resolved."""


class GoogleMapsTransientError(GoogleMapsError):
    """Raised for retriable navigation / timing problems."""


class GoogleMapsPlaceAlreadySavedError(GoogleMapsError):
    """Raised when the current place is already saved in the target list."""


class GoogleMapsNoteWriteError(GoogleMapsError):
    """Raised when note text is requested but cannot be written."""

    def __init__(
        self,
        message: str,
        *,
        place_saved: bool = False,
    ) -> None:
        self.place_saved = place_saved
        super().__init__(message)


class GoogleMapsDriverPort(Protocol):
    """Structural interface for the Google Maps browser driver.

    Consumed by ``GoogleMapsSyncWriter`` so that tests can inject a fake
    driver without launching Playwright.
    """

    async def start(self) -> None: ...
    async def open_maps_home(self) -> None: ...
    async def is_authenticated(self, *, refresh: bool = True) -> bool: ...
    async def list_exists(self, list_name: str) -> bool: ...
    async def create_list(self, list_name: str) -> None: ...
    async def open_list(self, list_name: str) -> bool: ...
    async def search_and_open_first_result(self, query: str) -> PlaceCandidate | None: ...
    async def save_current_place_to_list(self, list_name: str, note_text: str = "") -> bool: ...
    async def close(self, *, force: bool = False) -> None: ...
    async def dump_page_html(self, path: Path) -> bool: ...


@dataclass(frozen=True)
class GoogleMapsDriverConfig:
    """Runtime config for Playwright persistent browser session."""

    user_data_dir: Path
    headless: bool
    sync_delay_seconds: float
    browser_channel: str | None = "chrome"
    suppress_automation_flags: bool = True


@dataclass(frozen=True)
class _SearchPanelState:
    page_url: str
    title_text: str
    first_result_signature: str
    no_results_visible: bool
    loading_visible: bool


class GoogleMapsDriver:
    """Thin Playwright wrapper for Google Maps operations."""

    _BROWSER_RECOVERY_SUPPRESSION_ARGS = (
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
    )
    _GOOGLE_SESSION_COOKIE_NAMES = selectors.GOOGLE_SESSION_COOKIE_NAMES
    _SEARCH_BOX_SELECTORS = selectors.SEARCH_BOX_SELECTORS
    _SEARCH_BUTTON_SELECTOR = selectors.SEARCH_BUTTON_SELECTOR
    _SIGNED_IN_ANCHOR_SELECTORS = selectors.SIGNED_IN_ANCHOR_SELECTORS
    _SIGN_IN_BUTTON_SELECTORS = selectors.SIGN_IN_BUTTON_SELECTORS
    _SAVED_TAB_SELECTORS = list_flow.SAVED_TAB_SELECTORS
    _LISTS_TAB_SELECTORS = list_flow.LISTS_TAB_SELECTORS
    _SAVED_VIEW_MORE_SELECTORS = list_flow.SAVED_VIEW_MORE_SELECTORS
    _NEW_LIST_BUTTON_SELECTORS = list_flow.NEW_LIST_BUTTON_SELECTORS
    _LIST_CREATION_ENTRY_SELECTORS = list_flow.LIST_CREATION_ENTRY_SELECTORS
    _LIST_NAME_INPUT_SELECTORS = list_flow.LIST_NAME_INPUT_SELECTORS
    _CREATE_LIST_BUTTON_SELECTORS = list_flow.CREATE_LIST_BUTTON_SELECTORS
    _INLINE_LIST_TITLE_BUTTON_SELECTORS = selectors.INLINE_LIST_TITLE_BUTTON_SELECTORS
    _INLINE_LIST_NAME_INPUT_SELECTORS = selectors.INLINE_LIST_NAME_INPUT_SELECTORS
    _LIST_NAME_INPUT_EXCLUSION_PHRASES = selectors.LIST_NAME_INPUT_EXCLUSION_PHRASES
    _FIRST_RESULT_SELECTORS = selectors.FIRST_RESULT_SELECTORS
    _PLACE_TITLE_SELECTORS = selectors.PLACE_TITLE_SELECTORS
    _PLACE_SUBTITLE_SELECTORS = selectors.PLACE_SUBTITLE_SELECTORS
    _PLACE_ADDRESS_SELECTORS = selectors.PLACE_ADDRESS_SELECTORS
    _PLACE_CATEGORY_SELECTORS = selectors.PLACE_CATEGORY_SELECTORS
    _PLACE_LOCATED_IN_SELECTORS = selectors.PLACE_LOCATED_IN_SELECTORS
    _ADD_PLACE_INPUT_SELECTORS = selectors.ADD_PLACE_INPUT_SELECTORS
    _SAVE_BUTTON_SELECTORS = save_flow.SAVE_BUTTON_SELECTORS
    _SAVE_CONTROL_TEXT_TOKENS = save_flow.SAVE_CONTROL_TEXT_TOKENS
    _SAVE_DIALOG_INTERACTIVE_SELECTORS = save_flow.SAVE_DIALOG_INTERACTIVE_SELECTORS
    _SAVE_DIALOG_NOTE_FIELD_SELECTORS = save_flow.SAVE_DIALOG_NOTE_FIELD_SELECTORS
    _SAVE_DIALOG_NOTE_KEYWORDS = save_flow.SAVE_DIALOG_NOTE_KEYWORDS
    _SEARCH_LOADING_INDICATOR_SELECTORS = search_flow.SEARCH_LOADING_INDICATOR_SELECTORS
    _SAVE_DIALOG_LOADING_INDICATOR_SELECTORS = save_flow.SAVE_DIALOG_LOADING_INDICATOR_SELECTORS
    _SAVE_PANEL_SAVED_STATE_SELECTORS = save_flow.SAVE_PANEL_SAVED_STATE_SELECTORS
    _SAVE_PANEL_NOTE_EXPAND_SELECTORS = save_flow.SAVE_PANEL_NOTE_EXPAND_SELECTORS
    _NO_RESULTS_TEXT_TOKENS = search_flow.NO_RESULTS_TEXT_TOKENS
    _SEARCH_OUTCOME_RESULT_READY = search_flow.SEARCH_OUTCOME_RESULT_READY
    _SEARCH_OUTCOME_NO_RESULTS = search_flow.SEARCH_OUTCOME_NO_RESULTS
    _SEARCH_OUTCOME_TIMEOUT_MS = search_flow.SEARCH_OUTCOME_TIMEOUT_MS
    _SEARCH_OUTCOME_MAX_TIMEOUT_MS = search_flow.SEARCH_OUTCOME_MAX_TIMEOUT_MS
    _SEARCH_OUTCOME_POLL_INTERVAL_MS = search_flow.SEARCH_OUTCOME_POLL_INTERVAL_MS
    _SEARCH_OUTCOME_MIN_SETTLE_MS = search_flow.SEARCH_OUTCOME_MIN_SETTLE_MS
    _SEARCH_RESULT_CLICK_TIMEOUT_MS = search_flow.SEARCH_RESULT_CLICK_TIMEOUT_MS
    _SEARCH_RESULT_CLICK_MAX_ATTEMPTS = search_flow.SEARCH_RESULT_CLICK_MAX_ATTEMPTS
    _SAVE_CONTROL_CLICK_TIMEOUT_MS = save_flow.SAVE_CONTROL_CLICK_TIMEOUT_MS
    _SAVE_CONTROL_CLICK_MAX_ATTEMPTS = save_flow.SAVE_CONTROL_CLICK_MAX_ATTEMPTS
    _SAVED_PANEL_READY_TIMEOUT_MS = list_flow.SAVED_PANEL_READY_TIMEOUT_MS
    _LIST_CREATION_READY_TIMEOUT_MS = list_flow.LIST_CREATION_READY_TIMEOUT_MS
    _LIST_OPEN_READY_TIMEOUT_MS = list_flow.LIST_OPEN_READY_TIMEOUT_MS
    _SAVE_DIALOG_READY_TIMEOUT_MS = save_flow.SAVE_DIALOG_READY_TIMEOUT_MS
    _SAVE_DIALOG_CLOSE_TIMEOUT_MS = save_flow.SAVE_DIALOG_CLOSE_TIMEOUT_MS
    _SAVE_DIALOG_LIST_RESOLVE_TIMEOUT_MS = save_flow.SAVE_DIALOG_LIST_RESOLVE_TIMEOUT_MS
    _SEARCH_INPUT_READY_TIMEOUT_MS = search_flow.SEARCH_INPUT_READY_TIMEOUT_MS
    _UI_ACTION_POLL_INTERVAL_MS = list_flow.UI_ACTION_POLL_INTERVAL_MS
    _NOTE_WRITE_MAX_ATTEMPTS = save_flow.NOTE_WRITE_MAX_ATTEMPTS
    _NOTE_CONFIRM_TIMEOUT_MS = save_flow.NOTE_CONFIRM_TIMEOUT_MS
    _UNTITLED_LIST_ENTRY_TOKENS = list_flow.UNTITLED_LIST_ENTRY_TOKENS
    _MAPS_OPEN_MAX_ATTEMPTS = search_flow.MAPS_OPEN_MAX_ATTEMPTS
    _SECURITY_BLOCK_MAIN_TOKEN = search_flow.SECURITY_BLOCK_MAIN_TOKEN
    _SECURITY_BLOCK_SIGNIN_TOKENS = search_flow.SECURITY_BLOCK_SIGNIN_TOKENS
    _LIMITED_VIEW_TOKEN = "limited view of google maps"

    def __init__(self, config: GoogleMapsDriverConfig) -> None:
        self._config = config
        self._playwright: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._last_page_html_snapshot: str | None = None
        self._interrupt_requested = False
        self._original_sigint_handler: Any | None = None

    async def start(self) -> None:
        """Start Playwright and open persistent browser context."""

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise GoogleMapsDependencyError(
                "Playwright is not installed. Run `uv sync` and `playwright install chromium`."
            ) from exc

        self._playwright = await async_playwright().start()
        _prepare_user_data_dir_for_automation(self._config.user_data_dir)
        launch_options = _build_launch_options(
            user_data_dir=self._config.user_data_dir,
            headless=self._config.headless,
            suppress_automation_flags=self._config.suppress_automation_flags,
            base_args=self._BROWSER_RECOVERY_SUPPRESSION_ARGS,
        )
        context = await self._launch_persistent_context_with_channel_fallback(launch_options)
        if context is None:
            raise GoogleMapsError("Playwright did not return a browser context.")
        self._context = context
        self._page = await context.new_page()
        self._install_interrupt_handler()

    def _install_interrupt_handler(self) -> None:
        """Install a SIGINT handler that requests a graceful shutdown.

        We keep the browser alive long enough to capture a debug snapshot and
        save checkpoints, then let the outer workflow close Playwright.
        """
        import signal as _signal

        original = _signal.getsignal(_signal.SIGINT)
        self._original_sigint_handler = original
        self._interrupt_requested = False

        def _on_sigint(signum: int, frame: Any) -> None:
            del signum, frame
            if self._interrupt_requested:
                # Second Ctrl+C should force-unblock Playwright and terminate.
                _force_kill_browser_process(self._context)
                _signal.signal(_signal.SIGINT, _signal.default_int_handler)
                raise KeyboardInterrupt
            self._interrupt_requested = True

        _signal.signal(_signal.SIGINT, _on_sigint)

    def _uninstall_interrupt_handler(self) -> None:
        import signal as _signal

        if self._original_sigint_handler is not None:
            _signal.signal(_signal.SIGINT, self._original_sigint_handler)
            self._original_sigint_handler = None
        self._interrupt_requested = False

    async def close(self, *, force: bool = False) -> None:
        """Close browser context and Playwright runtime."""

        self._uninstall_interrupt_handler()

        context = self._context
        playwright = self._playwright
        self._context = None
        self._playwright = None
        self._page = None

        if force:
            await _force_kill_playwright(context, playwright)
            return

        close_error: BaseException | None = None
        if context is not None:
            try:
                await context.close()
            except BaseException as exc:  # noqa: BLE001
                close_error = close_error or exc
        if playwright is not None:
            try:
                await playwright.stop()
            except BaseException as exc:  # noqa: BLE001
                close_error = close_error or exc
        if close_error is not None and not isinstance(close_error, KeyboardInterrupt):
            raise close_error

    async def open_maps_home(self) -> None:
        """Navigate to Google Maps home."""

        page = await self._require_page()
        last_error: Exception | None = None
        for _attempt in range(1, self._MAPS_OPEN_MAX_ATTEMPTS + 1):
            try:
                await self._run_browser_step(
                    page=page,
                    step=f"open_maps_home.goto({GOOGLE_MAPS_HOME_URL})",
                    action=lambda: page.goto(
                        GOOGLE_MAPS_HOME_URL,
                        wait_until="domcontentloaded",
                    ),
                )
                await self._wait_for_timeout(
                    page,
                    int(self._config.sync_delay_seconds * 1000),
                    step="open_maps_home.settle",
                )
                return
            except Exception as exc:  # noqa: BLE001
                if not _is_navigation_abort_error(exc):
                    raise
                last_error = exc
                try:
                    await self._run_browser_step(
                        page=page,
                        step="open_maps_home.recover_to_about_blank",
                        action=lambda: page.goto("about:blank", wait_until="commit", timeout=5000),
                    )
                except Exception:  # noqa: BLE001
                    pass
                await self._wait_for_timeout(page, 500, step="open_maps_home.retry_backoff")

        raise GoogleMapsTransientError(

                "Unable to open Google Maps due to repeated navigation aborts (net::ERR_ABORTED). "
                "Close other browsers using the same --google-user-data-dir and retry."

        ) from last_error

    async def is_authenticated(self, *, refresh: bool = True) -> bool:
        """Check whether current session appears authenticated."""

        page = await self._require_page()
        if refresh:
            await self.open_maps_home()
            page = await self._require_page()
        if await self._is_any_selector_visible(page, self._SIGN_IN_BUTTON_SELECTORS, timeout_ms=1000):
            return False
        if await self._is_any_selector_visible(page, self._SIGNED_IN_ANCHOR_SELECTORS, timeout_ms=1000):
            return True
        return await self._has_google_session_cookie()

    async def wait_for_authenticated(self, timeout_seconds: int) -> bool:
        """Wait for user to complete manual login in headed mode."""

        deadline_seconds = max(timeout_seconds, 1)
        elapsed = 0
        while elapsed < deadline_seconds:
            try:
                if await self._has_login_security_block():
                    raise GoogleMapsLoginBlockedError(

                            "Google sign-in was blocked with 'This browser or app may not be secure'. "
                            "Install/update Google Chrome and retry."

                    )
                if await self.is_authenticated(refresh=False):
                    return True
            except Exception as exc:  # noqa: BLE001
                if not _is_target_closed_error(exc):
                    raise
                # User may close the currently active tab during manual login.
                try:
                    await self._require_page()
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(1)
            elapsed += 1
        return False

    async def list_exists(self, list_name: str) -> bool:
        """Return whether the list appears under Saved lists."""

        page = await self._require_page()
        await self._open_saved_tab()
        await self._ensure_lists_view(page)

        list_locator = await self._wait_until_list_entry_available(
            page,
            list_name=list_name,
            max_attempts=4,
        )
        if list_locator is not None:
            return True

        if await self._open_saved_view_more(page):
            await self._wait_for_timeout(
                page,
                int(self._config.sync_delay_seconds * 1000),
                step=f"list_exists.wait_after_view_more({list_name})",
            )
            list_locator = await self._wait_until_list_entry_available(
                page,
                list_name=list_name,
                max_attempts=4,
            )
            if list_locator is not None:
                return True

        return False

    async def create_list(self, list_name: str) -> None:
        """Create a new Google Maps list."""

        page = await self._require_page()
        await self._open_saved_tab()
        await self._ensure_lists_view(page)
        new_list_button = await self._resolve_new_list_button(page)
        if new_list_button is None:
            visible_buttons = await self._summarize_saved_panel_controls_for_debug(page)
            raise GoogleMapsSelectorError(

                    "Unable to locate the New list button in Saved panel. "
                    f"Visible saved controls snapshot: {visible_buttons}"

            )
        await self._click_locator(
            page=page,
            locator=new_list_button,
            step=f"create_list.click_new_list_button({list_name})",
            error_type=GoogleMapsSelectorError,
        )
        await self._wait_for_timeout(
            page,
            int(self._config.sync_delay_seconds * 1000),
            step="create_list.wait_after_new_list_click",
        )
        await self._wait_for_list_creation_surface(page)

        list_name_applied = await self._try_rename_inline_untitled_list(page, list_name)
        if not list_name_applied:
            list_name_applied = await self._try_set_list_name_in_creation_dialog(page, list_name)
        if not list_name_applied:
            list_name_applied = await self._try_rename_inline_untitled_list(page, list_name)
        await self._wait_for_timeout(
            page,
            int(self._config.sync_delay_seconds * 1000),
            step="create_list.wait_before_verify_list_entry",
        )
        created_entry = await self._wait_until_list_entry_available(page, list_name=list_name, max_attempts=8)
        if created_entry is None:
            saved_snapshot = await self._summarize_saved_panel_controls_for_debug(page)
            if not list_name_applied:
                raise GoogleMapsSelectorError(

                        "Unable to locate list-name input field or inline list-title editor. "
                        f"Visible saved controls snapshot: {saved_snapshot}"

                )
            raise GoogleMapsSelectorError(

                    f"List creation did not produce expected list '{list_name}'. "
                    f"Visible saved controls snapshot: {saved_snapshot}"

            )
        await self._wait_for_timeout(
            page,
            int(self._config.sync_delay_seconds * 1000),
            step=f"create_list.wait_for_backend_settle({list_name})",
        )
        await self.open_maps_home()
        if not await self.open_list(list_name):
            saved_snapshot = await self._summarize_saved_panel_controls_for_debug(await self._require_page())
            raise GoogleMapsSelectorError(

                    f"List creation produced '{list_name}', but it was not reusable after refresh. "
                    f"Visible saved controls snapshot: {saved_snapshot}"

            )

    async def open_list(self, list_name: str) -> bool:
        """Open a named list in the side panel."""

        page = await self._require_page()
        await self._open_saved_tab()
        await self._ensure_lists_view(page)
        list_locator = await self._wait_until_list_entry_available(
            page,
            list_name=list_name,
            max_attempts=8,
        )
        if list_locator is None and await self._open_saved_view_more(page):
            await self._wait_for_timeout(
                page,
                int(self._config.sync_delay_seconds * 1000),
                step=f"open_list.wait_after_view_more({list_name})",
            )
            list_locator = await self._wait_until_list_entry_available(
                page,
                list_name=list_name,
                max_attempts=8,
            )
        if list_locator is None:
            await self.open_maps_home()
            page = await self._require_page()
            await self._open_saved_tab()
            await self._ensure_lists_view(page)
            list_locator = await self._wait_until_list_entry_available(
                page,
                list_name=list_name,
                max_attempts=8,
            )
        if list_locator is None:
            return False
        await self._click_locator(
            page=page,
            locator=list_locator,
            step=f"open_list.click_list_entry({list_name})",
            error_type=GoogleMapsSelectorError,
        )
        await self._wait_for_timeout(
            page,
            int(self._config.sync_delay_seconds * 1000),
            step=f"open_list.wait_after_click({list_name})",
        )
        await self._wait_for_opened_list_context(page, list_name=list_name)
        return True

    async def search_and_open_first_result(self, query: str) -> PlaceCandidate | None:
        """Search Google Maps and open the best first result candidate."""
        import logging as _logging
        _log = _logging.getLogger(__name__)

        page = await self._require_page()
        _t0 = monotonic()
        search_box = await self._resolve_search_box_locator(page)
        if search_box is None:
            await self.open_maps_home()
            page = await self._require_page()
            search_box = await self._resolve_search_box_locator(page)
        if search_box is None:
            controls_snapshot = await self._summarize_search_controls_for_debug(page)
            raise GoogleMapsSelectorError(

                    "Google Maps search box is not available. "
                    f"Visible search controls snapshot: {controls_snapshot}"

            )

        _t1 = monotonic()
        _log.debug(f"[timing] resolve_search_box: {(_t1-_t0)*1000:.0f}ms  query={query!r}")

        await self._wait_for_search_input_ready(page, search_box)
        _t2 = monotonic()
        _log.debug(f"[timing] wait_for_search_input_ready: {(_t2-_t1)*1000:.0f}ms")

        previous_state = await self._capture_search_panel_state(page)
        _t3 = monotonic()
        _log.debug(f"[timing] capture_previous_state: {(_t3-_t2)*1000:.0f}ms  prev_sig={previous_state.first_result_signature!r:.40}")

        await self._fill_locator(
            page=page,
            locator=search_box,
            value=query,
            step=f"search.fill_query({query})",
            error_type=GoogleMapsSelectorError,
        )
        await self._press_locator(
            page=page,
            locator=search_box,
            key="Enter",
            step=f"search.submit_query({query})",
            error_type=GoogleMapsSelectorError,
        )
        _t4 = monotonic()
        _log.debug(f"[timing] fill_and_press_enter: {(_t4-_t3)*1000:.0f}ms")

        search_outcome = await self._wait_for_search_outcome(
            page=page,
            query=query,
            previous_state=previous_state,
        )
        _t5 = monotonic()
        _log.debug(f"[timing] wait_for_search_outcome: {(_t5-_t4)*1000:.0f}ms  outcome={search_outcome!r}")

        if search_outcome == self._SEARCH_OUTCOME_NO_RESULTS:
            await self._cache_page_html_snapshot(page)
            return None

        await self._try_open_first_result(page=page, query=query)
        _t6 = monotonic()
        _log.debug(f"[timing] try_open_first_result: {(_t6-_t5)*1000:.0f}ms")

        if await self._is_add_place_input_visible(page):
            await self._cache_page_html_snapshot(page)
            return None

        title_text = await self._extract_text(page, self._PLACE_TITLE_SELECTORS)
        if not title_text or title_text.lower() in ("results", "結果", "検索結果"):
            title_text = await self._extract_title_from_page_title(page)
        if not title_text:
            await self._cache_page_html_snapshot(page)
            return None
        address_text = await self._extract_text(page, self._PLACE_ADDRESS_SELECTORS)
        if not address_text:
            address_text = await self._extract_address_fallback(page)
        category_text = await self._extract_text(page, self._PLACE_CATEGORY_SELECTORS)
        subtitle_text = await self._extract_text(page, self._PLACE_SUBTITLE_SELECTORS)
        located_in_text = await self._extract_text(page, self._PLACE_LOCATED_IN_SELECTORS)
        _t7 = monotonic()
        _log.debug(f"[timing] extract_place_metadata: {(_t7-_t6)*1000:.0f}ms  title={title_text!r}  subtitle={subtitle_text!r}")
        _log.debug(f"[timing] TOTAL search_and_open_first_result: {(_t7-_t0)*1000:.0f}ms")
        await self._cache_page_html_snapshot(page)
        return PlaceCandidate(
            name=title_text,
            address=address_text,
            category=category_text,
            subtitle=subtitle_text,
            located_in=located_in_text,
        )

    async def save_current_place_to_list(self, list_name: str, note_text: str = "") -> bool:
        """Save currently opened place into a specific list."""
        import logging as _logging
        _log = _logging.getLogger(__name__)

        _t_require_start = monotonic()
        page = await self._require_page()
        _t0 = monotonic()
        _log.debug(f"[timing] save.require_page: {(_t0-_t_require_start)*1000:.0f}ms")
        add_place_input_visible = await self._is_add_place_input_visible(page)
        _t_add_check = monotonic()
        _log.debug(
            f"[timing] save.check_add_place_input: {(_t_add_check-_t0)*1000:.0f}ms  "
            f"visible={add_place_input_visible!r}"
        )
        if add_place_input_visible:
            controls_snapshot = await self._summarize_search_controls_for_debug(page)
            raise GoogleMapsSelectorError(

                    "Save control is unavailable because Maps is still in list-edit mode "
                    "(Search for a place to add). "
                    f"Visible controls snapshot: {controls_snapshot}"

            )
        note_value_check = _normalize_note_value(note_text)
        panel_saved_list_name = await self._resolve_panel_saved_list_name(page)
        panel_saved_state_visible = bool(panel_saved_list_name)
        _t_panel_saved_state = monotonic()
        _log.debug(
            f"[timing] save.check_panel_saved_state: {(_t_panel_saved_state-_t_add_check)*1000:.0f}ms  "
            f"visible={panel_saved_state_visible!r}"
        )
        if panel_saved_state_visible:
            saved_matches_target = self._list_name_match_score(
                list_name=list_name, candidate_text=panel_saved_list_name,
            ) >= 2
            _log.debug(
                f"[timing] save.panel_saved_state_visible: saved_in={panel_saved_list_name!r}  "
                f"matches_target={saved_matches_target!r}"
            )
            if not saved_matches_target:
                _log.debug(
                    f"[timing] save.panel_saved_state_visible: saved_in_different_list "
                    f"(target={list_name!r}, actual={panel_saved_list_name!r})"
                )
                # Place is saved to a different list — proceed with normal save flow below.
            else:
                if not note_value_check:
                    raise GoogleMapsPlaceAlreadySavedError(
                        f"Current place is already saved in list '{list_name}'."
                    )
                if not await self._is_target_note_editor_visible(page=page, list_name=list_name):
                    await self._attempt_expand_place_panel_note_editor(page=page, list_name=list_name)

                note_applied = False
                for _note_attempt in range(self._NOTE_WRITE_MAX_ATTEMPTS):
                    _tn0 = monotonic()
                    _log.debug(f"[timing] save.panel_note_attempt={_note_attempt + 1}")
                    if not await self._is_target_note_editor_visible(page=page, list_name=list_name):
                        await self._attempt_expand_place_panel_note_editor(page=page, list_name=list_name)
                    _note_written = await self._try_apply_note_to_saved_place(
                        page=page,
                        list_name=list_name,
                        note_text=note_value_check,
                    )
                    _tn1 = monotonic()
                    _log.warning(
                        f"[timing] save.panel_try_apply_note: {(_tn1-_tn0)*1000:.0f}ms  written={_note_written!r}"
                    )
                    if not _note_written:
                        continue
                    _note_verified = await self._wait_for_note_text_applied(
                        page=page,
                        list_name=list_name,
                        note_text=note_value_check,
                    )
                    _tn2 = monotonic()
                    _log.warning(
                        f"[timing] save.panel_wait_for_note_applied: {(_tn2-_tn1)*1000:.0f}ms  verified={_note_verified!r}"
                    )
                    if await self._verify_note_after_write(
                        page=page,
                        list_name=list_name,
                        note_text=note_value_check,
                        current_surface_verified=_note_verified,
                    ):
                        note_applied = True
                        break
                if not note_applied:
                    raise GoogleMapsNoteWriteError(
                        "Unable to verify note text persisted from the place panel save state. "
                        f"Visible controls snapshot: {await self._summarize_visible_controls_for_debug(page)}",
                        place_saved=True,
                    )
                _log.warning(
                    f"[timing] TOTAL save_current_place_to_list: {(monotonic()-_t0)*1000:.0f}ms  "
                    f"list={list_name!r} (panel_saved_state_path)"
                )
                return True
        save_button_summary = await self._click_save_control_with_retry(page=page, list_name=list_name)
        _t1 = monotonic()
        _log.debug(f"[timing] save.click_save_control: {(_t1-_t0)*1000:.0f}ms  list={list_name!r}")

        list_selector = await self._wait_for_save_dialog_list_selector(page, list_name)
        _t2 = monotonic()
        _log.debug(f"[timing] save.wait_for_list_selector: {(_t2-_t1)*1000:.0f}ms  found={list_selector is not None}")

        if list_selector is None:
            retry_click_error: GoogleMapsTransientError | None = None
            if not await self._wait_for_save_dialog_open(page):
                try:
                    save_button_summary = await self._click_save_control_with_retry(
                        page=page,
                        list_name=list_name,
                    )
                except GoogleMapsTransientError as exc:
                    retry_click_error = exc
                list_selector = await self._wait_for_save_dialog_list_selector(page, list_name)
            if list_selector is None and not await self._wait_for_save_dialog_open(page):
                controls_snapshot = await self._summarize_search_controls_for_debug(page)
                retry_hint = ""
                if retry_click_error is not None:
                    retry_hint = f" Retry click error: {retry_click_error}."
                raise GoogleMapsTransientError(

                        "Save dialog did not appear after clicking Save. "
                        f"Clicked control summary: {save_button_summary}. "
                        f"{retry_hint}"
                        f"Visible controls snapshot: {controls_snapshot}. "
                        f"Browser snapshot: {await self._summarize_browser_state_for_debug(page)}"

                )
            if list_selector is None:
                return False

        pre_click_selection_state = await self._read_locator_selection_state(list_selector)
        _log.debug(f"[timing] save.pre_click_selection_state={pre_click_selection_state!r}")
        # Treat True (confirmed saved) and None (unknown/unreadable state) the same:
        # do not click, to avoid accidentally toggling the item off — UNLESS we need
        # to write a note, in which case we must uncheck then recheck so the note
        # editor appears (Google Maps only shows the note editor when re-selecting).
        if pre_click_selection_state is not False and not note_value_check:
            if await self._is_save_dialog_visible(page):
                await self._press_keyboard(
                    page=page,
                    key="Escape",
                    step=f"save.dismiss_surface_already_saved({list_name})",
                    error_type=GoogleMapsTransientError,
                )
                await self._wait_for_timeout(
                    page,
                    int(self._config.sync_delay_seconds * 1000),
                    step=f"save.wait_after_dismiss_already_saved({list_name})",
                )
                await self._wait_for_save_dialog_closed(page)
            raise GoogleMapsPlaceAlreadySavedError(
                f"Current place is already saved in list '{list_name}'."
            )

        if pre_click_selection_state is True and note_value_check:
            # Place is already saved and a note is requested.
            # Do not uncheck/recheck; rely on note-surface expansion helpers instead.
            # The list dialog is currently open (we just clicked Save to open it).
            # Close it immediately so the place-panel note editor becomes accessible.
            _log.debug("[timing] save.already_saved_for_note: skip_uncheck_recheck")
            if await self._is_save_dialog_visible(page):
                await self._press_keyboard(
                    page=page,
                    key="Escape",
                    step=f"save.dismiss_dialog_already_saved_for_note({list_name})",
                    error_type=GoogleMapsTransientError,
                )
                await self._wait_for_save_dialog_closed(page)
                _log.debug("[timing] save.already_saved_for_note: dialog_dismissed")
        elif pre_click_selection_state is False:
            # Normal path: place not yet saved, click to save.
            _tc0 = monotonic()
            await self._click_locator(
                page=page,
                locator=list_selector,
                step=f"save.select_list_entry({list_name})",
                error_type=GoogleMapsTransientError,
            )
            _tc1 = monotonic()
            _log.debug(f"[timing] save.click_list_entry: {(_tc1-_tc0)*1000:.0f}ms")
            selection_applied = await self._wait_for_list_selection_applied(page, list_name, list_selector)
            _tc2 = monotonic()
            _log.debug(f"[timing] save.wait_for_selection_applied: {(_tc2-_tc1)*1000:.0f}ms  applied={selection_applied!r}")
            if not selection_applied:
                if note_value_check:
                    place_saved = await self._detect_place_saved_state_after_note_failure(
                        page=page,
                        list_name=list_name,
                        list_selector=list_selector,
                    )
                    if place_saved:
                        _log.warning(
                            "List selection verification did not settle, but target saved state is visible; "
                            f"continuing with note write. list={list_name!r}"
                        )
                    else:
                        raise GoogleMapsTransientError(

                                f"List selection did not apply after click for '{list_name}'. "
                                f"Clicked control summary: {await self._normalized_locator_text(list_selector) or '<unknown>'}. "
                                f"Visible controls snapshot: {await self._summarize_visible_controls_for_debug(page)}"

                        )
                else:
                    raise GoogleMapsTransientError(

                            f"List selection did not apply after click for '{list_name}'. "
                            f"Clicked control summary: {await self._normalized_locator_text(list_selector) or '<unknown>'}. "
                            f"Visible controls snapshot: {await self._summarize_visible_controls_for_debug(page)}"

                    )
        note_value = note_value_check
        if note_value:
            note_applied = False
            for _note_attempt in range(self._NOTE_WRITE_MAX_ATTEMPTS):
                _tn0 = monotonic()
                _log.debug(f"[timing] save.note_attempt={_note_attempt + 1}")
                _dialog_ready = await self._ensure_save_dialog_ready_for_note(page=page, list_name=list_name)
                _tn1 = monotonic()
                _log.debug(f"[timing] save.ensure_dialog_ready_for_note: {(_tn1-_tn0)*1000:.0f}ms  ready={_dialog_ready!r}")
                if not _dialog_ready:
                    continue
                _note_written = await self._try_apply_note_to_saved_place(
                    page=page,
                    list_name=list_name,
                    note_text=note_value,
                )
                _tn2 = monotonic()
                _log.debug(f"[timing] save.try_apply_note: {(_tn2-_tn1)*1000:.0f}ms  written={_note_written!r}")
                if not _note_written:
                    continue
                _note_verified = await self._wait_for_note_text_applied(
                    page=page,
                    list_name=list_name,
                    note_text=note_value,
                )
                _tn3 = monotonic()
                _log.debug(f"[timing] save.wait_for_note_applied: {(_tn3-_tn2)*1000:.0f}ms  verified={_note_verified!r}")
                if await self._verify_note_after_write(
                    page=page,
                    list_name=list_name,
                    note_text=note_value,
                    current_surface_verified=_note_verified,
                ):
                    note_applied = True
                    break
            if not note_applied:
                place_saved = await self._detect_place_saved_state_after_note_failure(
                    page=page,
                    list_name=list_name,
                    list_selector=list_selector,
                )
                limited_view_active = await self._is_limited_view_mode(page)
                loading_active = await self._is_any_selector_visible(
                    page,
                    self._SAVE_DIALOG_LOADING_INDICATOR_SELECTORS,
                    timeout_ms=80,
                )
                saved_state_hint = "yes" if place_saved else "no"
                limited_view_hint = (
                    " Limited-view mode detected; note editor may be unavailable."
                    if limited_view_active
                    else ""
                )
                loading_hint = (
                    " Save surface still showed loading indicators."
                    if loading_active
                    else ""
                )
                raise GoogleMapsNoteWriteError(
                    "Unable to verify note text persisted in the save surface after retry. "
                    f"Place saved before note failure: {saved_state_hint}. "
                    f"Visible controls snapshot: {await self._summarize_visible_controls_for_debug(page)}."
                    f"{limited_view_hint}{loading_hint}",
                    place_saved=place_saved,
                )
        if await self._is_save_dialog_visible(page):
            await self._press_keyboard(
                page=page,
                key="Escape",
                step=f"save.dismiss_surface({list_name})",
                error_type=GoogleMapsTransientError,
            )
            await self._wait_for_timeout(
                page,
                int(self._config.sync_delay_seconds * 1000),
                step=f"save.wait_after_dismiss({list_name})",
            )
            await self._wait_for_save_dialog_closed(page)
        _log.debug(f"[timing] TOTAL save_current_place_to_list: {(monotonic()-_t0)*1000:.0f}ms  list={list_name!r}")
        return True

    async def take_screenshot(self, path: Path) -> None:
        """Capture current page screenshot."""

        page = await self._require_page()
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=True)

    async def dump_page_html(self, path: Path) -> bool:
        """Dump current page HTML into a local file for debugging."""

        html_content: str | None = None

        # Snapshot capture should prefer existing page handles and avoid creating
        # a fresh blank tab when runtime is already failing/interrupted.
        candidate_pages: list[Any] = []
        if self._page is not None:
            candidate_pages.append(self._page)
        context = self._context
        if context is not None:
            for candidate in reversed(tuple(getattr(context, "pages", ()))):
                if candidate in candidate_pages:
                    continue
                candidate_pages.append(candidate)

        for candidate in candidate_pages:
            html_content = await _read_page_content_safe(candidate)
            if html_content is not None:
                self._last_page_html_snapshot = html_content
                break

        if html_content is None:
            try:
                page = await self._require_page()
            except Exception:  # noqa: BLE001
                html_content = self._last_page_html_snapshot
                if html_content is None:
                    return False
            else:
                html_content = await _read_page_content_safe(page)
                if html_content is None:
                    html_content = self._last_page_html_snapshot
                    if html_content is None:
                        return False
                else:
                    self._last_page_html_snapshot = html_content

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_content, encoding="utf-8")
        return True

    async def _cache_page_html_snapshot(self, page: Any) -> None:
        html_content = await _read_page_content_safe(page)
        if html_content is not None:
            self._last_page_html_snapshot = html_content

    async def _open_saved_tab(self) -> None:
        page = await self._require_page()
        exact_saved_tab = page.get_by_role("button", name="Saved", exact=True)
        if await exact_saved_tab.count() > 0:
            await self._click_locator(
                page=page,
                locator=exact_saved_tab.first,
                step="saved_tab.click_exact",
                error_type=GoogleMapsSelectorError,
            )
            await self._wait_for_timeout(
                page,
                int(self._config.sync_delay_seconds * 1000),
                step="saved_tab.wait_after_exact_click",
            )
            await self._wait_for_saved_panel_ready(page)
            return

        saved_tab = await self._first_visible_locator(page, self._SAVED_TAB_SELECTORS)
        if saved_tab is None:
            raise GoogleMapsSelectorError("Saved tab is unavailable in current Maps layout.")
        await self._click_locator(
            page=page,
            locator=saved_tab,
            step="saved_tab.click_fallback",
            error_type=GoogleMapsSelectorError,
        )
        await self._wait_for_timeout(
            page,
            int(self._config.sync_delay_seconds * 1000),
            step="saved_tab.wait_after_fallback_click",
        )
        await self._wait_for_saved_panel_ready(page)

    _GOOGLE_MAPS_TITLE_SUFFIX = " - Google Maps"

    async def _extract_title_from_page_title(self, page: Any) -> str:
        """Fallback: extract place name from the browser <title> tag.

        Google Maps sets ``<title>`` to ``"<place name> - Google Maps"``
        even when the H1 element is stale or contains unrelated text
        (e.g. ``"Results"`` or an address from a previous search).
        """
        try:
            raw_title = await page.title()
        except Exception:  # noqa: BLE001
            return ""
        if not raw_title:
            return ""
        if raw_title.endswith(self._GOOGLE_MAPS_TITLE_SUFFIX):
            return raw_title[: -len(self._GOOGLE_MAPS_TITLE_SUFFIX)].strip()
        # Some locales use a slightly different suffix; strip known suffix.
        if raw_title.endswith(" - Google 地圖"):
            return raw_title[: -len(" - Google 地圖")].strip()
        return raw_title.strip()

    async def _extract_address_fallback(self, page: Any) -> str:
        """Fallback address extraction for pages without ``data-item-id='address'``.

        Taiwan / newer Google Maps pages render the address icon as a
        ``<span aria-label="Address">`` (not a ``<button>``).  The actual
        address text lives in a sibling ``<span class="DkEaL">``.  We walk
        up to the nearest info-row ancestor and read its text.
        """
        try:
            # Strategy 1: find the Address icon span → get parent row text
            icon = page.locator('span[aria-label="Address"]')
            if await icon.count() > 0:
                # The parent chain: span icon → div.LCF4w (info row) contains the text
                parent_row = icon.first.locator("xpath=ancestor::div[contains(@class,'LCF4w')]")
                if await parent_row.count() > 0:
                    row_text = str(await parent_row.first.inner_text(timeout=2000)).strip()
                    if row_text:
                        return row_text
                # Simpler fallback: sibling span.DkEaL
                sibling = icon.first.locator(
                    "xpath=following-sibling::span//span[@class='DkEaL']"
                    " | ../span[@class='JpCtJf']//span[@class='DkEaL']"
                )
                if await sibling.count() > 0:
                    text = str(await sibling.first.inner_text(timeout=2000)).strip()
                    if text:
                        return text
            # Strategy 2: look for the "Copy address" tooltip sibling
            copy_btn = page.locator('button[data-tooltip="Copy address"], button[aria-label="Copy address"]')
            if await copy_btn.count() > 0:
                parent_row = copy_btn.first.locator("xpath=ancestor::div[contains(@class,'LCF4w')]")
                if await parent_row.count() > 0:
                    row_text = str(await parent_row.first.inner_text(timeout=2000)).strip()
                    if row_text:
                        return row_text
        except Exception:  # noqa: BLE001
            pass
        return ""

    async def _extract_text(self, page: Any, selectors: tuple[str, ...]) -> str:
        locator = await self._first_matching_locator(page, selectors)
        if locator is None or await locator.count() == 0:
            return ""
        try:
            return str(await locator.first.inner_text(timeout=2000)).strip()
        except Exception:  # noqa: BLE001
            return ""

    async def _first_matching_locator(self, page: Any, selectors: tuple[str, ...]) -> Any | None:
        for selector in selectors:
            locator = page.locator(selector)
            if await locator.count() > 0:
                return locator.first
        return None

    async def _first_visible_locator(self, page: Any, selectors: tuple[str, ...]) -> Any | None:
        for selector in selectors:
            locator = page.locator(selector)
            if await locator.count() == 0:
                continue
            candidate = locator.first
            try:
                if await candidate.is_visible():
                    return candidate
            except Exception:  # noqa: BLE001
                return candidate
        return None

    async def _resolve_search_box_locator(self, page: Any) -> Any | None:
        return await self._first_visible_locator(page, self._SEARCH_BOX_SELECTORS)

    async def _run_browser_step(
        self,
        *,
        page: Any,
        step: str,
        action: Callable[[], Any],
        error_type: type[GoogleMapsError] = GoogleMapsTransientError,
    ) -> Any:
        if self._interrupt_requested:
            raise KeyboardInterrupt
        try:
            result = await action()
        except GoogleMapsError:
            raise
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, KeyboardInterrupt):
                raise
            if _is_target_closed_error(exc):
                raise
            snapshot = await self._summarize_browser_state_for_debug(page)
            raise error_type(
                f"{step} failed: {exc}. Browser snapshot: {snapshot}"
            ) from exc
        if self._interrupt_requested:
            raise KeyboardInterrupt
        return result

    async def _wait_for_timeout(self, page: Any, delay_ms: int, *, step: str) -> None:
        await self._run_browser_step(
            page=page,
            step=step,
            action=lambda: page.wait_for_timeout(delay_ms),
            error_type=GoogleMapsTransientError,
        )

    async def _click_locator(
        self,
        *,
        page: Any,
        locator: Any,
        step: str,
        timeout_ms: int | None = None,
        error_type: type[GoogleMapsError] = GoogleMapsTransientError,
        allow_force_click: bool = False,
    ) -> None:
        async def _action() -> None:
            if timeout_ms is None:
                await locator.click()
                return
            await locator.click(timeout=timeout_ms)

        try:
            await self._run_browser_step(
                page=page,
                step=step,
                action=_action,
                error_type=error_type,
            )
        except error_type as click_error:
            if not allow_force_click or not self._is_retryable_click_error(click_error):
                raise

            async def _force_action() -> None:
                if timeout_ms is None:
                    await locator.click(force=True)
                    return
                await locator.click(timeout=timeout_ms, force=True)

            await self._run_browser_step(
                page=page,
                step=f"{step}.force_click",
                action=_force_action,
                error_type=error_type,
            )

    def _is_retryable_click_error(self, error: BaseException) -> bool:
        normalized_error = _normalize_text_for_matching(str(error))
        if "click" not in normalized_error:
            return False
        retryable_tokens = (
            "timeout",
            "not stable",
            "detached from the dom",
            "not attached to the dom",
            "element is outside of the viewport",
            "intercepts pointer events",
            "retrying click action",
        )
        return any(token in normalized_error for token in retryable_tokens)

    async def _try_open_first_result(self, *, page: Any, query: str) -> bool:
        for attempt in range(1, self._SEARCH_RESULT_CLICK_MAX_ATTEMPTS + 1):
            first_result_locator = await self._first_matching_locator(page, self._FIRST_RESULT_SELECTORS)
            if first_result_locator is None:
                return False
            try:
                if await first_result_locator.count() == 0:
                    return False
            except Exception:  # noqa: BLE001
                return False

            try:
                await self._click_locator(
                    page=page,
                    locator=first_result_locator,
                    step=f"search.open_first_result({query})",
                    timeout_ms=self._SEARCH_RESULT_CLICK_TIMEOUT_MS,
                    error_type=GoogleMapsTransientError,
                    allow_force_click=True,
                )
                await self._wait_for_timeout(
                    page,
                    int(self._config.sync_delay_seconds * 1000),
                    step=f"search.wait_after_open_first_result({query})",
                )
                if self._supports_dom_waits(page):
                    async def _save_btn_pred() -> bool:
                        return await self._resolve_save_button(page) is not None

                    await self._wait_for_condition(
                        page=page,
                        timeout_ms=self._SEARCH_RESULT_CLICK_TIMEOUT_MS,
                        predicate=_save_btn_pred,
                    )
                return True
            except GoogleMapsTransientError as exc:
                if not self._is_retryable_click_error(exc):
                    raise
                if attempt >= self._SEARCH_RESULT_CLICK_MAX_ATTEMPTS:
                    break
                await self._wait_for_timeout(
                    page,
                    self._UI_ACTION_POLL_INTERVAL_MS,
                    step=f"search.retry_open_first_result({query})",
                )
        return False

    async def _detect_place_saved_state_after_note_failure(
        self,
        *,
        page: Any,
        list_name: str,
        list_selector: Any,
    ) -> bool:
        try:
            if await self._did_list_selection_apply(page, list_name, list_selector):
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            panel_saved_list_name = await self._resolve_panel_saved_list_name(page)
        except Exception:  # noqa: BLE001
            return False
        return self._list_name_match_score(
            list_name=list_name,
            candidate_text=panel_saved_list_name,
        ) >= 2

    async def _verify_note_after_write(
        self,
        *,
        page: Any,
        list_name: str,
        note_text: str,
        current_surface_verified: bool,
    ) -> bool:
        if current_surface_verified:
            return True
        if await self._is_target_saved_state_visible_on_place_panel(page=page, list_name=list_name):
            return True
        return await self._confirm_note_persisted_after_reopen(
            page=page,
            list_name=list_name,
            note_text=note_text,
        )

    async def _confirm_note_persisted_on_place_panel(
        self,
        *,
        page: Any,
        list_name: str,
        note_text: str,
    ) -> bool:
        normalized_note_text = _normalize_note_value(note_text)
        if not normalized_note_text:
            return False
        if await self._is_save_dialog_visible(page):
            await self._press_keyboard(
                page=page,
                key="Escape",
                step=f"save.dismiss_surface_for_note_persistence_check({list_name})",
                error_type=GoogleMapsTransientError,
            )
            await self._wait_for_timeout(
                page,
                int(self._config.sync_delay_seconds * 1000),
                step=f"save.wait_after_dismiss_for_note_persistence_check({list_name})",
            )
            await self._wait_for_save_dialog_closed(page)

        panel_saved_list_name = await self._resolve_panel_saved_list_name(page)
        if self._list_name_match_score(
            list_name=list_name,
            candidate_text=panel_saved_list_name,
        ) < 2:
            return False

        if not await self._is_target_note_editor_visible(page=page, list_name=list_name):
            if not await self._attempt_expand_place_panel_note_editor(page=page, list_name=list_name):
                return False
        return await self._wait_for_place_panel_note_text(
            page=page,
            list_name=list_name,
            note_text=normalized_note_text,
        )

    async def _confirm_note_persisted_after_reopen(
        self,
        *,
        page: Any,
        list_name: str,
        note_text: str,
    ) -> bool:
        normalized_note_text = _normalize_note_value(note_text)
        if not normalized_note_text or not self._supports_dom_waits(page):
            return False

        if await self._is_save_dialog_visible(page):
            try:
                await self._press_keyboard(
                    page=page,
                    key="Escape",
                    step=f"save.dismiss_surface_before_reopen_note_check({list_name})",
                    error_type=GoogleMapsTransientError,
                )
                await self._wait_for_save_dialog_closed(page)
            except GoogleMapsError:
                return False

        current_url = self._read_page_url(page)
        if not current_url:
            return False

        try:
            await self._run_browser_step(
                page=page,
                step=f"save.reopen_place_for_note_check({list_name})",
                action=lambda: page.goto(
                    current_url,
                    wait_until="domcontentloaded",
                ),
                error_type=GoogleMapsTransientError,
            )
            await self._wait_for_timeout(
                page,
                max(int(self._config.sync_delay_seconds * 1000 * 2), self._UI_ACTION_POLL_INTERVAL_MS),
                step=f"save.wait_after_reopen_place_for_note_check({list_name})",
            )
        except GoogleMapsError:
            return False

        saved_state_visible = await self._wait_for_condition(
            page=page,
            timeout_ms=self._NOTE_CONFIRM_TIMEOUT_MS,
            predicate=lambda: self._is_saved_state_visible_on_place_panel(page),
        )
        if not saved_state_visible:
            return False

        if not await self._is_target_note_editor_visible(page=page, list_name=list_name):
            if not await self._attempt_expand_place_panel_note_editor(page=page, list_name=list_name):
                return False

        return await self._wait_for_place_panel_note_text(
            page=page,
            list_name=list_name,
            note_text=normalized_note_text,
        )

    async def _wait_for_place_panel_note_text(self, *, page: Any, list_name: str, note_text: str) -> bool:
        if await self._is_place_panel_note_text_applied(page=page, list_name=list_name, note_text=note_text):
            return True
        if not self._supports_dom_waits(page):
            return False
        return await self._wait_for_condition(
            page=page,
            timeout_ms=self._NOTE_CONFIRM_TIMEOUT_MS,
            predicate=lambda: self._is_place_panel_note_text_applied(
                page=page,
                list_name=list_name,
                note_text=note_text,
            ),
        )

    async def _is_place_panel_note_text_applied(self, *, page: Any, list_name: str, note_text: str) -> bool:
        normalized_note_text = _normalize_note_value(note_text)
        if not normalized_note_text:
            return False
        if self._supports_dom_waits(page):
            return await self._is_scoped_note_text_applied_via_js(
                page=page,
                list_name=list_name,
                note_text=normalized_note_text,
            )
        note_input = await self._first_visible_locator(page, self._SAVE_DIALOG_NOTE_FIELD_SELECTORS)
        if note_input is None:
            return False
        return _note_text_matches(
            actual_value=await self._read_locator_text_value(note_input),
            expected_value=normalized_note_text,
        )

    async def _is_target_note_editor_visible(self, *, page: Any, list_name: str) -> bool:
        if not self._supports_dom_waits(page):
            return await self._first_visible_locator(page, self._SAVE_DIALOG_NOTE_FIELD_SELECTORS) is not None
        try:
            return bool(await page.evaluate(_SCOPED_NOTE_FIELD_JS, {"mode": "visible", "listName": list_name}))
        except Exception:  # noqa: BLE001
            return False

    async def _set_scoped_note_text_via_js(self, *, page: Any, list_name: str, note_text: str) -> bool:
        try:
            return bool(
                await page.evaluate(
                    _SCOPED_NOTE_FIELD_JS,
                    {"mode": "set", "listName": list_name, "noteText": note_text},
                )
            )
        except Exception:  # noqa: BLE001
            return False

    async def _is_scoped_note_text_applied_via_js(self, *, page: Any, list_name: str, note_text: str) -> bool:
        try:
            return bool(
                await page.evaluate(
                    _SCOPED_NOTE_FIELD_JS,
                    {"mode": "matches", "listName": list_name, "noteText": note_text},
                )
            )
        except Exception:  # noqa: BLE001
            return False

    async def _click_save_control_with_retry(self, *, page: Any, list_name: str) -> str:
        last_click_error: GoogleMapsTransientError | None = None
        for attempt in range(1, self._SAVE_CONTROL_CLICK_MAX_ATTEMPTS + 1):
            save_button = await self._resolve_save_button(page)
            if save_button is None:
                if attempt == 1 and await self._try_open_first_result(
                    page=page,
                    query=f"{list_name} save_recover",
                ):
                    continue
                place_controls_snapshot = await self._summarize_save_controls_for_debug(page)
                search_controls_snapshot = await self._summarize_search_controls_for_debug(page)
                raise GoogleMapsTransientError(
                    
                        "Save button not found on place details panel. "
                        f"Visible place controls snapshot: {place_controls_snapshot}. "
                        f"Visible search controls snapshot: {search_controls_snapshot}. "
                        f"Current URL: {self._read_page_url(page)}"
                    
                )

            save_button_summary = await self._normalized_locator_text(save_button) or "<unknown>"
            try:
                await self._click_locator(
                    page=page,
                    locator=save_button,
                    step=f"save.click_save_control({list_name})",
                    timeout_ms=self._SAVE_CONTROL_CLICK_TIMEOUT_MS,
                    error_type=GoogleMapsTransientError,
                    allow_force_click=True,
                )
                await self._wait_for_timeout(
                    page,
                    int(self._config.sync_delay_seconds * 1000),
                    step=f"save.wait_after_save_click({list_name})",
                )
                return save_button_summary
            except GoogleMapsTransientError as exc:
                if not self._is_retryable_click_error(exc):
                    raise
                last_click_error = exc
                if attempt >= self._SAVE_CONTROL_CLICK_MAX_ATTEMPTS:
                    break
                await self._wait_for_timeout(
                    page,
                    self._UI_ACTION_POLL_INTERVAL_MS,
                    step=f"save.retry_click_save_control({list_name})",
                )

        if last_click_error is not None:
            raise last_click_error
        raise GoogleMapsTransientError(
            f"Save control click retry budget exhausted for list '{list_name}'."
        )

    async def _try_apply_note_to_saved_place(self, *, page: Any, list_name: str, note_text: str) -> bool:
        if not note_text:
            return False
        if self._supports_dom_waits(page):
            return await self._set_scoped_note_text_via_js(
                page=page,
                list_name=list_name,
                note_text=note_text,
            )
        note_input = await self._first_visible_locator(page, self._SAVE_DIALOG_NOTE_FIELD_SELECTORS)
        if note_input is not None:
            try:
                # Use the page keyboard instead of locator.fill() so the text
                # goes through the focused Google Maps editor surface.
                await self._click_locator(
                    page=page,
                    locator=note_input,
                    step=f"save.focus_note_field({list_name})",
                    error_type=GoogleMapsTransientError,
                )
                _select_all = "Meta+a" if sys.platform == "darwin" else "Control+a"
                await self._press_keyboard(
                    page=page,
                    key=_select_all,
                    step=f"save.select_all_note_field({list_name})",
                    error_type=GoogleMapsTransientError,
                )
                await self._run_browser_step(
                    page=page,
                    step=f"save.type_note_field({list_name})",
                    action=lambda: page.keyboard.type(note_text),
                    error_type=GoogleMapsTransientError,
                )
                await self._press_locator(
                    page=page,
                    locator=note_input,
                    key="Tab",
                    step=f"save.blur_note_field({list_name})",
                    error_type=GoogleMapsTransientError,
                )
                await self._wait_for_timeout(
                    page,
                    self._UI_ACTION_POLL_INTERVAL_MS,
                    step=f"save.wait_after_note_fill({list_name})",
                )
                return True
            except GoogleMapsError:
                return False
        return False

    async def _ensure_save_dialog_ready_for_note(self, *, page: Any, list_name: str) -> bool:
        import logging as _logging
        _log = _logging.getLogger(__name__)
        if await self._is_target_note_editor_visible(page=page, list_name=list_name):
            _log.debug("[timing] save.ensure_dialog_ready: note_field_immediately_visible")
            return True

        # Fast path: when the save dialog has already auto-closed after saving,
        # all dialog-scoped waits (settle, loader, note editor) would time out
        # fruitlessly.  Go directly to the place-panel note expansion path.
        if not await self._is_save_dialog_visible(page):
            _log.debug("[timing] save.ensure_dialog_ready: dialog_closed_fast_path")
            # The collapsed "Saved in A & B" summary is not enough to prove the
            # target list is saved. Expand place-list details, then inspect the
            # per-list rows/note editors.
            await self._attempt_expand_place_panel_note_editor(page=page, list_name=list_name)
            if not await self._is_target_saved_state_visible_on_place_panel(page=page, list_name=list_name):
                _log.debug("[timing] save.ensure_dialog_ready: waiting_for_saved_state")
                async def _expanded_target_saved_pred() -> bool:
                    if await self._is_target_saved_state_visible_on_place_panel(page=page, list_name=list_name):
                        return True
                    await self._attempt_expand_place_panel_note_editor(page=page, list_name=list_name)
                    return await self._is_target_saved_state_visible_on_place_panel(page=page, list_name=list_name)

                saved_state_visible = await self._wait_for_condition(
                    page=page,
                    timeout_ms=self._NOTE_CONFIRM_TIMEOUT_MS,
                    predicate=_expanded_target_saved_pred,
                )
                if not saved_state_visible:
                    return False
            if await self._is_target_note_editor_visible(page=page, list_name=list_name):
                return True
            if not await self._attempt_expand_place_panel_note_editor(page=page, list_name=list_name):
                return False
            await self._wait_for_timeout(
                page,
                self._UI_ACTION_POLL_INTERVAL_MS,
                step=f"save.wait_after_panel_note_expand({list_name})",
            )
            return await self._is_target_note_editor_visible(page=page, list_name=list_name)

        _ts0 = monotonic()
        settled = await self._wait_for_save_surface_settled_for_note(
            page=page,
            list_name=list_name,
        )
        _ts1 = monotonic()
        _log.debug(f"[timing] save.wait_surface_settled: {(_ts1-_ts0)*1000:.0f}ms  settled={settled!r}")
        if not settled:
            return False
        loader_ok = await self._wait_for_loader_disappear_if_seen(page=page, list_name=list_name)
        _ts2 = monotonic()
        _log.debug(f"[timing] save.wait_loader_disappear: {(_ts2-_ts1)*1000:.0f}ms  ok={loader_ok!r}")
        if not loader_ok:
            return False
        if await self._is_target_note_editor_visible(page=page, list_name=list_name):
            _log.debug("[timing] save.ensure_dialog_ready: note_field_visible_after_settle")
            return True
        _ts3 = monotonic()
        result = await self._wait_for_note_editor_visible(
            page=page,
            list_name=list_name,
            list_selector=await self._resolve_save_dialog_list_selector(page, list_name),
        )
        _ts4 = monotonic()
        _log.debug(f"[timing] save.wait_note_editor_visible: {(_ts4-_ts3)*1000:.0f}ms  result={result!r}")
        if result:
            return True
        # Final fallback: the save surface is settled and loaders have cleared,
        # but we still cannot confirm that a dedicated note editor is visible.
        # Allow the downstream note writer to attempt a DOM-script-based write
        # against any editable fields; if that also fails, verification logic
        # will still raise a NoteWriteError.
        _log.debug("[timing] save.ensure_dialog_ready: note_editor_missing_but_surface_ready")
        return True

    async def _wait_for_loader_disappear_if_seen(self, *, page: Any, list_name: str) -> bool:
        if not self._supports_dom_waits(page):
            return True

        # When the save dialog has already closed (auto-dismiss after save),
        # dialog-scoped loaders and note fields won't appear — skip the wait.
        if not await self._is_save_dialog_visible(page):
            return True

        saw_loader = await self._is_any_selector_visible(
            page,
            self._SAVE_DIALOG_LOADING_INDICATOR_SELECTORS,
            timeout_ms=80,
        )
        if not saw_loader:
            async def _loader_or_note_pred() -> bool:
                return (
                    await self._is_any_selector_visible(
                        page,
                        self._SAVE_DIALOG_LOADING_INDICATOR_SELECTORS,
                        timeout_ms=80,
                    )
                    or await self._is_target_note_editor_visible(page=page, list_name=list_name)
                )

            observed_loader_or_note = await self._wait_for_condition(
                page=page,
                timeout_ms=self._SAVE_DIALOG_LIST_RESOLVE_TIMEOUT_MS,
                predicate=_loader_or_note_pred,
            )
            if not observed_loader_or_note:
                return True
            if await self._is_target_note_editor_visible(page=page, list_name=list_name):
                return True
            saw_loader = await self._is_any_selector_visible(
                page,
                self._SAVE_DIALOG_LOADING_INDICATOR_SELECTORS,
                timeout_ms=80,
            )
        if not saw_loader:
            return True

        async def _loader_gone_pred() -> bool:
            return not await self._is_any_selector_visible(
                page,
                self._SAVE_DIALOG_LOADING_INDICATOR_SELECTORS,
                timeout_ms=80,
            )

        cleared = await self._wait_for_condition(
            page=page,
            timeout_ms=self._SAVE_DIALOG_LIST_RESOLVE_TIMEOUT_MS,
            predicate=_loader_gone_pred,
        )
        if not cleared:
            return False
        await self._wait_for_timeout(
            page,
            self._UI_ACTION_POLL_INTERVAL_MS,
            step=f"save.wait_after_loader_disappear({list_name})",
        )
        return True

    async def _wait_for_note_editor_visible(
        self,
        *,
        page: Any,
        list_name: str,
        list_selector: Any | None,
    ) -> bool:
        if await self._is_target_note_editor_visible(page=page, list_name=list_name):
            return True
        if not self._supports_dom_waits(page):
            return False

        async def _note_field_visible_pred() -> bool:
            return await self._is_target_note_editor_visible(page=page, list_name=list_name)

        appeared_without_expand = await self._wait_for_condition(
            page=page,
            timeout_ms=self._SAVE_DIALOG_LIST_RESOLVE_TIMEOUT_MS,
            predicate=_note_field_visible_pred,
        )
        if appeared_without_expand:
            return True
        if not await self._attempt_expand_note_surface_for_note(
            page=page,
            list_name=list_name,
            list_selector=list_selector,
        ):
            return False
        return await self._wait_for_condition(
            page=page,
            timeout_ms=self._SAVE_DIALOG_LIST_RESOLVE_TIMEOUT_MS,
            predicate=_note_field_visible_pred,
        )

    async def _attempt_expand_note_surface_for_note(
        self,
        *,
        page: Any,
        list_name: str,
        list_selector: Any | None,
    ) -> bool:
        if list_selector is not None:
            if await self._is_save_dialog_visible(page) and await self._attempt_expand_selected_list_entry_for_note(
                page=page,
                list_name=list_name,
                list_selector=list_selector,
            ):
                return True
            # Fallback: when the list selector/menu remains open but does not expose
            # a reliable per-row note/details control, close it first so that the
            # place-panel "Saved in …" control becomes interactable.
            await self._dismiss_save_surface_before_place_panel_note_expand(page=page, list_name=list_name)
        return await self._attempt_expand_place_panel_note_editor(page=page, list_name=list_name)

    async def _dismiss_save_surface_before_place_panel_note_expand(self, *, page: Any, list_name: str) -> None:
        try:
            await self._press_keyboard(
                page=page,
                key="Escape",
                step=f"save.dismiss_surface_before_note_expand({list_name})",
                error_type=GoogleMapsTransientError,
            )
            await self._wait_for_timeout(
                page,
                self._UI_ACTION_POLL_INTERVAL_MS,
                step=f"save.wait_after_dismiss_before_note_expand({list_name})",
            )
            await self._wait_for_save_dialog_closed(page)
        except GoogleMapsError:
            return

    async def _attempt_expand_selected_list_entry_for_note(
        self,
        *,
        page: Any,
        list_name: str,
        list_selector: Any,
    ) -> bool:
        # Clicking the list row itself would toggle the save state off, so we
        # instead try to find a dedicated "note/details" control associated with
        # the selected list entry and click that control.
        #
        # The DOM structure for the save dialog and the main panel is not
        # stable enough to rely on a single CSS selector for this per-row
        # control, so we use a DOM script to:
        #
        # - Locate the row whose label matches the target list name.
        # - Within that row (and its immediate ancestors), search for buttons
        #   whose accessible label or text includes any of the note keywords
        #   we already use for the note editor.
        # - Click the first matching control if it is visible.
        try:
            clicked = bool(
                await page.evaluate(
                    """({ listName, noteKeywords }) => {
                        const normalize = (value) => String(value || '')
                          .toLowerCase()
                          .replace(/\\s+/g, ' ')
                          .trim();

                        const isVisible = (element) => {
                          if (!element) return false;
                          const style = window.getComputedStyle(element);
                          if (!style || style.visibility === 'hidden' || style.display === 'none') {
                            return false;
                          }
                          const rect = element.getBoundingClientRect();
                          return rect.width > 0 && rect.height > 0;
                        };

                        const readLabel = (element) => normalize(
                          [
                            (element.getAttribute && element.getAttribute('aria-label')) || '',
                            (element.getAttribute && element.getAttribute('placeholder')) || '',
                            element.innerText || '',
                            element.textContent || '',
                          ].join(' ')
                        );

                        const normalizedTarget = normalize(listName);
                        const keywordSet = new Set((noteKeywords || []).map(normalize).filter(Boolean));
                        if (!normalizedTarget || !keywordSet.size) return false;

                        const surfaceRoots = Array.from(
                          document.querySelectorAll("div[role='dialog'], [role='menu'], div[role='main']")
                        ).filter(isVisible);

                        const rowCandidatesSelector = (
                          "[role='checkbox'], [role='menuitemcheckbox'], [role='menuitemradio'], " +
                          "button, [role='button'], [role='menuitem']"
                        );

                        const buttonSelector = (
                          "button, [role='button'], [role='menuitem'], " +
                          "[role='menuitemcheckbox'], [role='menuitemradio']"
                        );

                        for (const surfaceRoot of surfaceRoots) {
                          const rowCandidates = Array.from(
                            surfaceRoot.querySelectorAll(rowCandidatesSelector)
                          );
                          for (const rowCandidate of rowCandidates) {
                            const rowText = readLabel(rowCandidate);
                            if (!rowText || !rowText.includes(normalizedTarget)) continue;

                            const ancestry = [
                              rowCandidate,
                              rowCandidate.parentElement,
                              rowCandidate.parentElement ? rowCandidate.parentElement.parentElement : null,
                            ];

                            for (const ancestor of ancestry) {
                              if (!ancestor) continue;
                              const buttons = Array.from(
                                ancestor.querySelectorAll(buttonSelector)
                              );
                              for (const button of buttons) {
                                if (button === rowCandidate) continue;
                                const label = readLabel(button);
                                if (!label) continue;
                                const hasKeyword = Array.from(keywordSet).some(
                                  (keyword) => label.includes(keyword)
                                );
                                if (!hasKeyword || !isVisible(button)) continue;
                                button.click();
                                return true;
                              }
                            }
                          }
                        }

                        return false;
                    }""",
                    {
                        "listName": list_name,
                        "noteKeywords": self._SAVE_DIALOG_NOTE_KEYWORDS,
                    },
                )
            )
        except Exception:  # noqa: BLE001
            return False

        if not clicked:
            return False

        try:
            await self._wait_for_timeout(
                page,
                self._UI_ACTION_POLL_INTERVAL_MS,
                step=f"save.wait_after_expand_selected_list_entry_for_note({list_name})",
            )
        except GoogleMapsError:
            return False

        return True

    async def _attempt_expand_place_panel_note_editor(self, *, page: Any, list_name: str) -> bool:
        if await self._is_place_panel_list_details_expanded(page=page):
            return True

        expand_control = await self._resolve_place_panel_details_expand_control(page=page)
        if expand_control is None:
            # Fallback: some UIs expose the "Saved in …" chip itself (matched by
            # SAVE_PANEL_SAVED_STATE_SELECTORS) as the control that expands the
            # note editor or list-details surface. When we cannot find a
            # dedicated "details" button, click the saved-state chip instead.
            saved_state_container = await self._first_visible_locator(page, self._SAVE_PANEL_SAVED_STATE_SELECTORS)
            if saved_state_container is not None:
                # SAVE_PANEL_SAVED_STATE_SELECTORS may match a group/region div
                # containing the actual clickable button. Try to find the button inside.
                try:
                    expand_control = saved_state_container.locator("button").first
                    if await expand_control.count() == 0:
                        expand_control = saved_state_container
                except Exception:  # noqa: BLE001
                    expand_control = saved_state_container
            else:
                expand_control = None
        if expand_control is None:
            return False
        try:
            await self._click_locator(
                page=page,
                locator=expand_control,
                step=f"save.expand_place_panel_note_editor({list_name})",
                error_type=GoogleMapsTransientError,
                allow_force_click=True,
            )
            await self._wait_for_timeout(
                page,
                self._UI_ACTION_POLL_INTERVAL_MS,
                step=f"save.wait_after_expand_place_panel_note_editor({list_name})",
            )
            return True
        except GoogleMapsError:
            if not self._supports_dom_waits(page):
                return False
            if not await self._click_place_panel_details_control_via_js(page=page):
                return False
            try:
                await self._wait_for_timeout(
                    page,
                    self._UI_ACTION_POLL_INTERVAL_MS,
                    step=f"save.wait_after_expand_place_panel_note_editor_js({list_name})",
                )
            except GoogleMapsError:
                return False
            return True

    async def _is_place_panel_list_details_expanded(self, *, page: Any) -> bool:
        if not hasattr(page, "locator"):
            return False
        return await self._is_any_selector_visible(
            page,
            (
                "div[role='main'] button[aria-label*='Hide place lists details' i]",
                "div[role='main'] [role='button'][aria-label*='Hide place lists details' i]",
            ),
            timeout_ms=80,
        )

    async def _resolve_place_panel_details_expand_control(self, *, page: Any) -> Any | None:
        if not hasattr(page, "locator"):
            return await self._first_visible_locator(page, self._SAVE_PANEL_NOTE_EXPAND_SELECTORS)
        locator_group = page.locator(", ".join(self._SAVE_PANEL_NOTE_EXPAND_SELECTORS))
        try:
            candidate_count = min(await locator_group.count(), 40)
        except Exception:  # noqa: BLE001
            return None
        for index in range(candidate_count):
            candidate = locator_group.nth(index)
            try:
                if not await candidate.is_visible():
                    continue
            except Exception:  # noqa: BLE001
                continue
            normalized_text = await self._normalized_locator_text(candidate)
            if "hide place lists details" in normalized_text:
                return None
            if (
                "show place lists details" in normalized_text
                or "add note" in normalized_text
                or "saved in" in normalized_text
            ):
                return candidate
        return None

    async def _click_place_panel_details_control_via_js(self, *, page: Any) -> bool:
        try:
            return bool(
                await page.evaluate(
                    """() => {
                        const isVisible = (element) => {
                          if (!element) return false;
                          const style = window.getComputedStyle(element);
                          if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
                          const rect = element.getBoundingClientRect();
                          return rect.width > 0 && rect.height > 0;
                        };
                        const normalize = (value) => String(value || '')
                          .toLowerCase()
                          .replace(/\\s+/g, ' ')
                          .trim();
                        const controls = Array.from(
                          document.querySelectorAll(
                            "div[role='main'] button, div[role='main'] [role='button']"
                          )
                        );
                        for (const control of controls) {
                          if (!isVisible(control)) continue;
                          const label = normalize(
                            [
                              control.getAttribute('aria-label') || '',
                              control.getAttribute('data-value') || '',
                              control.innerText || '',
                            ].join(' ')
                          );
                          if (!label.includes('show place lists details')) continue;
                          control.scrollIntoView({ block: 'center', inline: 'center' });
                          const rect = control.getBoundingClientRect();
                          const eventInit = {
                            bubbles: true,
                            cancelable: true,
                            view: window,
                            clientX: rect.left + rect.width / 2,
                            clientY: rect.top + rect.height / 2,
                          };
                          const eventTypes = [
                            'pointerover', 'pointerenter', 'mouseover', 'mouseenter',
                            'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click',
                          ];
                          for (const type of eventTypes) {
                            const EventCtor = type.startsWith('pointer') ? PointerEvent : MouseEvent;
                            control.dispatchEvent(new EventCtor(type, eventInit));
                          }
                          return true;
                        }
                        return false;
                    }"""
                )
            )
        except Exception:  # noqa: BLE001
            return False

    async def _wait_for_save_surface_settled_for_note(self, *, page: Any, list_name: str) -> bool:
        import logging as _logging
        _log = _logging.getLogger(__name__)
        _ts0 = monotonic()
        if await self._is_save_surface_ready_for_note(page=page, list_name=list_name):
            _log.debug("[timing] save.surface_settled: immediately_ready")
            return True
        if not self._supports_dom_waits(page):
            return False

        settled = await self._wait_for_condition(
            page=page,
            timeout_ms=self._SAVE_DIALOG_LIST_RESOLVE_TIMEOUT_MS,
            predicate=lambda: self._is_save_surface_ready_for_note(
                page=page,
                list_name=list_name,
            ),
        )
        _ts1 = monotonic()
        _log.debug(f"[timing] save.surface_settled.first_wait: {(_ts1-_ts0)*1000:.0f}ms  settled={settled!r}")
        if not settled:
            try:
                await self._click_save_control_with_retry(page=page, list_name=list_name)
            except GoogleMapsError:
                _log.debug("[timing] save.surface_settled: click_save_retry_failed")
                return False
            _ts2 = monotonic()
            _log.debug(f"[timing] save.surface_settled.retry_click: {(_ts2-_ts1)*1000:.0f}ms")
            settled = await self._wait_for_condition(
                page=page,
                timeout_ms=self._SAVE_DIALOG_LIST_RESOLVE_TIMEOUT_MS,
                predicate=lambda: self._is_save_surface_ready_for_note(
                    page=page,
                    list_name=list_name,
                ),
            )
            _ts3 = monotonic()
            _log.debug(f"[timing] save.surface_settled.second_wait: {(_ts3-_ts2)*1000:.0f}ms  settled={settled!r}")
            if not settled:
                return False

        # Add one short settle cycle after loader disappears to avoid premature note probing.
        await self._wait_for_timeout(
            page,
            self._UI_ACTION_POLL_INTERVAL_MS,
            step=f"save.wait_for_note_surface_settle({list_name})",
        )
        return await self._is_save_surface_ready_for_note(
            page=page,
            list_name=list_name,
        )

    async def _is_save_surface_ready_for_note(self, *, page: Any, list_name: str) -> bool:
        if await self._is_target_note_editor_visible(page=page, list_name=list_name):
            return True
        if not await self._is_save_dialog_visible(page):
            await self._attempt_expand_place_panel_note_editor(page=page, list_name=list_name)
            return await self._is_target_saved_state_visible_on_place_panel(page=page, list_name=list_name)
        list_selector = await self._resolve_save_dialog_list_selector(page, list_name)
        if list_selector is None:
            return False
        return await self._read_locator_selection_state(list_selector) is True

    async def _is_saved_state_visible_on_place_panel(self, page: Any) -> bool:
        return await self._is_any_selector_visible(
            page,
            self._SAVE_PANEL_SAVED_STATE_SELECTORS,
            timeout_ms=80,
        )

    async def _is_target_saved_state_visible_on_place_panel(self, *, page: Any, list_name: str) -> bool:
        if not hasattr(page, "locator"):
            panel_saved_list_name = await self._resolve_panel_saved_list_name(page)
            return self._list_name_match_score(
                list_name=list_name,
                candidate_text=panel_saved_list_name,
            ) >= 2
        locator_group = page.locator(", ".join(self._SAVE_PANEL_SAVED_STATE_SELECTORS))
        try:
            candidate_count = min(await locator_group.count(), 40)
        except Exception:  # noqa: BLE001
            candidate_count = 0
        for index in range(candidate_count):
            locator = locator_group.nth(index)
            try:
                if not await locator.is_visible():
                    continue
            except Exception:  # noqa: BLE001
                continue
            if await self._locator_list_name_match_score(locator=locator, list_name=list_name) >= 2:
                return True
        return False

    async def _resolve_panel_saved_list_name(self, page: Any) -> str:
        """Extract the list name from the panel 'Saved in ...' aria-label, or ''."""
        locator = await self._first_visible_locator(page, self._SAVE_PANEL_SAVED_STATE_SELECTORS)
        if locator is None:
            return ""
        try:
            aria_label = await locator.get_attribute("aria-label", timeout=2000)
        except Exception:  # noqa: BLE001
            return ""
        if not aria_label:
            return ""
        _SAVED_IN_PREFIX = "Saved in "
        lower_label = aria_label.lower()
        lower_prefix = _SAVED_IN_PREFIX.lower()
        if lower_prefix in lower_label:
            idx = lower_label.index(lower_prefix)
            return aria_label[idx + len(_SAVED_IN_PREFIX) :].strip()
        return ""

    async def _wait_for_note_text_applied(self, *, page: Any, list_name: str, note_text: str) -> bool:
        if not note_text:
            return False
        if (
            await self._is_note_text_applied(page=page, list_name=list_name, note_text=note_text)
            and not await self._is_any_selector_visible(
                page,
                self._SAVE_DIALOG_LOADING_INDICATOR_SELECTORS,
                timeout_ms=80,
            )
        ):
            return True
        if not self._supports_dom_waits(page):
            return False
        async def _note_applied_pred() -> bool:
            return (
                await self._is_note_text_applied(
                    page=page,
                    list_name=list_name,
                    note_text=note_text,
                )
                and not await self._is_any_selector_visible(
                    page,
                    self._SAVE_DIALOG_LOADING_INDICATOR_SELECTORS,
                    timeout_ms=80,
                )
            )

        return await self._wait_for_condition(
            page=page,
            timeout_ms=self._NOTE_CONFIRM_TIMEOUT_MS,
            predicate=_note_applied_pred,
        )

    async def _is_note_text_applied(self, *, page: Any, list_name: str, note_text: str) -> bool:
        normalized_note_text = _normalize_note_value(note_text)
        if not normalized_note_text:
            return False
        if self._supports_dom_waits(page):
            return await self._is_scoped_note_text_applied_via_js(
                page=page,
                list_name=list_name,
                note_text=normalized_note_text,
            )
        if not self._supports_dom_waits(page):
            note_input = await self._first_visible_locator(page, self._SAVE_DIALOG_NOTE_FIELD_SELECTORS)
            if note_input is None:
                return False
            return _note_text_matches(
                actual_value=await self._read_locator_text_value(note_input),
                expected_value=normalized_note_text,
            )
        return False

    async def _read_locator_text_value(self, locator: Any) -> str:
        attribute_readers = (
            lambda: locator.get_attribute("value"),
            lambda: locator.get_attribute("aria-label"),
            lambda: locator.get_attribute("placeholder"),
        )
        for reader in attribute_readers:
            try:
                raw_value = await reader()
            except Exception:  # noqa: BLE001
                raw_value = None
            if raw_value:
                return str(raw_value)

        value_reader: Any = getattr(locator, "input_value", None)
        if callable(value_reader):
            try:
                raw_value = await cast(Any, value_reader())
            except Exception:  # noqa: BLE001
                raw_value = None
            if raw_value:
                return str(raw_value)

        for reader_attr in ("inner_text", "text_content"):
            reader: Any = getattr(locator, reader_attr, None)
            if not callable(reader):
                continue
            try:
                raw_value = await cast(Any, reader())
            except Exception:  # noqa: BLE001
                raw_value = None
            if raw_value:
                return str(raw_value)
        return ""

    async def _fill_locator(
        self,
        *,
        page: Any,
        locator: Any,
        value: str,
        step: str,
        error_type: type[GoogleMapsError] = GoogleMapsTransientError,
    ) -> None:
        await self._run_browser_step(
            page=page,
            step=step,
            action=lambda: locator.fill(value),
            error_type=error_type,
        )

    async def _press_locator(
        self,
        *,
        page: Any,
        locator: Any,
        key: str,
        step: str,
        error_type: type[GoogleMapsError] = GoogleMapsTransientError,
    ) -> None:
        await self._run_browser_step(
            page=page,
            step=step,
            action=lambda: locator.press(key),
            error_type=error_type,
        )

    async def _press_keyboard(
        self,
        *,
        page: Any,
        key: str,
        step: str,
        error_type: type[GoogleMapsError] = GoogleMapsTransientError,
    ) -> None:
        await self._run_browser_step(
            page=page,
            step=step,
            action=lambda: page.keyboard.press(key),
            error_type=error_type,
        )

    async def _resolve_save_button(self, page: Any) -> Any | None:
        for selector in self._SAVE_BUTTON_SELECTORS:
            locator = page.locator(selector)
            if await locator.count() == 0:
                continue
            candidate_count = min(await locator.count(), 120)
            for index in range(candidate_count):
                candidate = locator.nth(index)
                if not await self._is_valid_save_button_candidate(candidate):
                    continue
                return candidate
        return None

    async def _is_valid_save_button_candidate(self, candidate: Any) -> bool:
        if not await self._is_locator_interactable(candidate):
            return False
        normalized_text = await self._normalized_locator_text(candidate)
        if not normalized_text:
            return False
        if not any(token in normalized_text for token in self._SAVE_CONTROL_TEXT_TOKENS):
            return False

        try:
            has_navigation_rail_ancestor = bool(
                await candidate.evaluate(
                    """(node) => Boolean(
                        node.closest('[jsaction*="navigationrail"], ul.ODXihb, [data-bundle-id]')
                    )"""
                )
            )
        except Exception:  # noqa: BLE001
            has_navigation_rail_ancestor = False
        if has_navigation_rail_ancestor:
            return False

        # Reject obvious Saved panel navigation controls.
        if "navigationrail" in normalized_text:
            return False
        if "saved places" in normalized_text:
            return False
        if _is_saved_only_control_label(normalized_text):
            try:
                has_place_panel_ancestor = bool(
                    await candidate.evaluate(
                        """(node) => Boolean(
                            node.closest(
                              "div[role='main'][aria-label*='place' i], " +
                              "div[role='main'][aria-label*='details' i], " +
                              "div[role='main']:not([aria-label*='your places' i])"
                            )
                        )"""
                    )
                )
            except Exception:  # noqa: BLE001
                has_place_panel_ancestor = False
            if not has_place_panel_ancestor:
                return False
        return True

    async def _wait_for_saved_panel_ready(self, page: Any) -> None:
        if not self._supports_dom_waits(page):
            return
        await self._wait_for_condition(
            page=page,
            timeout_ms=self._SAVED_PANEL_READY_TIMEOUT_MS,
            predicate=lambda: self._is_saved_panel_ready(page),
        )

    async def _wait_for_list_creation_surface(self, page: Any) -> None:
        if not self._supports_dom_waits(page):
            return
        await self._wait_for_condition(
            page=page,
            timeout_ms=self._LIST_CREATION_READY_TIMEOUT_MS,
            predicate=lambda: self._is_list_creation_surface_visible(page),
        )

    async def _wait_for_opened_list_context(self, page: Any, *, list_name: str) -> None:
        if not self._supports_dom_waits(page):
            return
        async def _list_context_pred() -> bool:
            return (
                await self._active_list_title_matches(page, list_name)
                or await self._is_add_place_input_visible(page)
            )

        await self._wait_for_condition(
            page=page,
            timeout_ms=self._LIST_OPEN_READY_TIMEOUT_MS,
            predicate=_list_context_pred,
        )

    async def _wait_for_save_dialog_open(self, page: Any) -> bool:
        if not self._supports_dom_waits(page):
            return True
        return await self._wait_for_condition(
            page=page,
            timeout_ms=self._SAVE_DIALOG_READY_TIMEOUT_MS,
            predicate=lambda: self._is_save_dialog_visible(page),
        )

    async def _wait_for_save_dialog_closed(self, page: Any) -> None:
        if not self._supports_dom_waits(page):
            return
        async def _dialog_closed_pred() -> bool:
            return not await self._is_save_dialog_visible(page)

        await self._wait_for_condition(
            page=page,
            timeout_ms=self._SAVE_DIALOG_CLOSE_TIMEOUT_MS,
            predicate=_dialog_closed_pred,
        )

    async def _wait_for_search_input_ready(self, page: Any, search_box: Any) -> None:
        if not self._supports_dom_waits(page):
            return
        await self._wait_for_condition(
            page=page,
            timeout_ms=self._SEARCH_INPUT_READY_TIMEOUT_MS,
            predicate=lambda: self._is_locator_interactable(search_box),
        )

    async def _is_locator_interactable(self, locator: Any) -> bool:
        try:
            is_visible: Any = getattr(locator, "is_visible", None)
            if callable(is_visible) and not bool(await cast(Any, is_visible())):
                return False
        except Exception:  # noqa: BLE001
            return False
        try:
            is_enabled: Any = getattr(locator, "is_enabled", None)
            if callable(is_enabled) and not bool(await cast(Any, is_enabled())):
                return False
        except Exception:  # noqa: BLE001
            return False
        return True

    async def _wait_for_save_dialog_list_selector(self, page: Any, list_name: str) -> Any | None:
        import logging as _logging
        _log = _logging.getLogger(__name__)

        if not self._supports_dom_waits(page):
            return await self._resolve_save_dialog_list_selector(page, list_name)

        _t_start = monotonic()
        _poll = 0
        deadline = monotonic() + (max(self._SAVE_DIALOG_LIST_RESOLVE_TIMEOUT_MS, 1) / 1000)
        while monotonic() <= deadline:
            _tp = monotonic()
            list_selector = await self._resolve_save_dialog_list_selector(page, list_name)
            _log.debug(
                f"[timing] save.resolve_list_selector poll={_poll} "
                f"took={( monotonic()-_tp)*1000:.0f}ms found={list_selector is not None}"
            )
            _poll += 1
            if list_selector is not None:
                return list_selector
            await self._wait_for_timeout(
                page,
                self._UI_ACTION_POLL_INTERVAL_MS,
                step=f"save.resolve_list_selector.poll({list_name})",
            )
            if not await self._is_any_selector_visible(
                page,
                self._SAVE_DIALOG_LOADING_INDICATOR_SELECTORS,
                timeout_ms=80,
            ):
                # Give one additional short settle cycle after loading disappears.
                await self._wait_for_timeout(
                    page,
                    self._UI_ACTION_POLL_INTERVAL_MS,
                    step=f"save.resolve_list_selector.settle({list_name})",
                )
        _log.debug(f"[timing] save.resolve_list_selector TIMEOUT after {(monotonic()-_t_start)*1000:.0f}ms polls={_poll}")
        return await self._resolve_save_dialog_list_selector(page, list_name)

    async def _wait_for_list_selection_applied(self, page: Any, list_name: str, list_selector: Any) -> bool:
        pre_click_state = await self._read_locator_selection_state(list_selector)
        if pre_click_state is True:
            return True

        if not self._supports_dom_waits(page):
            return True

        return await self._wait_for_condition(
            page=page,
            timeout_ms=self._SAVE_DIALOG_LIST_RESOLVE_TIMEOUT_MS,
            predicate=lambda: self._did_list_selection_apply(page, list_name, list_selector),
        )

    async def _did_list_selection_apply(self, page: Any, list_name: str, list_selector: Any) -> bool:
        # Check dialog visibility *first*.  When the save dialog auto-closes
        # after a successful click the original ``list_selector`` locator
        # points at a detached DOM node.  Playwright's ``get_attribute()``
        # will block until its own action timeout (30 s by default) waiting
        # for the element to reappear, causing the outer poll loop to hang
        # far past the intended deadline.
        if not await self._is_save_dialog_visible(page):
            await self._attempt_expand_place_panel_note_editor(page=page, list_name=list_name)
            return await self._is_target_saved_state_visible_on_place_panel(page=page, list_name=list_name)

        direct_state = await self._read_locator_selection_state(list_selector)
        if direct_state is True:
            return True

        resolved_again = await self._resolve_save_dialog_list_selector(page, list_name)
        if resolved_again is None:
            return False
        return await self._read_locator_selection_state(resolved_again) is True

    async def _read_locator_selection_state(self, locator: Any) -> bool | None:
        for attribute_name in ("aria-checked", "aria-selected", "data-selected", "data-state"):
            try:
                raw_value = await locator.get_attribute(attribute_name, timeout=2000)
            except Exception:  # noqa: BLE001
                raw_value = None
            if raw_value is None:
                continue
            normalized = str(raw_value).strip().lower()
            if normalized in {"true", "1", "yes", "on", "checked", "selected"}:
                return True
            if normalized in {"false", "0", "no", "off", "unchecked", "unselected"}:
                return False
        return None

    async def _is_saved_panel_ready(self, page: Any) -> bool:
        if await self._first_visible_locator(page, self._LISTS_TAB_SELECTORS) is not None:
            return True
        if await self._first_visible_locator(page, self._SAVED_VIEW_MORE_SELECTORS) is not None:
            return True
        if await self._locate_new_list_button(page) is not None:
            return True
        return await self._is_any_selector_visible(
            page,
            ("div[role='main'][aria-label*='Your places']",),
            timeout_ms=150,
        )

    async def _is_list_creation_surface_visible(self, page: Any) -> bool:
        if await self._resolve_inline_list_name_input(page) is not None:
            return True
        list_name_input = await self._first_visible_locator(page, self._LIST_NAME_INPUT_SELECTORS)
        if list_name_input is not None and await self._is_list_name_input_candidate(list_name_input):
            return True
        return await self._first_visible_locator(page, self._LIST_CREATION_ENTRY_SELECTORS) is not None

    async def _is_add_place_input_visible(self, page: Any) -> bool:
        return await self._first_visible_locator(page, self._ADD_PLACE_INPUT_SELECTORS) is not None

    async def _is_save_dialog_visible(self, page: Any) -> bool:
        try:
            return bool(
                await page.evaluate(
                    """() => {
                        const isVisible = (element) => {
                          if (!element) return false;
                          const style = window.getComputedStyle(element);
                          if (!style || style.visibility === 'hidden' || style.display === 'none') {
                            return false;
                          }
                          const rect = element.getBoundingClientRect();
                          return rect.width > 0 && rect.height > 0;
                        };
                        const normalize = (value) => String(value || '')
                          .toLowerCase()
                          .replace(/\\s+/g, ' ')
                          .trim();
                        const hasListSelectionControls = (root) => Boolean(
                          root && root.querySelector(
                            "[role='checkbox'], [role='menuitemcheckbox'], [role='menuitemradio'], " +
                            "[role='menuitem'][aria-checked], [aria-checked='true'], [aria-checked='false']"
                          )
                        );
                        const hasNoteEditor = (root) => Boolean(
                          root && root.querySelector(
                            "textarea, [contenteditable='true'], [role='textbox'], " +
                            "input[aria-label*='note' i], input[placeholder*='note' i]"
                          )
                        );
                        const hasSaveToken = (root) => {
                          if (!root) return false;
                          const text = normalize(
                            (root.getAttribute('aria-label') || '') + ' ' + (root.innerText || '')
                          );
                          if (!text) return false;
                          const saveTokens = [
                            "save", "saved", "list", "lists",
                          ];
                          return saveTokens.some((token) => text.includes(token));
                        };

                        const dialog = document.querySelector("div[role='dialog']");
                        if (
                          dialog &&
                          isVisible(dialog) &&
                          (hasListSelectionControls(dialog) || (hasSaveToken(dialog) && hasNoteEditor(dialog)))
                        ) {
                          return true;
                        }

                        const menus = document.querySelectorAll("[role='menu']");
                        for (const menu of menus) {
                          if (!isVisible(menu)) continue;
                          const matchesSaveSurface = hasSaveToken(menu);
                          if (!matchesSaveSurface) continue;
                          if (hasListSelectionControls(menu) || hasNoteEditor(menu)) return true;
                        }
                        return false;
                    }"""
                )
            )
        except Exception:  # noqa: BLE001
            return await self._is_any_selector_visible(
                page,
                self._SAVE_DIALOG_INTERACTIVE_SELECTORS,
                timeout_ms=150,
            )

    async def _wait_for_condition(
        self,
        *,
        page: Any,
        timeout_ms: int,
        predicate: Callable[[], bool | Awaitable[bool]],
    ) -> bool:
        if await self._safe_predicate(predicate):
            return True
        poll_interval_ms = max(
            self._UI_ACTION_POLL_INTERVAL_MS,
            int(self._config.sync_delay_seconds * 1000),
        )
        deadline = monotonic() + (max(timeout_ms, 1) / 1000)
        while True:
            remaining_ms = (deadline - monotonic()) * 1000
            if remaining_ms <= 0:
                return False
            await self._wait_for_timeout(
                page,
                min(poll_interval_ms, max(int(remaining_ms), 1)),
                step="wait_for_condition.poll",
            )
            if await self._safe_predicate(predicate):
                return True
            if monotonic() > deadline:
                return False

    async def _safe_predicate(self, predicate: Callable[[], bool | Awaitable[bool]]) -> bool:
        try:
            result = predicate()
            if asyncio.iscoroutine(result):
                result = await result
            return bool(result)
        except Exception:  # noqa: BLE001
            return False

    def _supports_dom_waits(self, page: Any) -> bool:
        return callable(getattr(page, "evaluate", None))

    async def _capture_search_panel_state(self, page: Any) -> _SearchPanelState:
        return _SearchPanelState(
            page_url=self._read_page_url(page),
            title_text=await self._extract_text(page, self._PLACE_TITLE_SELECTORS),
            first_result_signature=await self._read_first_result_signature(page),
            no_results_visible=await self._is_no_results_indicator_visible(page),
            loading_visible=await self._is_search_loading_indicator_visible(page),
        )

    async def _wait_for_search_outcome(
        self,
        *,
        page: Any,
        query: str,
        previous_state: _SearchPanelState,
    ) -> str:
        poll_interval_ms = max(
            self._SEARCH_OUTCOME_POLL_INTERVAL_MS,
            int(self._config.sync_delay_seconds * 1000),
        )
        settle_delay_ms = max(
            self._SEARCH_OUTCOME_MIN_SETTLE_MS,
            int(self._config.sync_delay_seconds * 1000 * 2),
        )
        timeout_ms = self._resolve_search_outcome_timeout_ms()
        deadline = monotonic() + (timeout_ms / 1000)
        elapsed_ms = 0
        observed_post_submit_loading = False
        loading_settle_cycle_pending = False
        loading_stuck_threshold_ms = max(settle_delay_ms * 8, 3000)

        while monotonic() <= deadline:
            current_state = await self._capture_search_panel_state(page)

            url_changed = bool(
                current_state.page_url and current_state.page_url != previous_state.page_url
            )

            has_fresh_result_list = bool(
                current_state.first_result_signature
                and (
                    current_state.first_result_signature != previous_state.first_result_signature
                    or (url_changed and elapsed_ms >= settle_delay_ms)
                )
            )
            if has_fresh_result_list:
                return self._SEARCH_OUTCOME_RESULT_READY
            has_stable_result_list_after_settle = bool(
                current_state.first_result_signature
                and current_state.first_result_signature == previous_state.first_result_signature
                and observed_post_submit_loading
                and elapsed_ms >= settle_delay_ms
                and not current_state.no_results_visible
            )
            if has_stable_result_list_after_settle:
                return self._SEARCH_OUTCOME_RESULT_READY

            has_fresh_place_details = bool(
                current_state.title_text
                and (
                    current_state.title_text != previous_state.title_text
                    or (url_changed and elapsed_ms >= settle_delay_ms)
                )
            )
            if has_fresh_place_details:
                return self._SEARCH_OUTCOME_RESULT_READY
            has_stable_place_details_after_settle = bool(
                current_state.title_text
                and current_state.title_text == previous_state.title_text
                and observed_post_submit_loading
                and elapsed_ms >= settle_delay_ms
                and not current_state.no_results_visible
            )
            if has_stable_place_details_after_settle:
                return self._SEARCH_OUTCOME_RESULT_READY

            # Single-result searches: Google Maps navigates directly to the place
            # detail page without showing a results list. Detect this by checking
            # if the URL has changed to a /maps/place/ URL, which means the result
            # is ready even if title_text hasn't loaded yet.
            if url_changed and "/maps/place/" in (current_state.page_url or ""):
                return self._SEARCH_OUTCOME_RESULT_READY

            if current_state.loading_visible:
                observed_post_submit_loading = True
                loading_settle_cycle_pending = True
                if url_changed and elapsed_ms >= loading_stuck_threshold_ms:
                    return self._SEARCH_OUTCOME_RESULT_READY
                await self._wait_for_timeout(
                    page,
                    poll_interval_ms,
                    step=f"search.wait_for_outcome.loading_poll({query})",
                )
                elapsed_ms += poll_interval_ms
                continue
            if loading_settle_cycle_pending:
                loading_settle_cycle_pending = False
                await self._wait_for_timeout(
                    page,
                    poll_interval_ms,
                    step=f"search.wait_for_outcome.settle_poll({query})",
                )
                elapsed_ms += poll_interval_ms
                continue

            if current_state.no_results_visible:
                # Always wait for settle_delay_ms before accepting a no-results state
                # to ensure we aren't seeing stale data from a previous search or
                # a transient UI state during navigation.
                # Also guard against false positives: if the URL already points to a
                # place detail page (/maps/place/), the search succeeded even if
                # "no results" text appears elsewhere on the page (e.g. in reviews).
                place_url = current_state.page_url or ""
                if url_changed and "/maps/place/" in place_url:
                    return self._SEARCH_OUTCOME_RESULT_READY
                if elapsed_ms >= settle_delay_ms:
                    return self._SEARCH_OUTCOME_NO_RESULTS

            await self._wait_for_timeout(
                page,
                poll_interval_ms,
                step=f"search.wait_for_outcome.poll({query})",
            )
            elapsed_ms += poll_interval_ms

        controls_snapshot = await self._summarize_search_controls_for_debug(page)
        loading_hint = (
            "Search loading indicator remained visible. "
            if observed_post_submit_loading
            else ""
        )
        raise GoogleMapsTransientError(
            
                f"Timed out waiting for Google Maps search outcome for query '{query}'. "
                f"{loading_hint}"
                f"Visible search controls snapshot: {controls_snapshot}"
            
        )

    def _resolve_search_outcome_timeout_ms(self) -> int:
        return min(
            max(
                self._SEARCH_OUTCOME_TIMEOUT_MS,
                int(self._config.sync_delay_seconds * 1000 * 20),
            ),
            self._SEARCH_OUTCOME_MAX_TIMEOUT_MS,
        )

    def _read_page_url(self, page: Any) -> str:
        raw_url = getattr(page, "url", "")
        if callable(raw_url):
            try:
                raw_url = raw_url()
            except Exception:  # noqa: BLE001
                return ""
        if not raw_url:
            return ""
        return str(raw_url).strip()

    async def _read_first_result_signature(self, page: Any) -> str:
        first_result_locator = await self._first_matching_locator(page, self._FIRST_RESULT_SELECTORS)
        if first_result_locator is None:
            return ""
        try:
            if await first_result_locator.count() == 0:
                return ""
        except Exception:  # noqa: BLE001
            return ""

        signature_parts: list[str] = []
        for attribute_name in ("href", "aria-label", "data-result-index", "data-item-id"):
            try:
                attribute_value = await first_result_locator.get_attribute(attribute_name, timeout=2000)
            except Exception:  # noqa: BLE001
                attribute_value = None
            if attribute_value:
                signature_parts.append(str(attribute_value))
        try:
            visible_text = await first_result_locator.inner_text(timeout=2000)
        except Exception:  # noqa: BLE001
            visible_text = ""
        if visible_text:
            signature_parts.append(str(visible_text))
        if not signature_parts:
            return ""
        return _normalize_text_for_matching(" ".join(signature_parts))

    async def _is_no_results_indicator_visible(self, page: Any) -> bool:
        # Only inspect the search results panel, not the entire page body.
        # Scanning the full page causes false positives when "no results" tokens
        # appear in reviews, place descriptions, or other unrelated page content.
        try:
            panel_text = await page.evaluate(
                """() => {
                    const selectors = [
                        'div[role=\'main\'] div[role=\'status\']',
                        'div[role=\'main\'] .section-no-result',
                        'div[role=\'main\'] .section-bad-query-title',
                        'div[role=\'main\'] [jsaction*=\'pane\']',
                        'div[role=\'main\']',
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el) return el.innerText || '';
                    }
                    return document.body ? document.body.innerText : '';
                }"""
            )
        except Exception:  # noqa: BLE001
            panel_text = ""
        if not panel_text:
            return False
        normalized_text = _normalize_text_for_matching(str(panel_text))
        return any(token in normalized_text for token in self._NO_RESULTS_TEXT_TOKENS)

    async def _is_search_loading_indicator_visible(self, page: Any) -> bool:
        try:
            return bool(
                await page.evaluate(
                    """() => {
                        const searchRoot = document.querySelector("div[role='search']");
                        if (!searchRoot) return false;
                        const isVisible = (element) => {
                          if (!element) return false;
                          const style = window.getComputedStyle(element);
                          if (!style || style.visibility === 'hidden' || style.display === 'none') {
                            return false;
                          }
                          const rect = element.getBoundingClientRect();
                          return rect.width > 0 && rect.height > 0;
                        };
                        const candidates = searchRoot.querySelectorAll(
                          ".lSDxNd, .lSDxNd .q0z1yb, [role='progressbar']"
                        );
                        for (const node of candidates) {
                          if (!isVisible(node)) continue;
                          const role = (node.getAttribute('role') || '').toLowerCase();
                          const className = String(node.className || '');
                          if (role === 'progressbar') return true;
                          if (
                            className.includes('lSDxNd')
                            || className.includes('q0z1yb')
                            || className.includes('uj8Yqe')
                          ) {
                            return true;
                          }
                        }
                        return false;
                    }"""
                )
            )
        except Exception:  # noqa: BLE001
            return await self._is_any_selector_visible(
                page,
                self._SEARCH_LOADING_INDICATOR_SELECTORS,
                timeout_ms=150,
            )

    async def _resolve_list_name_input(self, page: Any) -> Any:
        for _attempt in range(6):
            list_name_input = await self._first_visible_locator(page, self._LIST_NAME_INPUT_SELECTORS)
            if list_name_input is not None and await self._is_list_name_input_candidate(list_name_input):
                return list_name_input
            if not await self._advance_list_creation_step(page):
                break
            await self._wait_for_timeout(
                page,
                int(self._config.sync_delay_seconds * 1000),
                step="create_list.wait_for_name_input",
            )
        control_summary = await self._summarize_visible_controls_for_debug(page)
        raise GoogleMapsSelectorError(
            f"Unable to locate list-name input field. Visible controls snapshot: {control_summary}"
        )

    async def _resolve_new_list_button(self, page: Any) -> Any | None:
        new_list_button = await self._locate_new_list_button(page)
        if new_list_button is not None:
            return new_list_button

        if await self._open_lists_subtab(page):
            await self._wait_for_timeout(
                page,
                int(self._config.sync_delay_seconds * 1000),
                step="create_list.wait_after_open_lists_tab",
            )
            await self._wait_for_saved_panel_ready(page)
            new_list_button = await self._locate_new_list_button(page)
            if new_list_button is not None:
                return new_list_button

        if await self._open_saved_view_more(page):
            await self._wait_for_timeout(
                page,
                int(self._config.sync_delay_seconds * 1000),
                step="create_list.wait_after_open_view_more",
            )
            await self._wait_for_saved_panel_ready(page)
            new_list_button = await self._locate_new_list_button(page)
            if new_list_button is not None:
                return new_list_button

        return None

    async def _try_set_list_name_in_creation_dialog(self, page: Any, list_name: str) -> bool:
        try:
            list_name_input = await self._resolve_list_name_input(page)
        except GoogleMapsSelectorError:
            return False

        await self._fill_locator(
            page=page,
            locator=list_name_input,
            value=list_name,
            step=f"create_list.fill_list_name({list_name})",
            error_type=GoogleMapsSelectorError,
        )
        create_button = await self._first_visible_locator(page, self._CREATE_LIST_BUTTON_SELECTORS)
        if create_button is not None:
            await self._click_locator(
                page=page,
                locator=create_button,
                step=f"create_list.submit_create_button({list_name})",
                error_type=GoogleMapsSelectorError,
            )
        elif not await self._submit_locator_with_enter(page=page, locator=list_name_input):
            return False

        await self._wait_for_timeout(
            page,
            int(self._config.sync_delay_seconds * 1000),
            step=f"create_list.wait_after_submit({list_name})",
        )
        if await self._active_list_title_matches(page, list_name):
            return True
        return await self._wait_until_list_entry_available(page, list_name=list_name, max_attempts=3) is not None

    async def _try_rename_inline_untitled_list(self, page: Any, list_name: str) -> bool:
        for _attempt in range(4):
            if await self._active_list_title_matches(page, list_name):
                return True

            inline_input = await self._resolve_inline_list_name_input(page)
            if inline_input is not None:
                renamed = await self._set_inline_list_name(
                    page=page,
                    inline_input=inline_input,
                    list_name=list_name,
                )
                if renamed:
                    return True

            title_button = await self._first_visible_locator(page, self._INLINE_LIST_TITLE_BUTTON_SELECTORS)
            if title_button is not None:
                try:
                    await self._click_locator(
                        page=page,
                        locator=title_button,
                        step=f"create_list.focus_inline_title({list_name})",
                        error_type=GoogleMapsSelectorError,
                    )
                except GoogleMapsError:
                    pass
                await self._wait_for_timeout(
                    page,
                    int(self._config.sync_delay_seconds * 1000),
                    step=f"create_list.wait_after_inline_title_focus({list_name})",
                )
                focused_input = await self._resolve_inline_list_name_input(page)
                if focused_input is None and await self._type_list_name_via_keyboard(page, list_name):
                    await self._wait_for_timeout(
                        page,
                        int(self._config.sync_delay_seconds * 1000),
                        step=f"create_list.wait_after_keyboard_name_type({list_name})",
                    )
                    if await self._active_list_title_matches(page, list_name):
                        return True
                    if (
                        await self._wait_until_list_entry_available(
                            page,
                            list_name=list_name,
                            max_attempts=3,
                        )
                        is not None
                    ):
                        return True
                continue

            if await self._open_untitled_list_entry(page):
                await self._wait_for_timeout(
                    page,
                    int(self._config.sync_delay_seconds * 1000),
                    step=f"create_list.wait_after_open_untitled_entry({list_name})",
                )
                continue

            await self._wait_for_timeout(
                page,
                int(self._config.sync_delay_seconds * 1000),
                step=f"create_list.retry_wait({list_name})",
            )

        return False

    async def _set_inline_list_name(
        self,
        *,
        page: Any,
        inline_input: Any,
        list_name: str,
    ) -> bool:
        try:
            await self._fill_locator(
                page=page,
                locator=inline_input,
                value=list_name,
                step=f"create_list.inline_fill_name({list_name})",
                error_type=GoogleMapsSelectorError,
            )
        except Exception:  # noqa: BLE001
            if not await self._type_list_name_via_keyboard(page, list_name):
                return False
        else:
            # Enter is best-effort; some layouts auto-save on blur/input.
            await self._submit_locator_with_enter(page=page, locator=inline_input)

        await self._wait_for_timeout(
            page,
            int(self._config.sync_delay_seconds * 1000),
            step=f"create_list.wait_after_inline_name({list_name})",
        )
        if await self._active_list_title_matches(page, list_name):
            return True
        return await self._wait_until_list_entry_available(page, list_name=list_name, max_attempts=3) is not None

    async def _open_untitled_list_entry(self, page: Any) -> bool:
        get_by_text = getattr(page, "get_by_text", None)
        if callable(get_by_text):
            for untitled_token in self._UNTITLED_LIST_ENTRY_TOKENS:
                entry: Any = get_by_text(untitled_token, exact=True)
                if await entry.count() == 0:
                    continue
                try:
                    await self._click_locator(
                        page=page,
                        locator=entry.first,
                        step=f"create_list.open_untitled_entry_text({untitled_token})",
                        error_type=GoogleMapsSelectorError,
                    )
                    return True
                except Exception:  # noqa: BLE001
                    continue

        untitled_button = await self._find_visible_button_by_keywords(
            page,
            keyword_phrases=tuple(token.lower() for token in self._UNTITLED_LIST_ENTRY_TOKENS),
            exclusion_phrases=("saved places", "get app"),
        )
        if untitled_button is None:
            return False
        try:
            await self._click_locator(
                page=page,
                locator=untitled_button,
                step="create_list.open_untitled_entry_button",
                error_type=GoogleMapsSelectorError,
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _submit_locator_with_enter(self, *, page: Any, locator: Any) -> bool:
        try:
            await self._press_locator(
                page=page,
                locator=locator,
                key="Enter",
                step="create_list.submit_locator_with_enter",
                error_type=GoogleMapsSelectorError,
            )
            return True
        except Exception:
            try:
                await self._press_keyboard(
                    page=page,
                    key="Enter",
                    step="create_list.keyboard_press_enter",
                    error_type=GoogleMapsSelectorError,
                )
                return True
            except Exception:  # noqa: BLE001
                return False

    async def _type_list_name_via_keyboard(self, page: Any, list_name: str) -> bool:
        select_all_shortcut = "Meta+A" if sys.platform == "darwin" else "Control+A"
        try:
            await self._press_keyboard(
                page=page,
                key=select_all_shortcut,
                step=f"create_list.keyboard_select_all({list_name})",
                error_type=GoogleMapsSelectorError,
            )
            await self._run_browser_step(
                page=page,
                step=f"create_list.keyboard_type_name({list_name})",
                action=lambda: page.keyboard.type(list_name),
                error_type=GoogleMapsSelectorError,
            )
            await self._press_keyboard(
                page=page,
                key="Enter",
                step=f"create_list.keyboard_submit_name({list_name})",
                error_type=GoogleMapsSelectorError,
            )
        except Exception:  # noqa: BLE001
            return False
        return True

    async def _active_list_title_matches(self, page: Any, list_name: str) -> bool:
        normalized_target = _normalize_text_for_matching(list_name)
        title_button = await self._first_visible_locator(page, self._INLINE_LIST_TITLE_BUTTON_SELECTORS)
        if title_button is not None:
            normalized_title = await self._normalized_locator_text(title_button)
            if normalized_title and normalized_target and normalized_target in normalized_title:
                return True

        inline_input = await self._resolve_inline_list_name_input(page)
        if inline_input is None:
            return False
        normalized_input = await self._normalized_locator_text(inline_input)
        return bool(normalized_input and normalized_target and normalized_target in normalized_input)

    async def _resolve_inline_list_name_input(self, page: Any) -> Any | None:
        for selector in self._INLINE_LIST_NAME_INPUT_SELECTORS:
            locator = page.locator(selector)
            if await locator.count() == 0:
                continue
            candidate = locator.first
            try:
                if not await candidate.is_visible():
                    continue
            except Exception:  # noqa: BLE001
                pass
            if await self._is_list_name_input_candidate(candidate):
                return candidate
        return None

    async def _is_list_name_input_candidate(self, locator: Any) -> bool:
        normalized_text = await self._normalized_locator_text(locator)
        if not normalized_text:
            return True
        return not any(
            phrase in normalized_text for phrase in self._LIST_NAME_INPUT_EXCLUSION_PHRASES
        )

    async def _normalized_locator_text(self, locator: Any) -> str:
        segments: list[str] = []
        for attribute_name in (
            "aria-label",
            "placeholder",
            "id",
            "name",
            "class",
            "jsaction",
            "value",
            "data-value",
            "data-tooltip",
        ):
            try:
                value = await locator.get_attribute(attribute_name, timeout=2000)
            except Exception:  # noqa: BLE001
                value = None
            if value:
                segments.append(str(value))
        try:
            text_value = await locator.inner_text(timeout=2000)
        except Exception:  # noqa: BLE001
            text_value = ""
        if text_value:
            segments.append(str(text_value))
        try:
            input_value = await locator.input_value(timeout=2000)
        except Exception:  # noqa: BLE001
            input_value = ""
        if input_value:
            segments.append(str(input_value))
        if not segments:
            return ""
        return _normalize_text_for_matching(" ".join(segments))

    async def _normalized_list_label_text(self, locator: Any) -> str:
        segments: list[str] = []
        for attribute_name in ("aria-label", "data-value", "title"):
            try:
                value = await locator.get_attribute(attribute_name, timeout=2000)
            except Exception:  # noqa: BLE001
                value = None
            if value:
                segments.append(str(value))
        try:
            text_value = await locator.inner_text(timeout=2000)
        except Exception:  # noqa: BLE001
            text_value = ""
        if text_value:
            segments.append(str(text_value))
        if not segments:
            return ""
        return _normalize_text_for_matching(" ".join(segments))

    def _list_name_match_score(self, *, list_name: str, candidate_text: str) -> int:
        normalized_target = _normalize_text_for_matching(list_name)
        normalized_candidate = _normalize_text_for_matching(candidate_text)
        if not normalized_target or not normalized_candidate:
            return 0
        if normalized_target == normalized_candidate:
            return 3
        if _candidate_starts_with_list_name(normalized_candidate, normalized_target):
            return 2
        return 0

    async def _locator_list_name_match_score(self, *, locator: Any, list_name: str) -> int:
        return self._list_name_match_score(
            list_name=list_name,
            candidate_text=await self._normalized_list_label_text(locator),
        )

    async def _ensure_lists_view(self, page: Any) -> None:
        if await self._open_lists_subtab(page):
            await self._wait_for_timeout(
                page,
                int(self._config.sync_delay_seconds * 1000),
                step="saved_tab.wait_after_open_lists_view",
            )
            await self._wait_for_saved_panel_ready(page)

    _SAVE_DIALOG_LIST_CANDIDATE_SELECTOR = (
        "div[role='dialog'] [role='checkbox'], "
        "div[role='dialog'] [role='menuitemcheckbox'], "
        "div[role='dialog'] [role='menuitemradio'], "
        "div[role='dialog'] button, "
        "div[role='dialog'] [role='button'], "
        "[role='menu'] [role='menuitemradio'], "
        "[role='menu'] [role='menuitemcheckbox'], "
        "[role='menu'] [role='menuitem'], "
        "[role='menu'] button"
    )

    async def _resolve_save_dialog_list_selector(self, page: Any, list_name: str) -> Any | None:
        # Fast path: single page.evaluate() call to find the matching index,
        # avoiding N×5 Playwright round-trips for visibility + attribute fetching.
        try:
            best_index = await self._resolve_list_index_via_js(page, list_name)
        except Exception:  # noqa: BLE001
            best_index = -1
        if best_index >= 0:
            candidates = page.locator(self._SAVE_DIALOG_LIST_CANDIDATE_SELECTOR)
            return candidates.nth(best_index)

        # Fallback: element-by-element Playwright locator scan.
        return await self._resolve_save_dialog_list_selector_slow(page, list_name)

    async def _resolve_list_index_via_js(self, page: Any, list_name: str) -> int:
        """Find best-matching candidate index using a single JS round-trip."""
        return await page.evaluate(
            """({ selector, listName }) => {
                const normalize = (s) => (s || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                const target = normalize(listName);
                if (!target) return -1;

                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' ||
                        style.opacity === '0') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };

                const allowedSuffixStarts = [
                    '', 'private', 'public', 'shared', 'places', 'place',
                    '0 places', '1 place', 'add a note', 'hide place lists details',
                ];
                const stripLeadingUiIcons = (value) => value.replace(/^[\\uE000-\\uF8FF\\s]+/u, '');
                const startsWithListName = (candidate, needle) => {
                    const variants = [candidate, stripLeadingUiIcons(candidate)];
                    const savedInMarker = 'saved in';
                    let savedInIndex = candidate.indexOf(savedInMarker);
                    while (savedInIndex >= 0) {
                        variants.push(candidate.slice(savedInIndex + savedInMarker.length).trim());
                        savedInIndex = candidate.indexOf(savedInMarker, savedInIndex + savedInMarker.length);
                    }
                    for (const value of variants) {
                        if (!value.startsWith(needle)) continue;
                        const suffix = value.slice(needle.length).trim();
                        if (!suffix) return true;
                        if (allowedSuffixStarts.some((allowed) => allowed && suffix.startsWith(allowed))) {
                            return true;
                        }
                        if (/^\\d+\\s+(saved|places?|items?)/u.test(suffix)) {
                            return true;
                        }
                    }
                    return false;
                };

                const candidates = document.querySelectorAll(selector);
                let bestIndex = -1;
                let bestScore = 0;
                let bestLen = Infinity;
                const limit = Math.min(candidates.length, 120);
                for (let i = 0; i < limit; i++) {
                    const el = candidates[i];
                    if (!isVisible(el)) continue;
                    const parts = [];
                    for (const attr of ['aria-label', 'data-value', 'title']) {
                        const v = el.getAttribute(attr);
                        if (v) parts.push(v);
                    }
                    const txt = (el.innerText || '').trim();
                    if (txt) parts.push(txt);
                    if (!parts.length) continue;
                    const candidate = normalize(parts.join(' '));
                    if (!candidate) continue;
                    let score = 0;
                    if (candidate === target) {
                        score = 3;
                    } else if (startsWithListName(candidate, target)) {
                        score = 2;
                    }
                    if (score > bestScore || (score > 0 && score === bestScore && candidate.length < bestLen)) {
                        bestScore = score;
                        bestIndex = i;
                        bestLen = candidate.length;
                        if (score >= 3) break;
                    }
                }
                return bestIndex;
            }""",
            {"selector": self._SAVE_DIALOG_LIST_CANDIDATE_SELECTOR, "listName": list_name},
        )

    async def _resolve_save_dialog_list_selector_slow(self, page: Any, list_name: str) -> Any | None:
        """Fallback: element-by-element Playwright locator scan."""
        candidates = page.locator(self._SAVE_DIALOG_LIST_CANDIDATE_SELECTOR)
        candidate_count = min(await candidates.count(), 120)
        best_candidate: Any | None = None
        best_score = 0
        for index in range(candidate_count):
            candidate = candidates.nth(index)
            try:
                if not await candidate.is_visible():
                    continue
            except Exception:  # noqa: BLE001
                continue
            match_score = await self._locator_list_name_match_score(
                locator=candidate,
                list_name=list_name,
            )
            if match_score <= 0:
                continue
            if match_score > best_score:
                best_score = match_score
                best_candidate = candidate
                if match_score >= 3:
                    break
        if best_candidate is not None:
            return best_candidate
        return None

    async def _find_list_entry_locator(self, page: Any, list_name: str) -> Any | None:
        # Exclude aria-live toast notifications (e.g. "Saved to <list>") which
        # contain the list name as text but are not clickable list-entry buttons.
        non_toast = page.locator(":not([aria-live])")
        exact_locator = page.get_by_text(list_name, exact=True).and_(non_toast)
        if await exact_locator.count() > 0:
            return exact_locator.first

        partial_locator = page.get_by_text(list_name, exact=False).and_(non_toast)
        if await partial_locator.count() > 0 and await self._locator_list_name_match_score(
            locator=partial_locator.first,
            list_name=list_name,
        ) > 0:
            return partial_locator.first

        button_candidates = page.locator("button, [role='button'], [role='listitem'], a")
        candidate_count = min(await button_candidates.count(), 240)
        best_candidate: Any | None = None
        best_score = 0
        for index in range(candidate_count):
            candidate = button_candidates.nth(index)
            try:
                if not await candidate.is_visible():
                    continue
            except Exception:  # noqa: BLE001
                continue
            match_score = await self._locator_list_name_match_score(
                locator=candidate,
                list_name=list_name,
            )
            if match_score <= 0:
                continue
            if match_score > best_score:
                best_score = match_score
                best_candidate = candidate
                if match_score >= 3:
                    break
        return best_candidate

    async def _wait_until_list_entry_available(
        self,
        page: Any,
        *,
        list_name: str,
        max_attempts: int,
    ) -> Any | None:
        for _attempt in range(max(max_attempts, 1)):
            locator = await self._find_list_entry_locator(page, list_name)
            if locator is not None:
                return locator
            await self._wait_for_timeout(
                page,
                int(self._config.sync_delay_seconds * 1000),
                step=f"saved_tab.wait_for_list_entry({list_name})",
            )
            await self._ensure_lists_view(page)
        return None

    async def _locate_new_list_button(self, page: Any) -> Any | None:
        new_list_button = await self._first_visible_locator(page, self._NEW_LIST_BUTTON_SELECTORS)
        if new_list_button is not None:
            return new_list_button
        return await self._find_visible_button_by_keywords(
            page,
            keyword_phrases=(
                "new list",
                "create list",
                "\ue1d3",
                "\ue145",
            ),
            exclusion_phrases=(
                "saved places",
                "lists",
            ),
        )

    async def _open_lists_subtab(self, page: Any) -> bool:
        tab_button = await self._first_visible_locator(page, self._LISTS_TAB_SELECTORS)
        if tab_button is None:
            return False
        try:
            await self._click_locator(
                page=page,
                locator=tab_button,
                step="saved_tab.open_lists_subtab",
                error_type=GoogleMapsSelectorError,
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _open_saved_view_more(self, page: Any) -> bool:
        view_more_button = await self._first_visible_locator(page, self._SAVED_VIEW_MORE_SELECTORS)
        if view_more_button is None:
            return False
        try:
            await self._click_locator(
                page=page,
                locator=view_more_button,
                step="saved_tab.open_view_more",
                error_type=GoogleMapsSelectorError,
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _find_visible_button_by_keywords(
        self,
        page: Any,
        *,
        keyword_phrases: tuple[str, ...],
        exclusion_phrases: tuple[str, ...] = (),
        max_scan_count: int = 120,
    ) -> Any | None:
        candidates = page.locator("button, [role='button']")
        candidate_count = min(await candidates.count(), max_scan_count)
        for index in range(candidate_count):
            candidate = candidates.nth(index)
            try:
                if not await candidate.is_visible():
                    continue
                aria_label = str(await candidate.get_attribute("aria-label", timeout=2000) or "")
                text_value = str(await candidate.inner_text(timeout=2000) or "")
            except Exception:  # noqa: BLE001
                continue

            normalized = f"{aria_label} {text_value}".strip().lower()
            if not normalized:
                continue
            if any(exclusion in normalized for exclusion in exclusion_phrases):
                continue
            if any(keyword in normalized for keyword in keyword_phrases):
                return candidate
        return None

    async def _advance_list_creation_step(self, page: Any) -> bool:
        entry_button = await self._first_visible_locator(page, self._LIST_CREATION_ENTRY_SELECTORS)
        if entry_button is not None:
            await self._click_locator(
                page=page,
                locator=entry_button,
                step="create_list.advance_step_entry_button",
                error_type=GoogleMapsSelectorError,
            )
            return True

        menu_item_locator = page.locator(
            "div[role='menu'] [role='menuitem'], div[role='menu'] button, [role='menu'] [role='menuitem']"
        )
        if await menu_item_locator.count() == 0:
            return False
        try:
            await self._click_locator(
                page=page,
                locator=menu_item_locator.first,
                step="create_list.advance_step_menu_item",
                error_type=GoogleMapsSelectorError,
            )
        except Exception:  # noqa: BLE001
            return False
        return True

    async def _summarize_visible_controls_for_debug(self, page: Any) -> str:
        try:
            payload = await page.evaluate(
                """() => {
                    const isVisible = (element) => {
                      if (!element) return false;
                      const style = window.getComputedStyle(element);
                      if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
                      const rect = element.getBoundingClientRect();
                      return rect.width > 0 && rect.height > 0;
                    };
                    const controls = [];
                    const candidates = document.querySelectorAll(
                      "div[role='dialog'] button, div[role='dialog'] input, div[role='dialog'] textarea, " +
                      "div[role='dialog'] [role='textbox'], div[role='menu'] [role='menuitem'], " +
                      "div[role='menu'] button, [contenteditable='true'], " +
                      "div[role='main'] textarea[aria-label*='note' i], " +
                      "div[role='main'] [role='button'][aria-label*='place lists details' i], " +
                      "div[role='main'] [aria-label*='saved in' i]"
                    );
                    for (const node of candidates) {
                      if (!isVisible(node)) continue;
                      const role = node.getAttribute('role') || node.tagName.toLowerCase();
                      const text = (node.innerText || node.getAttribute('aria-label') || node.getAttribute('placeholder') || '')
                        .replace(/\\s+/g, ' ')
                        .trim()
                        .slice(0, 36);
                      controls.push(text ? `${role}:${text}` : role);
                      if (controls.length >= 10) break;
                    }
                    return controls;
                }"""
            )
        except Exception:  # noqa: BLE001
            return "<unavailable>"
        if not payload:
            return "<none>"
        return ", ".join(str(item) for item in payload)

    async def _summarize_saved_panel_controls_for_debug(self, page: Any) -> str:
        try:
            payload = await page.evaluate(
                """() => {
                    const isVisible = (element) => {
                      if (!element) return false;
                      const style = window.getComputedStyle(element);
                      if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
                      const rect = element.getBoundingClientRect();
                      return rect.width > 0 && rect.height > 0;
                    };
                    const controls = [];
                    const candidates = document.querySelectorAll(
                      "button, [role='button'], [role='menuitem'], [aria-label]"
                    );
                    for (const node of candidates) {
                      if (!isVisible(node)) continue;
                      const role = node.getAttribute('role') || node.tagName.toLowerCase();
                      const text = (node.innerText || node.getAttribute('aria-label') || '')
                        .replace(/\\s+/g, ' ')
                        .trim()
                        .slice(0, 42);
                      controls.push(text ? `${role}:${text}` : role);
                      if (controls.length >= 20) break;
                    }
                    return controls;
                }"""
            )
        except Exception:  # noqa: BLE001
            return "<unavailable>"
        if not payload:
            return "<none>"
        return ", ".join(str(item) for item in payload)

    async def _summarize_save_controls_for_debug(self, page: Any) -> str:
        try:
            payload = await page.evaluate(
                """() => {
                    const isVisible = (element) => {
                      if (!element) return false;
                      const style = window.getComputedStyle(element);
                      if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
                      const rect = element.getBoundingClientRect();
                      return rect.width > 0 && rect.height > 0;
                    };
                    const controls = [];
                    const candidates = document.querySelectorAll(
                      "div[role='main'] button, div[role='main'] [role='button']"
                    );
                    for (const node of candidates) {
                      if (!isVisible(node)) continue;
                      const role = node.getAttribute('role') || node.tagName.toLowerCase();
                      const ariaLabel = (node.getAttribute('aria-label') || '').trim();
                      const dataItemId = (node.getAttribute('data-item-id') || '').trim();
                      const dataValue = (node.getAttribute('data-value') || '').trim();
                      const jsaction = (node.getAttribute('jsaction') || '').trim();
                      const text = (node.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 24);
                      const summary = [role, ariaLabel || text, dataItemId, dataValue, jsaction]
                        .filter(Boolean)
                        .join(':');
                      controls.push(summary || role);
                      if (controls.length >= 20) break;
                    }
                    return controls;
                }"""
            )
        except Exception:  # noqa: BLE001
            return "<unavailable>"
        if not payload:
            return "<none>"
        return ", ".join(str(item) for item in payload)

    async def _summarize_search_controls_for_debug(self, page: Any) -> str:
        try:
            payload = await page.evaluate(
                """() => {
                    const isVisible = (element) => {
                      if (!element) return false;
                      const style = window.getComputedStyle(element);
                      if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
                      const rect = element.getBoundingClientRect();
                      return rect.width > 0 && rect.height > 0;
                    };
                    const controls = [];
                    const candidates = document.querySelectorAll(
                      "input, textarea, [role='search'] button, [role='combobox']"
                    );
                    for (const node of candidates) {
                      if (!isVisible(node)) continue;
                      const role = node.getAttribute('role') || node.tagName.toLowerCase();
                      const id = node.getAttribute('id') || '';
                      const name = node.getAttribute('name') || '';
                      const ariaLabel = node.getAttribute('aria-label') || '';
                      const placeholder = node.getAttribute('placeholder') || '';
                      const text = (node.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 24);
                      const summary = [role, id, name, ariaLabel || placeholder || text]
                        .filter(Boolean)
                        .join(':');
                      controls.push(summary);
                      if (controls.length >= 12) break;
                    }
                    return controls;
                }"""
            )
        except Exception:  # noqa: BLE001
            return "<unavailable>"
        if not payload:
            return "<none>"
        return ", ".join(str(item) for item in payload)

    async def _summarize_browser_state_for_debug(self, page: Any) -> str:
        return (
            f"url={self._read_page_url(page) or '<unknown>'}; "
            f"search_controls={await self._summarize_search_controls_for_debug(page)}; "
            f"place_controls={await self._summarize_save_controls_for_debug(page)}; "
            f"saved_controls={await self._summarize_saved_panel_controls_for_debug(page)}; "
            f"dialog_controls={await self._summarize_visible_controls_for_debug(page)}"
        )

    async def _is_any_selector_visible(
        self,
        page: Any,
        selectors: tuple[str, ...],
        timeout_ms: int,
    ) -> bool:
        for selector in selectors:
            locator_group = page.locator(selector)
            locator_count_callable: Any = getattr(locator_group, "count", None)
            if callable(locator_count_callable):
                try:
                    locator_count = await cast(Any, locator_count_callable())
                    if not isinstance(locator_count, int):
                        continue
                    if locator_count == 0:
                        continue
                except Exception:  # noqa: BLE001
                    continue
            candidate = getattr(locator_group, "first", locator_group)
            try:
                if await candidate.is_visible(timeout=timeout_ms):
                    return True
            except TypeError:
                try:
                    if bool(await candidate.is_visible()):
                        return True
                except Exception:  # noqa: BLE001
                    continue
            except Exception:  # noqa: BLE001
                continue
        return False

    async def _require_page(self) -> Any:
        context = self._context
        if context is None:
            raise GoogleMapsError("Google Maps driver is not started.")
        if self._page is not None and not _is_page_closed(self._page):
            return self._page
        for candidate in reversed(tuple(context.pages)):
            if _is_page_closed(candidate):
                continue
            self._page = candidate
            return candidate
        self._page = await context.new_page()
        return self._page

    async def _has_login_security_block(self) -> bool:
        page = await self._require_page()
        try:
            html_content = str(await page.content()).lower()
        except Exception:  # noqa: BLE001
            return False
        if self._SECURITY_BLOCK_MAIN_TOKEN not in html_content:
            return False
        return any(token in html_content for token in self._SECURITY_BLOCK_SIGNIN_TOKENS)

    async def _is_limited_view_mode(self, page: Any) -> bool:
        try:
            html_content = str(await page.content()).lower()
        except Exception:  # noqa: BLE001
            return False
        return self._LIMITED_VIEW_TOKEN in html_content

    async def _has_google_session_cookie(self) -> bool:
        if self._context is None:
            return False
        try:
            cookies = await self._context.cookies([GOOGLE_MAPS_HOME_URL, "https://accounts.google.com"])
        except Exception:  # noqa: BLE001
            return False
        for cookie in cookies:
            cookie_name = str(cookie.get("name", ""))
            cookie_value = str(cookie.get("value", ""))
            if cookie_name in self._GOOGLE_SESSION_COOKIE_NAMES and cookie_value:
                return True
        return False

    async def _launch_persistent_context_with_channel_fallback(
        self,
        launch_options: dict[str, Any],
    ) -> Any:
        if self._playwright is None:
            raise GoogleMapsError("Playwright runtime is not started.")

        last_error: Exception | None = None
        attempt_failures: list[_LaunchAttemptFailure] = []
        channel_candidates: tuple[str | None, ...]
        if self._config.browser_channel:
            channel_candidates = (self._config.browser_channel, None)
        else:
            channel_candidates = (None,)

        for channel in channel_candidates:
            for chromium_sandbox in _sandbox_candidates_for_channel(channel):
                launch_kwargs = dict(launch_options)
                launch_kwargs["chromium_sandbox"] = chromium_sandbox
                if channel is not None:
                    launch_kwargs["channel"] = channel
                try:
                    return await self._playwright.chromium.launch_persistent_context(**launch_kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    attempt_failures.append(
                        _LaunchAttemptFailure(
                            channel=channel,
                            chromium_sandbox=chromium_sandbox,
                            error=exc,
                        )
                    )
                    if channel is not None and _is_missing_browser_channel_error(exc):
                        break
                    continue

        raise GoogleMapsTransientError(
            _build_browser_launch_failure_message(last_error, attempt_failures)
        ) from last_error


async def _read_page_content_safe(page: Any) -> str | None:
    """Read page HTML, returning None on any error (e.g. browser already dead)."""
    try:
        return str(await page.content())
    except Exception:  # noqa: BLE001
        return None


async def _playwright_stop_quiet(playwright: Any) -> None:
    try:
        await playwright.stop()
    except Exception:  # noqa: BLE001
        pass


def _force_kill_browser_process(context: Any | None) -> None:
    """SIGKILL the browser child process so blocked Playwright calls unblock."""
    import os
    import signal as _signal

    for obj in (context, getattr(context, "browser", None)):
        if obj is None:
            continue
        process = getattr(obj, "process", None)
        if process is None:
            continue
        pid = getattr(process, "pid", None)
        if pid is None:
            continue
        try:
            os.kill(pid, _signal.SIGKILL)
        except OSError:
            pass
        break


async def _force_kill_playwright(context: Any | None, playwright: Any | None) -> None:
    """Kill the browser process and Playwright runtime without blocking."""
    _force_kill_browser_process(context)

    # Best-effort Playwright stop with a hard timeout so we never block.
    if playwright is not None:
        try:
            await asyncio.wait_for(_playwright_stop_quiet(playwright), timeout=3)
        except (TimeoutError, Exception):  # noqa: BLE001
            pass


def _is_navigation_abort_error(error: Exception) -> bool:
    """Return whether Playwright navigation failed due to net::ERR_ABORTED."""

    return "net::ERR_ABORTED" in str(error)


def _is_target_closed_error(error: Exception) -> bool:
    message = str(error).lower()
    return "target page, context or browser has been closed" in message


def _is_page_closed(page: Any) -> bool:
    try:
        return bool(page.is_closed())
    except Exception:  # noqa: BLE001
        return True


def _normalize_text_for_matching(text: str) -> str:
    return " ".join(text.lower().split())


_SCOPED_NOTE_FIELD_JS = r"""({ mode, listName, noteText }) => {
  const normalize = (value) => String(value || '').toLowerCase().replace(/\s+/g, ' ').trim();
  const normalizeNote = (value) => String(value || '')
    .split(/\r?\n/)
    .map((line) => line.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\n')
    .toLowerCase();
  const target = normalize(listName);
  if (!target) return false;

  const allowedSuffixStarts = [
    '', 'private', 'public', 'shared', 'places', 'place',
    '0 places', '1 place', 'add a note', 'hide place lists details',
  ];
  const stripLeadingUiIcons = (value) => value.replace(/^[\uE000-\uF8FF\s]+/u, '');
  const startsWithListName = (candidate, needle) => {
    const variants = [candidate, stripLeadingUiIcons(candidate)];
    const savedInMarker = 'saved in';
    let savedInIndex = candidate.indexOf(savedInMarker);
    while (savedInIndex >= 0) {
      variants.push(candidate.slice(savedInIndex + savedInMarker.length).trim());
      savedInIndex = candidate.indexOf(savedInMarker, savedInIndex + savedInMarker.length);
    }
    for (const value of variants) {
      if (!value.startsWith(needle)) continue;
      const suffix = value.slice(needle.length).trim();
      if (!suffix) return true;
      if (allowedSuffixStarts.some((allowed) => allowed && suffix.startsWith(allowed))) return true;
      if (/^\d+\s+(saved|places?|items?)/u.test(suffix)) return true;
    }
    return false;
  };
  const targetMatches = (value) => {
    const normalized = normalize(value);
    return normalized === target || startsWithListName(normalized, target);
  };

  const isVisible = (element) => {
    if (!element) return false;
    const style = window.getComputedStyle(element);
    if (!style || style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') {
      return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const readLabel = (element) => normalize([
    (element.getAttribute && element.getAttribute('aria-label')) || '',
    (element.getAttribute && element.getAttribute('placeholder')) || '',
    (element.getAttribute && element.getAttribute('title')) || '',
    (element.getAttribute && element.getAttribute('data-value')) || '',
    element.innerText || '',
    element.textContent || '',
  ].join(' '));
  const readOwnLabel = (element) => normalize([
    (element.getAttribute && element.getAttribute('aria-label')) || '',
    (element.getAttribute && element.getAttribute('placeholder')) || '',
    (element.getAttribute && element.getAttribute('title')) || '',
    (element.getAttribute && element.getAttribute('data-value')) || '',
  ].join(' '));
  const readValue = (element) => {
    const tagName = String(element.tagName || '').toLowerCase();
    if (tagName === 'textarea' || tagName === 'input') return element.value || '';
    if (element.isContentEditable) return element.innerText || element.textContent || '';
    return (
      (element.getAttribute && (
        element.getAttribute('value') ||
        element.getAttribute('aria-label') ||
        element.getAttribute('placeholder')
      )) ||
      element.innerText ||
      element.textContent ||
      ''
    );
  };
  const isEditable = (element) => {
    if (!element || !isVisible(element)) return false;
    const tagName = String(element.tagName || '').toLowerCase();
    if ((tagName === 'textarea' || tagName === 'input') && (element.disabled || element.readOnly)) {
      return false;
    }
    return true;
  };
  const noteFieldScore = (element) => {
    if (!isEditable(element)) return 0;
    const tagName = String(element.tagName || '').toLowerCase();
    const role = normalize(element.getAttribute && element.getAttribute('role'));
    if (role === 'combobox' || role === 'checkbox') return 0;

    const label = readLabel(element);
    let score = 0;
    if (label.includes('note') || label.includes('memo')) score += 100;
    if (tagName === 'textarea') score += 30;
    if (element.isContentEditable || role === 'textbox') score += 20;
    if (tagName === 'input') score += 5;
    const jsaction = normalize(element.getAttribute && element.getAttribute('jsaction'));
    if (jsaction.includes('textentry')) score += 5;
    return score;
  };
  const findEditableFields = (root) => Array.from(
    root.querySelectorAll("textarea, input, [contenteditable='true'], [role='textbox']")
  )
    .map((field, index) => ({ field, score: noteFieldScore(field), index }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map((entry) => entry.field);
  const isSelected = (element) => {
    for (const attr of ['aria-checked', 'aria-selected', 'data-selected', 'data-state']) {
      const value = normalize(element.getAttribute && element.getAttribute(attr));
      if (['true', '1', 'yes', 'on', 'checked', 'selected'].includes(value)) return true;
    }
    return false;
  };

  const surfaceRoots = Array.from(
    document.querySelectorAll("div[role='dialog'], [role='menu'], div[role='main']")
  ).filter(isVisible);
  const savedListContainerSelector = (
    "[role='group'][aria-label*='Saved in' i], " +
    "[role='region'][aria-label*='Saved in' i], " +
    "[aria-label*='Saved in' i]"
  );
  const rowCandidatesSelector = (
    "[role='checkbox'], [role='menuitemcheckbox'], [role='menuitemradio'], " +
    "button, [role='button'], [role='menuitem'], [aria-label*='Saved in' i]"
  );
  const scopedFields = [];
  const appendFields = (root) => {
    for (const field of findEditableFields(root)) scopedFields.push(field);
  };

  for (const surfaceRoot of surfaceRoots) {
    const targetContainers = Array.from(surfaceRoot.querySelectorAll(savedListContainerSelector))
      .filter(isVisible)
      .filter((container) => targetMatches(readOwnLabel(container)))
      .filter((container) => findEditableFields(container).length > 0);
    if (targetContainers.length > 0) {
      for (const container of targetContainers) appendFields(container);
      continue;
    }

    const rowCandidates = Array.from(surfaceRoot.querySelectorAll(rowCandidatesSelector)).filter(isVisible);
    for (const rowCandidate of rowCandidates) {
      if (!targetMatches(readLabel(rowCandidate))) continue;
      const ownTargetContainer = rowCandidate.matches(savedListContainerSelector)
        ? rowCandidate
        : rowCandidate.closest(savedListContainerSelector);
      if (
        ownTargetContainer &&
        surfaceRoot.contains(ownTargetContainer) &&
        targetMatches(readOwnLabel(ownTargetContainer))
      ) {
        appendFields(ownTargetContainer);
        continue;
      }

      const role = normalize(surfaceRoot.getAttribute && surfaceRoot.getAttribute('role'));
      if (role === 'dialog' || role === 'menu') {
        const surfaceFields = findEditableFields(surfaceRoot);
        if (isSelected(rowCandidate) || surfaceFields.length === 1) {
          for (const field of surfaceFields) scopedFields.push(field);
        }
      }
    }
  }

  const fields = Array.from(new Set(scopedFields));
  if (mode === 'visible') return fields.length > 0;

  const targetNote = normalizeNote(noteText);
  if (!targetNote) return false;
  const noteMatches = (value) => {
    const normalizedValue = normalizeNote(value);
    return Boolean(
      normalizedValue &&
      (normalizedValue === targetNote ||
        normalizedValue.includes(targetNote) ||
        targetNote.includes(normalizedValue))
    );
  };

  if (mode === 'matches') {
    return fields.some((field) => noteMatches(readValue(field)));
  }
  if (mode === 'set') {
    const field = fields[0];
    if (!field) return false;
    const tagName = String(field.tagName || '').toLowerCase();
    field.focus();
    if (tagName === 'textarea' || tagName === 'input') {
      field.value = noteText;
    } else if (field.isContentEditable) {
      field.innerText = noteText;
    } else {
      return false;
    }
    field.dispatchEvent(new Event('input', { bubbles: true }));
    field.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
    field.dispatchEvent(new Event('change', { bubbles: true }));
    field.blur();
    return true;
  }
  return false;
}"""


def _normalize_note_value(value: str) -> str:
    normalized_lines: list[str] = []
    for line in str(value).splitlines():
        compact_line = " ".join(line.split()).strip()
        if compact_line:
            normalized_lines.append(compact_line)
    return "\n".join(normalized_lines)


def _note_text_matches(*, actual_value: str, expected_value: str) -> bool:
    normalized_expected = _normalize_note_value(expected_value).lower()
    normalized_actual = _normalize_note_value(actual_value).lower()
    if not normalized_expected or not normalized_actual:
        return False
    if normalized_actual == normalized_expected:
        return True
    return (
        normalized_expected in normalized_actual
        or normalized_actual in normalized_expected
    )


_LIST_NAME_BOUNDARY_CHARS = frozenset(
    {
        "|",
        "/",
        ",",
        ".",
        ":",
        ";",
        "(",
        ")",
        "[",
        "]",
        "{",
        "}",
        "-",
        "_",
        "=",
        "+",
        "·",
        "•",
    }
)


def _is_list_name_boundary_character(character: str) -> bool:
    if not character:
        return True
    if character.isspace():
        return True
    return character in _LIST_NAME_BOUNDARY_CHARS


def _contains_text_with_boundaries(*, haystack: str, needle: str) -> bool:
    if not haystack or not needle:
        return False
    start_index = haystack.find(needle)
    while start_index >= 0:
        end_index = start_index + len(needle)
        before_char = haystack[start_index - 1] if start_index > 0 else ""
        after_char = haystack[end_index] if end_index < len(haystack) else ""
        if _is_list_name_boundary_character(before_char) and _is_list_name_boundary_character(
            after_char
        ):
            return True
        start_index = haystack.find(needle, start_index + 1)
    return False


def _candidate_starts_with_list_name(candidate_text: str, list_name: str) -> bool:
    allowed_suffix_starts = (
        "private",
        "public",
        "shared",
        "places",
        "place",
        "0 places",
        "1 place",
        "add a note",
        "hide place lists details",
    )
    variants = [candidate_text, _strip_leading_ui_icon_text(candidate_text)]
    saved_in_marker = "saved in"
    saved_in_index = candidate_text.find(saved_in_marker)
    while saved_in_index >= 0:
        variants.append(candidate_text[saved_in_index + len(saved_in_marker) :].strip())
        saved_in_index = candidate_text.find(saved_in_marker, saved_in_index + len(saved_in_marker))

    for value in variants:
        if not value.startswith(list_name):
            continue
        suffix = value[len(list_name) :].strip()
        if not suffix:
            return True
        if any(suffix.startswith(allowed) for allowed in allowed_suffix_starts):
            return True
        if re.match(r"^\d+\s+(saved|places?|items?)", suffix):
            return True
    return False


def _strip_leading_ui_icon_text(value: str) -> str:
    index = 0
    while index < len(value):
        character = value[index]
        codepoint = ord(character)
        if character.isspace() or 0xE000 <= codepoint <= 0xF8FF:
            index += 1
            continue
        break
    return value[index:]


def _is_saved_only_control_label(normalized_text: str) -> bool:
    if not normalized_text:
        return False

    english_words = [word for word in normalized_text.split() if word]
    if not english_words:
        return False
    return all(word == "saved" for word in english_words)


def _is_missing_browser_channel_error(error: Exception) -> bool:
    """Return whether a requested browser channel is unavailable."""

    message = str(error).lower()
    return (
        "distribution 'chrome' is not found" in message
        or "browser executable not found" in message
        or "could not find browser" in message
    )


def _is_browser_crash_on_launch(error: Exception | None) -> bool:
    if error is None:
        return False
    message = str(error).lower()
    return (
        "target page, context or browser has been closed" in message
        or "sigtrap" in message
        or "gracefully close start" in message
    )


def _build_browser_launch_failure_message(
    error: Exception | None,
    attempt_failures: list[_LaunchAttemptFailure],
) -> str:
    if error is None:
        base_message = "Unable to launch browser."
    else:
        base_message = f"Unable to launch browser: {_compact_error_message(str(error))}"
    attempts_summary = _build_launch_attempt_summary(attempt_failures)
    detail_block = f"\nLaunch attempts:\n{attempts_summary}" if attempts_summary else ""
    if _is_browser_crash_on_launch(error):
        return (
            f"{base_message}\n"
            f"{detail_block}\n"
            "Fix:\n"
            "  1. Close all Google Chrome windows.\n"
            "  2. Retry with a fresh profile path via --google-user-data-dir.\n"
            "  3. Upgrade Playwright and reinstall browser runtime:\n"
            "     uv sync\n"
            "     uv run playwright install chromium"
        )
    return f"{base_message}{detail_block}"


@dataclass(frozen=True)
class _LaunchAttemptFailure:
    channel: str | None
    chromium_sandbox: bool
    error: Exception


def _sandbox_candidates_for_channel(channel: str | None) -> tuple[bool, ...]:
    # On macOS, Chrome channel with --no-sandbox is unstable on some setups.
    if channel is not None and sys.platform == "darwin":
        return (True,)
    return (True, False)


def _build_launch_attempt_summary(attempt_failures: list[_LaunchAttemptFailure]) -> str:
    lines: list[str] = []
    for attempt in attempt_failures:
        channel_label = attempt.channel if attempt.channel is not None else "playwright-default"
        sandbox_label = "on" if attempt.chromium_sandbox else "off"
        compact_error = _compact_error_message(str(attempt.error))
        lines.append(
            f"  - channel={channel_label}, chromium_sandbox={sandbox_label}: {compact_error}"
        )
    return "\n".join(lines)


def _compact_error_message(message: str) -> str:
    stripped = message.strip()
    if not stripped:
        return "<no error message>"
    first_line = stripped.splitlines()[0].strip()
    if not first_line:
        return "<no error message>"
    max_length = 220
    if len(first_line) <= max_length:
        return first_line
    return f"{first_line[:max_length - 3]}..."


def _prepare_user_data_dir_for_automation(user_data_dir: Path) -> None:
    """Clean known stale Chromium lock/session artifacts before launch."""

    user_data_dir.mkdir(parents=True, exist_ok=True)
    for transient_name in (
        "SingletonLock",
        "SingletonSocket",
        "SingletonCookie",
        "DevToolsActivePort",
    ):
        _safe_remove_path(user_data_dir / transient_name)

    default_profile_dir = user_data_dir / "Default"
    for transient_name in (
        "Sessions",
        "Last Session",
        "Last Tabs",
        "Current Session",
        "Current Tabs",
    ):
        _safe_remove_path(default_profile_dir / transient_name)


def _safe_remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    path.unlink(missing_ok=True)


def _build_launch_options(
    *,
    user_data_dir: Path,
    headless: bool,
    suppress_automation_flags: bool,
    base_args: tuple[str, ...],
) -> dict[str, Any]:
    args = list(base_args)
    launch_options: dict[str, Any] = {
        "user_data_dir": str(user_data_dir),
        "headless": headless,
        "viewport": {"width": 1440, "height": 960},
        "accept_downloads": False,
    }

    if suppress_automation_flags:
        launch_options["ignore_default_args"] = ["--enable-automation"]
        args.append("--disable-blink-features=AutomationControlled")

    launch_options["args"] = args
    return launch_options
