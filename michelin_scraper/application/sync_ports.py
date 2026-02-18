"""Ports for maps sync use-cases."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .sync_models import SyncBatchResult, SyncRowResult, SyncSummary


class ProgressOutputPort(Protocol):
    """Reporter interface used by the scraper to emit progress updates."""

    def update(self, message: str, progress: float | None = None) -> None:
        """Update in-progress status."""
        ...

    def log(self, message: str) -> None:
        """Write an informational status line."""
        ...

    def finish(self, message: str | None = None) -> None:
        """Finalize progress reporting."""
        ...


class ResumeStatePort(Protocol):
    """Checkpoint state contract consumed by application services."""

    @property
    def next_url(self) -> str:
        """Next page URL that should be scraped."""
        ...

    @property
    def next_page_number(self) -> int:
        """Next page number that should be scraped."""
        ...

    @property
    def total_restaurants(self) -> int:
        """Total rows processed up to the saved checkpoint."""
        ...

    @property
    def estimated_total_pages(self) -> int | None:
        """Estimated total page count captured at checkpoint time."""
        ...

    @property
    def rows_per_level(self) -> Mapping[str, int]:
        """Accumulated row counts grouped by level slug."""
        ...

    @property
    def synced_row_keys(self) -> frozenset[str]:
        """Row keys that have been successfully synced to Maps."""
        ...


class CheckpointStorePort(Protocol):
    """Persistence contract for resumable checkpoint storage."""

    def load(
        self,
        expected_start_url: str,
    ) -> tuple[ResumeStatePort | None, str | None]:
        """Load and validate checkpoint state."""
        ...

    def clear(self) -> None:
        """Remove checkpoint state."""
        ...

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
        """Persist checkpoint state."""
        ...


class LevelSyncWriterPort(Protocol):
    """Output persistence contract for level-based Maps syncing."""

    @property
    def list_names_by_level(self) -> Mapping[str, str]:
        """Return immutable mapping of level slug to list name."""
        ...

    @property
    def missing_row_counts_by_level(self) -> Mapping[str, int]:
        """Return rows skipped because a target list went missing."""
        ...

    async def initialize_run(self, *, scope_name: str, level_slugs: Sequence[str]) -> None:
        """Bootstrap destination state before scraping starts."""
        ...

    async def sync_row(
        self,
        level_slug: str,
        row: dict[str, Any],
    ) -> SyncRowResult:
        """Sync a single row into a Maps list."""
        ...

    async def sync_rows_by_level(
        self,
        rows_by_level: Mapping[str, list[dict[str, Any]]],
    ) -> SyncBatchResult:
        """Sync page rows into Maps lists by level."""
        ...

    async def finalize_run(self) -> None:
        """Finalize writer resources."""
        ...


class SyncOutputPort(Protocol):
    """Output port for CLI-facing sync messages."""

    def warn(self, message: str) -> None:
        """Render a warning message."""
        ...

    def show_resume(
        self,
        next_page_number: int,
        next_url: str,
        scraped_before_resume: int = 0,
        synced_before_resume: int = 0,
        rows_per_level: Mapping[str, int] | None = None,
    ) -> None:
        """Render resume information."""
        ...

    def show_interrupted(
        self,
        *,
        scraped_total: int = 0,
        added_total: int = 0,
        failed_total: int = 0,
        skipped_total: int = 0,
    ) -> None:
        """Render interruption information."""
        ...

    def show_failure(self, message: str) -> None:
        """Render failure information."""
        ...

    def show_final_results(self, summary: SyncSummary) -> None:
        """Render end-of-run summary."""
        ...

    def create_progress_reporter(self) -> ProgressOutputPort:
        """Build the progress reporter used during scraping."""
        ...


class LoginOutputPort(Protocol):
    """Output port for interactive login workflow messages."""

    def warn(self, message: str) -> None:
        """Render a warning/info message."""
        ...

    def show_failure(self, message: str) -> None:
        """Render failure information."""
        ...
