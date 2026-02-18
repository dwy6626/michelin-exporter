"""Use-case for interactive Google Maps login bootstrap."""

import asyncio
import sys
import time
from pathlib import Path

from ..adapters import safe_filename
from ..adapters.google_maps_driver import (
    GoogleMapsDriver,
    GoogleMapsDriverConfig,
    GoogleMapsLoginBlockedError,
)
from ..config import GOOGLE_MAPS_HOME_URL
from .html_redaction import redact_html_text
from .sync_models import MapsLoginCommand
from .sync_ports import LoginOutputPort


def run_maps_login(command: MapsLoginCommand, output: LoginOutputPort) -> int:
    """Bootstrap a persistent authenticated Google Maps session."""

    if command.headless:
        output.show_failure("Interactive Google login requires headed mode. Use --headed.")
        return 2

    return asyncio.run(_run_maps_login_async(command, output))


async def _run_maps_login_async(command: MapsLoginCommand, output: LoginOutputPort) -> int:
    """Async implementation of the Maps login bootstrap flow."""

    driver = GoogleMapsDriver(
        GoogleMapsDriverConfig(
            user_data_dir=Path(command.google_user_data_dir).expanduser(),
            headless=False,
            sync_delay_seconds=0.6,
        )
    )
    try:
        await driver.start()
        await driver.open_maps_home()
        if await driver.is_authenticated(refresh=False):
            output.warn("Google Maps session is already authenticated.")
            return 0

        output.warn(
            (
                "Complete Google login in the opened browser window. "
                "The command will finish when authentication is detected."
            ),
        )
        if not await driver.wait_for_authenticated(command.login_timeout_seconds):
            await _capture_login_debug_html(
                driver=driver,
                profile_path=Path(command.google_user_data_dir).expanduser(),
                context="login-timeout",
                output=output,
            )
            output.show_failure(
                (
                    "Login timeout reached before authentication was detected. "
                    "Retry the command and complete sign-in."
                ),
            )
            return 1

        output.warn("Google Maps login bootstrap completed successfully.")
        return 0
    except GoogleMapsLoginBlockedError as exc:
        profile_path = Path(command.google_user_data_dir).expanduser()
        await _capture_login_debug_html(
            driver=driver,
            profile_path=profile_path,
            context="login-blocked",
            output=output,
        )
        manual_login_hint = _build_manual_login_hint(profile_path)
        output.show_failure(
            (
                f"{exc}\n"
                "Fix:\n"
                "  1. Install or update Google Chrome.\n"
                "  2. Close all Chrome windows.\n"
                "  3. Re-run this command.\n"
                "If needed, use a fresh profile with --google-user-data-dir.\n"
                f"{manual_login_hint}"
            ),
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        await _capture_login_debug_html(
            driver=driver,
            profile_path=Path(command.google_user_data_dir).expanduser(),
            context=exc.__class__.__name__,
            output=output,
        )
        output.show_failure(f"Interactive login failed: {exc}")
        return 1
    finally:
        await driver.close()


def _build_manual_login_hint(profile_path: Path) -> str:
    profile_text = str(profile_path)
    if sys.platform == "darwin":
        return (
            "Manual fallback:\n"
            "  Login once using regular Chrome with the same profile path, then retry sync:\n"
            f"  open -na \"Google Chrome\" --args --user-data-dir=\"{profile_text}\" "
            f"\"{GOOGLE_MAPS_HOME_URL}\""
        )

    return (
        "Manual fallback:\n"
        "  Login once using regular Chrome with the same profile path, then retry sync:\n"
        f"  chrome --user-data-dir=\"{profile_text}\" \"{GOOGLE_MAPS_HOME_URL}\""
    )


def _resolve_login_debug_html_path(profile_path: Path, context: str) -> Path:
    safe_context = safe_filename(context)
    timestamp_ms = int(time.time() * 1000)
    debug_dir = profile_path / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    return debug_dir / f"maps-login-debug-{safe_context}-{timestamp_ms}.html"


async def _capture_login_debug_html(
    *,
    driver: GoogleMapsDriver,
    profile_path: Path,
    context: str,
    output: LoginOutputPort,
) -> Path | None:
    html_path = _resolve_login_debug_html_path(profile_path, context)
    if not await driver.dump_page_html(html_path):
        output.warn("Login debug HTML snapshot was not written because no active page was available.")
        return None
    try:
        raw_html = html_path.read_text(encoding="utf-8")
        redacted_html = redact_html_text(raw_html)
        if redacted_html != raw_html:
            html_path.write_text(redacted_html, encoding="utf-8")
            output.warn(f"Login debug HTML snapshot written to: {html_path} (de-identified).")
            return html_path
    except Exception as exc:  # noqa: BLE001
        output.warn(f"Failed to de-identify login debug HTML snapshot: {exc}")
    output.warn(f"Login debug HTML snapshot written to: {html_path}")
    return html_path
