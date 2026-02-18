"""Application-level models for Google Maps sync workflows."""

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..config import (
    DEFAULT_LANGUAGE,
    DEFAULT_LIST_NAME_PREFIX,
    DEFAULT_LIST_NAME_TEMPLATE,
    DEFAULT_MAX_PAGES,
    DEFAULT_MAX_ROWS_PER_PAGE,
    DEFAULT_MAX_SAVE_RETRIES,
    DEFAULT_MISSING_LIST_POLICY,
    DEFAULT_RECORD_FIXTURES_DIR,
    DEFAULT_SLEEP_SECONDS,
    DEFAULT_SYNC_DELAY_SECONDS,
)
from ..domain import ScrapeRunMetrics
from .sync_enums import SyncRowStatus


@dataclass(frozen=True)
class ScrapeSyncCommand:
    """Input command accepted by the maps sync use-case."""

    target: str
    google_user_data_dir: str
    levels: tuple[str, ...]
    language: str = DEFAULT_LANGUAGE
    state_dir: str = ""
    ignore_checkpoint: bool = False
    debug_sync_failures: bool = False
    max_pages: int = DEFAULT_MAX_PAGES
    max_pages_specified: bool = False
    max_rows_per_page: int = DEFAULT_MAX_ROWS_PER_PAGE
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS
    sync_delay_seconds: float = DEFAULT_SYNC_DELAY_SECONDS
    max_save_retries: int = DEFAULT_MAX_SAVE_RETRIES
    headless: bool = False
    dry_run: bool = False
    sandbox: bool = False
    maps_probe_only: bool = False
    maps_probe_rows_file: str = ""
    record_fixtures_dir: str = DEFAULT_RECORD_FIXTURES_DIR
    list_name_prefix: str = DEFAULT_LIST_NAME_PREFIX
    list_name_template: str = DEFAULT_LIST_NAME_TEMPLATE
    on_missing_list: str = DEFAULT_MISSING_LIST_POLICY
    ignore_existing_lists_check: bool = False
    ca_bundle: str = ""
    insecure: bool = False


@dataclass(frozen=True)
class MapsLoginCommand:
    """Interactive login bootstrap command for Google Maps."""

    google_user_data_dir: str
    login_timeout_seconds: int
    headless: bool = False


@dataclass(frozen=True)
class ResumePlan:
    """Resolved start point for the current scrape run."""

    start_scrape_url: str
    start_page_number: int
    start_estimated_total_pages: int | None
    initial_total_restaurants: int
    row_counts: dict[str, int]
    synced_row_keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SyncItemFailure:
    """One failed row sync outcome."""

    level_slug: str
    row_key: str
    restaurant_name: str
    reason: str
    attempted_queries: tuple[str, ...]


@dataclass(frozen=True)
class SyncBatchResult:
    """Aggregate sync result for one page write batch."""

    added_count_by_level: Mapping[str, int]
    skipped_count_by_level: Mapping[str, int]
    failed_items: Sequence[SyncItemFailure]


@dataclass(frozen=True)
class SyncRowResult:
    """Sync result for one row write."""

    status: SyncRowStatus
    failure: SyncItemFailure | None = None


@dataclass
class SyncAccumulation:
    """Mutable in-memory state accumulated during one sync run."""

    scraped_count_by_level: dict[str, int]
    added_count_by_level: dict[str, int]
    skipped_count_by_level: dict[str, int]
    sample_rows: list[dict[str, Any]]
    failed_items: list[SyncItemFailure]


@dataclass(frozen=True)
class MissingListReport:
    """Final report entry for one list that became unavailable during a run."""

    level_slug: str
    list_name: str
    skipped_rows: int


@dataclass(frozen=True)
class SyncSummary:
    """Final summary payload rendered by sync output adapters."""

    metrics: ScrapeRunMetrics
    sample_rows: Sequence[dict[str, Any]]
    scraped_count_by_level: Mapping[str, int]
    added_count_by_level: Mapping[str, int]
    skipped_count_by_level: Mapping[str, int]
    failed_items: Sequence[SyncItemFailure]
    missing_lists: Sequence[MissingListReport]
    list_names_by_level: Mapping[str, str]
    output_targets: Sequence[tuple[str, str]]
    elapsed_seconds: float
    resumed_synced_count: int = 0
    resumed_scraped_count_by_level: Mapping[str, int] = dataclasses.field(default_factory=dict)


def create_empty_row_counts(level_slugs: Sequence[str]) -> dict[str, int]:
    """Create a fresh row counter map for all levels."""

    return {level_slug: 0 for level_slug in level_slugs}
