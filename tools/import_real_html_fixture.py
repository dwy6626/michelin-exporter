"""Import real-page HTML snapshots into de-identified test fixtures."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

from michelin_scraper.application.html_redaction import (
    find_unredacted_sensitive_markers,
    redact_html_text,
)

_FIXTURE_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)([?&]key=)(?:AIza[0-9A-Za-z_-]{20,}|[^&#\"'\s]+)"),
        r"\1AIzaSyFAKE_KEY_FOR_TESTING_ONLY_00000000",
    ),
    (
        re.compile(r'(<div class="gb_g">)[^<]*(</div>)'),
        r"\1<redacted-account-name>\2",
    ),
)

_FIXTURE_UNREDACTED_MARKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "google-api-key",
        re.compile(r"(?i)[?&]key=AIza(?!SyFAKE_KEY_FOR_TESTING_ONLY_00000000)[0-9A-Za-z_-]{20,}"),
    ),
    (
        "google-account-name",
        re.compile(r'(?s)<div class="gb_g">(?!<redacted-account-name>)[^<]+</div>'),
    ),
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import real HTML snapshots into tests/fixtures with de-identification "
            "and metadata sidecar files."
        )
    )
    parser.add_argument(
        "--source",
        help="Source raw HTML file path for single-file import.",
    )
    parser.add_argument(
        "--source-dir",
        help="Directory containing raw HTML files for batch import.",
    )
    parser.add_argument(
        "--fixture-set",
        required=True,
        choices=("michelin", "google_maps"),
        help="Fixture namespace under tests/fixtures.",
    )
    parser.add_argument(
        "--name",
        default="",
        help="Fixture basename (without extension). Required when --source is used.",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Language tag for metadata (default: en).",
    )
    parser.add_argument(
        "--scenario",
        default="",
        help="Scenario label for metadata (default: fixture name).",
    )
    parser.add_argument(
        "--captured-at",
        default=date.today().isoformat(),
        help="Capture date in YYYY-MM-DD (default: today).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing fixture files if present.",
    )
    return parser


def _write_text_file(path: Path, text: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    path.write_text(text, encoding="utf-8")


def _resolve_import_entries(args: argparse.Namespace) -> list[tuple[Path, str]]:
    source_arg = str(args.source or "").strip()
    source_dir_arg = str(args.source_dir or "").strip()
    has_source = bool(source_arg)
    has_source_dir = bool(source_dir_arg)
    if has_source == has_source_dir:
        raise ValueError("Specify exactly one of --source or --source-dir.")

    fixture_name = str(args.name or "").strip()
    if has_source:
        if not fixture_name:
            raise ValueError("--name is required when using --source.")
        source_path = Path(source_arg).expanduser()
        if not source_path.exists():
            raise FileNotFoundError(f"Source file does not exist: {source_path}")
        if not source_path.is_file():
            raise ValueError(f"Source path is not a file: {source_path}")
        return [(source_path, fixture_name)]

    if fixture_name:
        raise ValueError("--name is only supported with --source.")
    source_dir_path = Path(source_dir_arg).expanduser()
    if not source_dir_path.exists():
        raise FileNotFoundError(f"Source directory does not exist: {source_dir_path}")
    if not source_dir_path.is_dir():
        raise ValueError(f"Source directory path is not a directory: {source_dir_path}")
    html_paths = sorted(
        path for path in source_dir_path.glob("*.html") if path.is_file()
    )
    if not html_paths:
        raise ValueError(f"No .html files found in source directory: {source_dir_path}")
    return [(path, path.stem) for path in html_paths]


def _apply_fixture_redaction(html_text: str) -> str:
    redacted_text = str(html_text)
    for pattern, replacement in _FIXTURE_REDACTION_PATTERNS:
        redacted_text = pattern.sub(replacement, redacted_text)
    return redacted_text


def _find_unredacted_fixture_markers(html_text: str) -> tuple[str, ...]:
    markers: list[str] = []
    for label, pattern in _FIXTURE_UNREDACTED_MARKER_PATTERNS:
        if pattern.search(html_text):
            markers.append(label)
    return tuple(markers)


def _import_single_fixture(
    *,
    source_path: Path,
    fixture_set: str,
    fixture_name: str,
    language: str,
    scenario: str,
    captured_at: str,
    overwrite: bool,
) -> tuple[Path, Path]:
    fixture_base = fixture_name.strip()
    if not fixture_base:
        raise ValueError("Fixture name cannot be empty.")

    source_html = source_path.read_text(encoding="utf-8")
    redacted_html = redact_html_text(source_html)
    redacted_html = _apply_fixture_redaction(redacted_html)
    markers = find_unredacted_sensitive_markers(redacted_html)
    fixture_markers = _find_unredacted_fixture_markers(redacted_html)
    all_markers = tuple(dict.fromkeys((*markers, *fixture_markers)))
    if all_markers:
        marker_text = ", ".join(all_markers)
        raise ValueError(
            "Refusing to write fixture because sensitive markers remain after redaction: "
            f"{marker_text}"
        )

    fixtures_root = Path("tests") / "fixtures" / fixture_set
    fixtures_root.mkdir(parents=True, exist_ok=True)
    html_path = fixtures_root / f"{fixture_base}.html"
    metadata_path = fixtures_root / f"{fixture_base}.metadata.json"
    metadata = {
        "scenario": scenario.strip() or fixture_base,
        "captured_at": captured_at,
        "language": language.strip() or "en",
        "source": "real-page-snapshot",
        "sanitized": True,
    }

    _write_text_file(html_path, redacted_html, overwrite=overwrite)
    _write_text_file(
        metadata_path,
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        overwrite=overwrite,
    )
    return html_path, metadata_path


def main() -> int:
    args = _build_parser().parse_args()

    entries = _resolve_import_entries(args)
    for source_path, fixture_name in entries:
        html_path, metadata_path = _import_single_fixture(
            source_path=source_path,
            fixture_set=args.fixture_set,
            fixture_name=fixture_name,
            language=args.language,
            scenario=args.scenario,
            captured_at=args.captured_at,
            overwrite=args.overwrite,
        )
        print(f"Wrote fixture HTML: {html_path}")
        print(f"Wrote fixture metadata: {metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
