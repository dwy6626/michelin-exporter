"""Redact sensitive markers from existing fixture files in place."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path

from michelin_scraper.application.html_redaction import (
    find_unredacted_sensitive_markers,
    redact_html_text,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply shared HTML redaction to fixture files and refuse incomplete output."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Fixture files to redact. Defaults to every text file under tests/fixtures.",
    )
    return parser


def _default_fixture_paths() -> Iterable[Path]:
    fixtures_root = Path("tests") / "fixtures"
    if not fixtures_root.exists():
        return
    for path in sorted(fixtures_root.rglob("*")):
        if path.is_file():
            yield path


def _input_paths(path_args: Iterable[str]) -> Iterable[Path]:
    explicit_paths = [Path(path).expanduser() for path in path_args]
    if explicit_paths:
        yield from explicit_paths
        return
    yield from _default_fixture_paths()


def _redact_path(path: Path) -> bool:
    try:
        original_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False

    redacted_text = redact_html_text(original_text)
    markers = find_unredacted_sensitive_markers(redacted_text)
    if markers:
        marker_text = ", ".join(markers)
        raise ValueError(f"Sensitive markers remain in {path}: {marker_text}")
    if redacted_text == original_text:
        return False
    path.write_text(redacted_text, encoding="utf-8")
    return True


def main() -> int:
    args = _build_parser().parse_args()
    changed_paths: list[Path] = []
    for path in _input_paths(args.paths):
        if _redact_path(path):
            changed_paths.append(path)

    for path in changed_paths:
        print(f"Redacted fixture: {path}")
    print(f"Redacted {len(changed_paths)} fixture file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
