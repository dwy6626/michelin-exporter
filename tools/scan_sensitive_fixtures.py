"""Scan fixture files for unredacted sensitive markers."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@dataclass(frozen=True)
class _ScanFinding:
    path: str
    markers: tuple[str, ...]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan tests/fixtures for unredacted account labels, account avatar URLs, "
            "emails, tokens, cookies, and local user paths."
        )
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--all",
        action="store_true",
        help="Scan every file under tests/fixtures from the working tree.",
    )
    mode_group.add_argument(
        "--staged",
        action="store_true",
        help="Scan staged fixture blobs from the git index.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root to scan (default: current directory).",
    )
    return parser


def _is_fixture_path(path: Path) -> bool:
    path_parts = path.as_posix().split("/")
    return len(path_parts) >= 3 and path_parts[0] == "tests" and path_parts[1] == "fixtures"


def _decode_text(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _find_unredacted_sensitive_markers(html_text: str) -> tuple[str, ...]:
    from michelin_scraper.application.html_redaction import find_unredacted_sensitive_markers

    return find_unredacted_sensitive_markers(html_text)


def _working_tree_fixture_entries(root: Path) -> Iterable[tuple[Path, str]]:
    fixtures_root = root / "tests" / "fixtures"
    if not fixtures_root.exists():
        return
    for path in sorted(fixtures_root.rglob("*")):
        if not path.is_file():
            continue
        text = _decode_text(path.read_bytes())
        if text is None:
            continue
        yield path.relative_to(root), text


def _run_git(root: Path, args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
    )


def _staged_fixture_entries(root: Path) -> Iterable[tuple[Path, str]]:
    completed = _run_git(
        root,
        ["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
    )
    for raw_path in completed.stdout.split(b"\0"):
        if not raw_path:
            continue
        rel_path = Path(raw_path.decode("utf-8"))
        if not _is_fixture_path(rel_path):
            continue
        blob = _run_git(root, ["show", f":{rel_path.as_posix()}"]).stdout
        text = _decode_text(blob)
        if text is None:
            continue
        yield rel_path, text


def _scan_entries(entries: Iterable[tuple[Path, str]]) -> list[_ScanFinding]:
    findings: list[_ScanFinding] = []
    for rel_path, text in entries:
        markers = _find_unredacted_sensitive_markers(text)
        if markers:
            findings.append(
                _ScanFinding(
                    path=rel_path.as_posix(),
                    markers=tuple(dict.fromkeys(markers)),
                )
            )
    return findings


def _print_findings(findings: Iterable[_ScanFinding]) -> None:
    print("Sensitive fixture markers found:")
    for finding in findings:
        marker_text = ", ".join(finding.markers)
        print(f"- {finding.path}: {marker_text}")


def main() -> int:
    args = _build_parser().parse_args()
    root = Path(args.root).expanduser().resolve()

    try:
        entries = _staged_fixture_entries(root) if args.staged else _working_tree_fixture_entries(root)
        findings = _scan_entries(entries)
    except subprocess.CalledProcessError as exc:
        command = " ".join(str(part) for part in exc.cmd)
        print(f"Unable to scan staged fixtures because git command failed: {command}", file=sys.stderr)
        return 2

    if findings:
        _print_findings(findings)
        return 1

    print("No unredacted sensitive fixture markers found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
