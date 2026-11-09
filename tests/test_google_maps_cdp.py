"""CDP configuration and external-browser resource ownership regressions."""

import asyncio
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from typer.testing import CliRunner

from michelin_scraper.adapters.google_maps_driver import GoogleMapsDriver, GoogleMapsDriverConfig, GoogleMapsError
from michelin_scraper.adapters.google_maps_sync_writer import GoogleMapsSyncWriter
from michelin_scraper.application.sync_models import ScrapeSyncCommand
from michelin_scraper.application.sync_use_case import AUTH_REQUIRED_EXIT_CODE, _create_sync_writer
from michelin_scraper.application.taiwan_500_maintenance import AwardMapsDriver
from michelin_scraper.config import GOOGLE_CDP_URL_OPTION_FLAGS
from michelin_scraper.entrypoints.cli import app

_CDP_URL = "http://127.0.0.1:9222"
_CLI_COMMANDS = (
    ("sync-michelin", "--target", "taiwan"),
    ("sync-my-maps", "--my-maps-file", "/tmp/export.kml"),
)
_DRIVER_MODULE = "michelin_scraper.adapters.google_maps_driver"


def _driver() -> GoogleMapsDriver:
    return GoogleMapsDriver(GoogleMapsDriverConfig(
        user_data_dir=Path("/unused-profile"),
        headless=True,
        sync_delay_seconds=0,
        google_cdp_url=_CDP_URL,
    ))


def _resources() -> tuple[MagicMock, MagicMock, MagicMock, MagicMock, MagicMock]:
    owned_page = MagicMock()
    owned_page.is_closed.return_value = False
    owned_page.close = AsyncMock()
    owned_page.set_viewport_size = AsyncMock()
    owned_page.content = AsyncMock(return_value="<html>owned page</html>")
    owned_page.locator.return_value.first.wait_for = AsyncMock()
    existing_page = MagicMock()
    existing_page.close = AsyncMock()
    existing_page.content = AsyncMock()
    context = MagicMock()
    context.pages = [existing_page]
    context.new_page = AsyncMock(return_value=owned_page)
    network_session = MagicMock()
    network_session.send = AsyncMock()
    context.new_cdp_session = AsyncMock(return_value=network_session)
    context.close = AsyncMock()
    context.cookies = AsyncMock()
    browser = MagicMock()
    browser.contexts = [context, MagicMock()]
    browser.close = AsyncMock()
    runtime = MagicMock()
    runtime.chromium.connect_over_cdp = AsyncMock(return_value=browser)
    runtime.stop = AsyncMock()
    return runtime, browser, context, owned_page, existing_page


class CdpCliConfigurationTests(unittest.TestCase):
    def test_both_commands_pass_endpoint_and_default_to_no_attachment(self) -> None:
        for cli_command in _CLI_COMMANDS:
            for endpoint in (None, _CDP_URL):
                with self.subTest(command=cli_command[0], endpoint=endpoint):
                    args = list(cli_command)
                    if endpoint is not None:
                        args.extend([GOOGLE_CDP_URL_OPTION_FLAGS[0], endpoint])
                    with patch("michelin_scraper.entrypoints.cli.run_scrape_sync", return_value=0) as sync:
                        result = CliRunner().invoke(app, args)
                    self.assertEqual(result.exit_code, 0, result.output)
                    self.assertEqual(sync.call_args.kwargs["command"].google_cdp_url, endpoint)

    def test_both_command_help_outputs_describe_owned_tab(self) -> None:
        for cli_command in _CLI_COMMANDS:
            with self.subTest(command=cli_command[0]):
                result = CliRunner().invoke(app, [cli_command[0], "--help"])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn(GOOGLE_CDP_URL_OPTION_FLAGS[0], result.stdout)
                self.assertIn("one new tab", " ".join(result.stdout.split()))

    def test_attached_auth_failure_never_prompts_or_launches_profile_login(self) -> None:
        for cli_command in _CLI_COMMANDS:
            with (
                self.subTest(command=cli_command[0]),
                patch("michelin_scraper.entrypoints.cli.run_scrape_sync", return_value=AUTH_REQUIRED_EXIT_CODE) as sync,
                patch("michelin_scraper.entrypoints.cli.run_maps_login") as login,
                patch("michelin_scraper.entrypoints.cli.typer.confirm") as confirm,
            ):
                result = CliRunner().invoke(app, [*cli_command, "--google-cdp-url", _CDP_URL])
                self.assertEqual(result.exit_code, AUTH_REQUIRED_EXIT_CODE, result.output)
                self.assertIn("Sign in to Google Maps in the attached browser", result.output)
                sync.assert_called_once()
                login.assert_not_called()
                confirm.assert_not_called()

    def test_use_case_passes_endpoint_through_real_writer_to_driver(self) -> None:
        for endpoint in (None, _CDP_URL):
            with self.subTest(endpoint=endpoint):
                command = ScrapeSyncCommand(
                    target="taiwan",
                    google_user_data_dir="/unused-profile",
                    levels=("selected",),
                    google_cdp_url=endpoint,
                )
                writer = _create_sync_writer(command, "Taiwan", language="zh_TW")
                self.assertIsInstance(writer, GoogleMapsSyncWriter)
                self.assertIsInstance(writer._driver, GoogleMapsDriver)
                self.assertEqual(writer._driver._config.google_cdp_url, endpoint)


class CdpResourceOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_award_home_recovery_replaces_only_its_owned_tab(self) -> None:
        driver = AwardMapsDriver(_driver()._config)
        runtime, browser, context, owned_page, existing_page = _resources()
        replacement = _resources()[3]
        owned_page.url = replacement.url = "about:blank"
        context.new_page.side_effect = [owned_page, replacement]

        async def opened_home() -> None:
            driver._page.url = "https://www.google.com/maps?hl=en"

        with (patch("playwright.async_api.async_playwright") as playwright,
              patch.object(driver, "_install_interrupt_handler"),
              patch.object(GoogleMapsDriver, "open_maps_home", AsyncMock(side_effect=opened_home))):
            playwright.return_value.start = AsyncMock(return_value=runtime)
            await driver.start()
            await driver.open_maps_home()
            owned_page.close.assert_not_called()
            await driver.open_maps_home()
            owned_page.close.assert_awaited_once_with()
            self.assertIs(driver._page, replacement)
            self.assertEqual(context.new_page.await_count, 2)
            self.assertEqual(runtime.chromium.connect_over_cdp.await_count, 2)
            await driver.close()
        replacement.close.assert_awaited_once_with()
        existing_page.close.assert_not_called()
        context.close.assert_not_called()
        browser.close.assert_not_called()
        browser.new_context.assert_not_called()
        runtime.chromium.launch_persistent_context.assert_not_called()

    async def test_start_and_normal_or_forced_close_own_only_new_page(self) -> None:
        for force in (False, True):
            with self.subTest(force=force):
                driver = _driver()
                runtime, browser, context, owned_page, existing_page = _resources()
                with (
                    patch("playwright.async_api.async_playwright") as playwright,
                    patch(f"{_DRIVER_MODULE}._prepare_user_data_dir_for_automation") as prepare_profile,
                    patch(f"{_DRIVER_MODULE}._force_kill_playwright") as kill_runtime,
                    patch(f"{_DRIVER_MODULE}._force_kill_browser_process") as kill_browser,
                    patch.object(driver, "_install_interrupt_handler"),
                ):
                    playwright.return_value.start = AsyncMock(return_value=runtime)
                    await driver.start()
                    self.assertIs(driver._context, context)
                    self.assertIs(driver._page, owned_page)
                    runtime.chromium.connect_over_cdp.assert_awaited_once_with(_CDP_URL)
                    context.new_page.assert_awaited_once_with()
                    owned_page.set_viewport_size.assert_awaited_once_with({"width": 1280, "height": 720})
                    context.new_cdp_session.assert_awaited_once_with(owned_page)
                    context.new_cdp_session.return_value.send.assert_awaited_once_with(
                        "Network.setBypassServiceWorker", {"bypass": True},
                    )
                    prepare_profile.assert_not_called()
                    runtime.chromium.launch_persistent_context.assert_not_called()
                    browser.new_context.assert_not_called()
                    await driver.close(force=force)
                    kill_runtime.assert_not_called()
                    kill_browser.assert_not_called()
                owned_page.close.assert_awaited_once_with()
                runtime.stop.assert_awaited_once_with()
                context.close.assert_not_called()
                browser.close.assert_not_called()
                existing_page.close.assert_not_called()
                existing_page.set_viewport_size.assert_not_called()
                self.assertIsNone(driver._page)
                self.assertIsNone(driver._context)
                self.assertIsNone(driver._playwright)

    async def test_start_failures_disconnect_without_closing_external_resources(self) -> None:
        for failure in ("connect", "no-context", "new-page", "cancelled"):
            with self.subTest(failure=failure):
                driver = _driver()
                runtime, browser, context, owned_page, existing_page = _resources()
                expected_error = RuntimeError
                if failure == "connect":
                    runtime.chromium.connect_over_cdp.side_effect = RuntimeError("connection failed")
                elif failure == "no-context":
                    browser.contexts = []
                    expected_error = GoogleMapsError
                elif failure == "cancelled":
                    context.new_page.side_effect = asyncio.CancelledError()
                    expected_error = asyncio.CancelledError
                else:
                    context.new_page.side_effect = RuntimeError("new page failed")
                with (
                    patch("playwright.async_api.async_playwright") as playwright,
                    patch(f"{_DRIVER_MODULE}._force_kill_playwright") as kill,
                ):
                    playwright.return_value.start = AsyncMock(return_value=runtime)
                    with self.assertRaises(expected_error):
                        await driver.start()
                    kill.assert_not_called()
                runtime.stop.assert_awaited_once_with()
                context.close.assert_not_called()
                browser.close.assert_not_called()
                owned_page.close.assert_not_called()
                existing_page.close.assert_not_called()

    async def test_owned_page_close_error_still_disconnects_without_killing(self) -> None:
        for force in (False, True):
            with self.subTest(force=force):
                driver = _driver()
                runtime, browser, context, owned_page, existing_page = _resources()
                driver._playwright, driver._context, driver._page = runtime, context, owned_page
                owned_page.close.side_effect = RuntimeError("page close failed")
                with patch(f"{_DRIVER_MODULE}._force_kill_playwright") as kill:
                    if force:
                        await driver.close(force=True)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "page close failed"):
                            await driver.close()
                    kill.assert_not_called()
                runtime.stop.assert_awaited_once_with()
                context.close.assert_not_called()
                browser.close.assert_not_called()
                existing_page.close.assert_not_called()

    def test_second_sigint_never_kills_external_browser(self) -> None:
        driver = _driver()
        original_handler = Mock()
        with (
            patch("signal.getsignal", return_value=original_handler),
            patch("signal.signal") as set_signal,
            patch(f"{_DRIVER_MODULE}._force_kill_browser_process") as kill,
        ):
            driver._install_interrupt_handler()
            handler = set_signal.call_args.args[1]
            handler(signal.SIGINT, None)
            self.assertTrue(driver._interrupt_requested)
            with self.assertRaises(KeyboardInterrupt):
                handler(signal.SIGINT, None)
            kill.assert_not_called()
            driver._uninstall_interrupt_handler()
            set_signal.assert_called_with(signal.SIGINT, original_handler)

    async def test_closed_or_missing_owned_page_never_adopts_or_creates_a_page(self) -> None:
        for missing in (False, True):
            with self.subTest(missing=missing):
                driver = _driver()
                _, _, context, owned_page, existing_page = _resources()
                owned_page.is_closed.return_value = True
                driver._context = context
                driver._page = None if missing else owned_page
                with self.assertRaisesRegex(GoogleMapsError, "owned Google Maps CDP tab"):
                    await driver._require_page()
                context.new_page.assert_not_called()
                existing_page.is_closed.assert_not_called()

    async def test_dump_failure_never_reads_other_tabs_or_creates_a_page(self) -> None:
        for cached_html in (None, "<html>cached owned page</html>"):
            with self.subTest(cached=bool(cached_html)):
                driver = _driver()
                _, _, context, owned_page, existing_page = _resources()
                driver._context, driver._page = context, owned_page
                owned_page.is_closed.return_value = True
                owned_page.content.side_effect = RuntimeError("owned page unavailable")
                driver._last_page_html_snapshot = cached_html
                with tempfile.TemporaryDirectory() as temp_dir:
                    snapshot = Path(temp_dir) / "failure.html"
                    self.assertEqual(await driver.dump_page_html(snapshot), cached_html is not None)
                    if cached_html is not None:
                        self.assertEqual(snapshot.read_text(), cached_html)
                existing_page.content.assert_not_called()
                context.new_page.assert_not_called()

    async def test_ambiguous_authentication_never_reads_session_cookies(self) -> None:
        driver = _driver()
        _, _, context, owned_page, _ = _resources()
        driver._context, driver._page = context, owned_page
        with patch.object(driver, "_is_any_selector_visible", new=AsyncMock(return_value=False)):
            self.assertFalse(await driver.is_authenticated(refresh=False))
        context.cookies.assert_not_called()


if __name__ == "__main__":
    unittest.main()
