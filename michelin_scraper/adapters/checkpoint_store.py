"""Checkpoint persistence adapter for resumable scrape runs."""


import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

CHECKPOINT_VERSION = 1


@dataclass(frozen=True)
class ResumeState:
    """Checkpoint resume state used by scrape-sync workflows."""

    next_url: str
    next_page_number: int
    total_restaurants: int
    estimated_total_pages: int | None
    rows_per_level: dict[str, int]
    synced_row_keys: frozenset[str] = frozenset()


class JsonCheckpointStore:
    """Read and write run checkpoint payloads as JSON."""

    def __init__(
        self,
        path: Path,
        level_slugs: Iterable[str],
        version: int = CHECKPOINT_VERSION,
    ) -> None:
        self._path = path
        self._level_slugs = tuple(level_slugs)
        self._version = version
        self._pending_synced_row_keys: set[str] = set()

    def clear(self) -> None:
        self._pending_synced_row_keys.clear()
        if self._path.exists():
            self._path.unlink()

    def initialize_synced_row_keys(self, row_keys: frozenset[str]) -> None:
        """Seed in-memory synced set from a loaded checkpoint."""
        self._pending_synced_row_keys = set(row_keys)

    def add_synced_row(self, row_key: str) -> None:
        """Record a successfully synced row key in memory."""
        self._pending_synced_row_keys.add(row_key)

    def load(self, expected_start_url: str) -> tuple[ResumeState | None, str | None]:
        if not self._path.exists():
            return None, None

        try:
            raw_data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, "checkpoint file is malformed."

        if not isinstance(raw_data, dict):
            return None, "checkpoint payload is not a JSON object."

        if raw_data.get("version") != self._version:
            return None, "checkpoint version does not match the current schema."

        if raw_data.get("start_url") != expected_start_url:
            return None, "checkpoint target does not match the requested target."

        next_url = raw_data.get("next_url")
        if not isinstance(next_url, str) or not next_url:
            return None, "checkpoint next URL is missing or invalid."

        next_page_number = raw_data.get("next_page_number")
        if not isinstance(next_page_number, int) or next_page_number < 1:
            return None, "checkpoint next page number is invalid."

        total_restaurants = raw_data.get("total_restaurants")
        if not isinstance(total_restaurants, int) or total_restaurants < 0:
            return None, "checkpoint total restaurant count is invalid."

        estimated_total_pages = raw_data.get("estimated_total_pages")
        if estimated_total_pages is not None and (
            not isinstance(estimated_total_pages, int) or estimated_total_pages < 1
        ):
            return None, "checkpoint total-page estimate is invalid."

        rows_per_level = self._validate_rows_per_level(raw_data.get("rows_per_level"))
        if rows_per_level is None:
            return None, "checkpoint row counters are invalid."

        raw_keys = raw_data.get("synced_row_keys", [])
        synced_row_keys: frozenset[str] = frozenset(
            k for k in raw_keys if isinstance(k, str)
        ) if isinstance(raw_keys, list) else frozenset()

        return (
            ResumeState(
                next_url=next_url,
                next_page_number=next_page_number,
                total_restaurants=total_restaurants,
                estimated_total_pages=estimated_total_pages,
                rows_per_level=rows_per_level,
                synced_row_keys=synced_row_keys,
            ),
            None,
        )

    def save(
        self,
        *,
        start_url: str,
        page_number: int,
        page_url: str,
        next_url: str,
        next_page_number: int,
        estimated_total_pages: int | None,
        total_restaurants: int,
        rows_per_level: dict[str, int],
    ) -> None:
        payload = {
            "version": self._version,
            "start_url": start_url,
            "last_page_number": page_number,
            "last_page_url": page_url,
            "next_url": next_url,
            "next_page_number": next_page_number,
            "estimated_total_pages": estimated_total_pages,
            "total_restaurants": total_restaurants,
            "rows_per_level": rows_per_level,
            "synced_row_keys": sorted(self._pending_synced_row_keys),
        }
        temporary_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        serialized = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)
        temporary_path.write_text(serialized + "\n", encoding="utf-8")
        temporary_path.replace(self._path)

    def _validate_rows_per_level(self, raw_value: object) -> dict[str, int] | None:
        if not isinstance(raw_value, dict):
            return None

        normalized: dict[str, int] = {level_slug: 0 for level_slug in self._level_slugs}
        for level_slug in self._level_slugs:
            candidate = raw_value.get(level_slug, 0)
            if not isinstance(candidate, int) or candidate < 0:
                return None
            normalized[level_slug] = candidate
        return normalized
