"""Tests for Google Maps login use-case helpers."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from michelin_scraper.application.maps_login_use_case import (
    _build_manual_login_hint,
    _resolve_login_debug_html_path,
)


class MapsLoginUseCaseHelperTests(unittest.TestCase):
    def test_manual_login_hint_on_macos(self) -> None:
        with patch("michelin_scraper.application.maps_login_use_case.sys.platform", "darwin"):
            hint = _build_manual_login_hint(Path("/tmp/profile"))
        self.assertIn("open -na \"Google Chrome\"", hint)
        self.assertIn("--user-data-dir=\"/tmp/profile\"", hint)

    def test_manual_login_hint_on_non_macos(self) -> None:
        with patch("michelin_scraper.application.maps_login_use_case.sys.platform", "linux"):
            hint = _build_manual_login_hint(Path("/tmp/profile"))
        self.assertIn("chrome --user-data-dir=\"/tmp/profile\"", hint)

    def test_resolve_login_debug_html_path_uses_profile_debug_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = Path(temp_dir) / "profile"
            with patch(
                "michelin_scraper.application.maps_login_use_case.time.time",
                return_value=1700000000.123,
            ):
                debug_html_path = _resolve_login_debug_html_path(profile_path, "login blocked")

        self.assertEqual(debug_html_path.parent.name, "debug")
        self.assertEqual(debug_html_path.suffix, ".html")
        self.assertIn("maps-login-debug-login-blocked-1700000000123", debug_html_path.name)


if __name__ == "__main__":
    unittest.main()
