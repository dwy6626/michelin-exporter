"""Scan committed fixture blobs for unredacted sensitive markers."""

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
class _HistoryFinding:
    path: str
    object_id: str
    markers: tuple[str, ...]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan committed tests/fixtures blobs for unredacted account labels, "
            "account avatar URLs, emails, tokens, cookies, and local user paths."
        )
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root to scan (default: current directory).",
    )
    return parser


def _run_git(root: Path, args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
    )


def _decode_text(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _find_unredacted_sensitive_markers(html_text: str) -> tuple[str, ...]:
    from michelin_scraper.application.html_redaction import find_unredacted_sensitive_markers

    return find_unredacted_sensitive_markers(html_text)


def _fixture_blob_entries(root: Path) -> Iterable[tuple[str, str, str]]:
    completed = _run_git(root, ["rev-list", "--objects", "--all"])
    for raw_line in completed.stdout.splitlines():
        line = raw_line.decode("utf-8", errors="replace")
        object_id, separator, path = line.partition(" ")
        if not separator:
            continue
        if not path.startswith("tests/fixtures/"):
            continue
        blob = _run_git(root, ["cat-file", "-p", object_id]).stdout
        text = _decode_text(blob)
        if text is None:
            continue
        yield object_id, path, text


def _scan_history(root: Path) -> list[_HistoryFinding]:
    findings: list[_HistoryFinding] = []
    seen: set[tuple[str, str]] = set()
    for object_id, path, text in _fixture_blob_entries(root):
        markers = _find_unredacted_sensitive_markers(text)
        if not markers:
            continue
        key = (object_id, path)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            _HistoryFinding(
                path=path,
                object_id=object_id[:12],
                markers=tuple(dict.fromkeys(markers)),
            )
        )
    return findings


def _print_findings(findings: Iterable[_HistoryFinding]) -> None:
    print("Sensitive fixture markers found in git history:")
    for finding in findings:
        marker_text = ", ".join(finding.markers)
        print(f"- {finding.path} @ {finding.object_id}: {marker_text}")


def main() -> int:
    args = _build_parser().parse_args()
    root = Path(args.root).expanduser().resolve()

    try:
        findings = _scan_history(root)
    except subprocess.CalledProcessError as exc:
        command = " ".join(str(part) for part in exc.cmd)
        print(f"Unable to scan git history because git command failed: {command}", file=sys.stderr)
        return 2

    if findings:
        _print_findings(findings)
        return 1

    print("No unredacted sensitive fixture markers found in git history.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
