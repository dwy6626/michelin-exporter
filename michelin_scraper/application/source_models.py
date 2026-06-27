"""Generic source contracts for syncing places into Google Maps lists."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..domain import ScrapeRunMetrics
from ..scraping.models import ScrapeProgressReporter
from .sync_models import ScrapeSyncCommand
from .sync_ports import SyncOutputPort


@dataclass(frozen=True)
class SourceBucket:
    """One source-defined output bucket that maps to a Google Maps list."""

    slug: str
    label: str
    badge: str


@dataclass(frozen=True)
class SourcePlan:
    """Prepared source metadata needed by the generic sync core."""

    source_id: str
    scope_name: str
    checkpoint_scope: str
    buckets: tuple[SourceBucket, ...]
    start_url: str | None = None


@dataclass(frozen=True)
class SourcePage:
    """A source page already routed into generic buckets."""

    page_number: int
    rows_by_bucket: Mapping[str, list[dict[str, Any]]]
    next_cursor: str | None
    estimated_total_pages: int | None
    total_source_rows: int | None


@dataclass(frozen=True)
class SourceRunResult:
    """Run-level source metrics."""

    total_rows: int
    processed_pages: int
    fetch_failures: int = 0
    skipped_rows: int = 0
    unsupported_rows: int = 0

    @classmethod
    def from_scrape_metrics(cls, metrics: ScrapeRunMetrics) -> SourceRunResult:
        return cls(
            total_rows=metrics.total_restaurants,
            processed_pages=metrics.processed_pages,
            fetch_failures=metrics.fetch_failures,
        )

    def to_scrape_metrics(self) -> ScrapeRunMetrics:
        return ScrapeRunMetrics(
            total_restaurants=self.total_rows,
            processed_pages=self.processed_pages,
            fetch_failures=self.fetch_failures,
        )


SourceItemHandler = Callable[
    [int, int | None, int | None, str, dict[str, Any]],
    None,
]
SourcePageHandler = Callable[
    [int, str, list[dict[str, Any]], str | None, int, int | None, int],
    None,
]
SourceInterruptHandler = Callable[[str, int, int | None, int], None]


@dataclass(frozen=True)
class SourceRunHandlers:
    """Callbacks and progress surfaces used by source adapters."""

    on_item: SourceItemHandler
    on_page: SourcePageHandler
    on_interrupt: SourceInterruptHandler
    progress_reporter: ScrapeProgressReporter
    start_cursor: str
    start_page_number: int = 1
    start_estimated_total_pages: int | None = None
    initial_total_rows: int = 0


class PlaceSourceAdapter(Protocol):
    """Protocol implemented by place sources such as Michelin."""

    def prepare(self, command: ScrapeSyncCommand, output: SyncOutputPort) -> SourcePlan:
        """Resolve source-specific input into a generic source plan."""
        ...

    def run(
        self,
        *,
        command: ScrapeSyncCommand,
        plan: SourcePlan,
        handlers: SourceRunHandlers,
    ) -> SourceRunResult:
        """Stream source rows to generic sync callbacks."""
        ...

    def group_local_rows_by_bucket(
        self,
        *,
        rows: list[dict[str, Any]],
        bucket_slugs: tuple[str, ...],
    ) -> dict[str, list[dict[str, Any]]]:
        """Route local rows for direct Maps probe mode."""
        ...
