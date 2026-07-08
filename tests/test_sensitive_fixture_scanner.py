"""Tests for local sensitive fixture scanners."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCANNER_PATH = _REPO_ROOT / "tools" / "scan_sensitive_fixtures.py"
_HISTORY_SCANNER_PATH = _REPO_ROOT / "tools" / "scan_sensitive_git_history.py"


class SensitiveFixtureScannerTests(unittest.TestCase):
    def test_all_mode_reports_account_avatar_without_printing_html_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_dir = root / "tests" / "fixtures" / "google_maps"
            fixture_dir.mkdir(parents=True, exist_ok=True)
            account_avatar_url = "https://lh3.googleusercontent.com/ogw/ACCOUNT_AVATAR_TOKEN=s64-c"
            (fixture_dir / "leak.html").write_text(
                f'<html><body><img src="{account_avatar_url}"></body></html>',
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(_SCANNER_PATH), "--all", "--root", str(root)],
                check=False,
                capture_output=True,
                text=True,
            )

            combined_output = completed.stdout + completed.stderr
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("tests/fixtures/google_maps/leak.html", combined_output)
            self.assertIn("google-account-avatar-url", combined_output)
            self.assertNotIn(account_avatar_url, combined_output)
            self.assertNotIn("<img", combined_output)

    def test_all_mode_ignores_public_reviewer_contrib_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_dir = root / "tests" / "fixtures" / "google_maps"
            fixture_dir.mkdir(parents=True, exist_ok=True)
            (fixture_dir / "reviewer.html").write_text(
                (
                    "<html><body>"
                    '<a href="https://www.google.com/maps/contrib/112233445566778899001/reviews">'
                    "Public Reviewer"
                    "</a>"
                    '<img src="https://lh3.googleusercontent.com/a-/PUBLIC_REVIEWER_AVATAR=s64-c">'
                    "</body></html>"
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(_SCANNER_PATH), "--all", "--root", str(root)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_staged_mode_scans_index_blob_not_working_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            fixture_path = root / "tests" / "fixtures" / "google_maps" / "leak.html"
            fixture_path.parent.mkdir(parents=True, exist_ok=True)
            account_avatar_url = "https://lh3.googleusercontent.com/ogw/STAGED_ACCOUNT_AVATAR_TOKEN=s64-c"
            fixture_path.write_text(
                f'<html><body><img src="{account_avatar_url}"></body></html>',
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "tests/fixtures/google_maps/leak.html"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            fixture_path.write_text("<html><body>clean working tree copy</body></html>", encoding="utf-8")

            completed = subprocess.run(
                [sys.executable, str(_SCANNER_PATH), "--staged", "--root", str(root)],
                check=False,
                capture_output=True,
                text=True,
            )

            combined_output = completed.stdout + completed.stderr
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("tests/fixtures/google_maps/leak.html", combined_output)
            self.assertIn("google-account-avatar-url", combined_output)
            self.assertNotIn(account_avatar_url, combined_output)
            self.assertNotIn("<img", combined_output)

    def test_git_history_scanner_reports_sensitive_fixture_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "config", "user.name", "Fixture Scanner Test"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "fixture-scanner@example.invalid"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            fixture_path = root / "tests" / "fixtures" / "google_maps" / "leak.html"
            fixture_path.parent.mkdir(parents=True, exist_ok=True)
            account_avatar_url = "https://lh3.googleusercontent.com/ogw/HISTORY_ACCOUNT_AVATAR_TOKEN=s64-c"
            fixture_path.write_text(
                f'<html><body><img src="{account_avatar_url}"></body></html>',
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "tests/fixtures/google_maps/leak.html"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "add fixture"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )

            completed = subprocess.run(
                [sys.executable, str(_HISTORY_SCANNER_PATH), "--root", str(root)],
                check=False,
                capture_output=True,
                text=True,
            )

            combined_output = completed.stdout + completed.stderr
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("tests/fixtures/google_maps/leak.html", combined_output)
            self.assertIn("google-account-avatar-url", combined_output)
            self.assertNotIn(account_avatar_url, combined_output)
            self.assertNotIn("<img", combined_output)


if __name__ == "__main__":
    unittest.main()
