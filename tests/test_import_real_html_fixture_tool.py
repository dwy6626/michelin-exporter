"""Tests for tools/import_real_html_fixture.py."""

import importlib.util
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_TOOL_PATH = Path(__file__).resolve().parent.parent / "tools" / "import_real_html_fixture.py"
_TOOL_SPEC = importlib.util.spec_from_file_location("import_real_html_fixture", _TOOL_PATH)
if _TOOL_SPEC is None or _TOOL_SPEC.loader is None:
    raise RuntimeError(f"Unable to load fixture import tool from {_TOOL_PATH}")
_TOOL_MODULE = importlib.util.module_from_spec(_TOOL_SPEC)
_TOOL_SPEC.loader.exec_module(_TOOL_MODULE)


@contextmanager
def _pushd(path: Path):
    current_dir = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(current_dir)


class ImportRealHtmlFixtureToolTests(unittest.TestCase):
    def test_source_dir_batch_import_writes_redacted_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            source_dir = workspace / "debug"
            source_dir.mkdir(parents=True, exist_ok=True)
            (source_dir / "alpha.html").write_text(
                "<html><body>contact qa.user@example.com</body></html>",
                encoding="utf-8",
            )
            (source_dir / "beta.html").write_text(
                "<html><body>SID=abc123</body></html>",
                encoding="utf-8",
            )

            argv = [
                "import_real_html_fixture.py",
                "--source-dir",
                str(source_dir),
                "--fixture-set",
                "google_maps",
                "--captured-at",
                "2026-02-17",
            ]
            with _pushd(workspace):
                with patch("sys.argv", argv):
                    exit_code = _TOOL_MODULE.main()

            self.assertEqual(exit_code, 0)
            fixtures_root = workspace / "tests" / "fixtures" / "google_maps"
            alpha_html_path = fixtures_root / "alpha.html"
            beta_html_path = fixtures_root / "beta.html"
            self.assertTrue(alpha_html_path.exists())
            self.assertTrue(beta_html_path.exists())
            self.assertIn("<redacted-email>", alpha_html_path.read_text(encoding="utf-8"))
            self.assertIn("SID=<redacted>", beta_html_path.read_text(encoding="utf-8"))
            self.assertTrue((fixtures_root / "alpha.metadata.json").exists())
            self.assertTrue((fixtures_root / "beta.metadata.json").exists())

    def test_source_dir_batch_import_redacts_google_account_name_and_maps_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            source_dir = workspace / "debug"
            source_dir.mkdir(parents=True, exist_ok=True)
            (source_dir / "google.html").write_text(
                (
                    "<html><head>"
                    '<meta content="https://maps.google.com/maps/api/staticmap?key='
                    "AIzaSyBoYjeRtfVI0Jd8Q_9mnflo9i4sOYpShB0"
                    '&signature=abc">'
                    '<meta content="https://maps.google.com/maps/api/staticmap?center=1,2&amp;key='
                    "AIzaSyBoYjeRtfVI0Jd8Q_9mnflo9i4sOYpShB0"
                    '&amp;signature=def">'
                    "</head><body>"
                    '<div class="gb_g"><redacted-account-name></div>'
                    "</body></html>"
                ),
                encoding="utf-8",
            )

            argv = [
                "import_real_html_fixture.py",
                "--source-dir",
                str(source_dir),
                "--fixture-set",
                "google_maps",
                "--captured-at",
                "2026-02-17",
            ]
            with _pushd(workspace):
                with patch("sys.argv", argv):
                    exit_code = _TOOL_MODULE.main()

            self.assertEqual(exit_code, 0)
            fixtures_root = workspace / "tests" / "fixtures" / "google_maps"
            html_path = fixtures_root / "google.html"
            self.assertTrue(html_path.exists())
            html_text = html_path.read_text(encoding="utf-8")
            self.assertNotIn("AIzaSyBoYjeRtfVI0Jd8Q_9mnflo9i4sOYpShB0", html_text)
            self.assertNotIn('<div class="gb_g"><redacted-account-name></div>', html_text)
            self.assertIn("key=AIzaSyFAKE_KEY_FOR_TESTING_ONLY_00000000", html_text)
            self.assertIn("&amp;key=AIzaSyFAKE_KEY_FOR_TESTING_ONLY_00000000", html_text)
            self.assertIn('<div class="gb_g"><redacted-account-name></div>', html_text)


if __name__ == "__main__":
    unittest.main()
