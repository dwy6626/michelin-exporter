"""Core scraping interfaces and data structures."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

PageHandler = Callable[
    [int, str, list[dict[str, Any]], str | None, int, int | None, int],
    None,
]

ItemHandler = Callable[[int, int | None, int | None, dict[str, Any]], None]

InterruptHandler = Callable[[str, int, int | None, int], None]


class ScrapeProgressReporter(Protocol):
    """Progress reporting interface injected by outer layers."""

    def update(self, message: str, progress: float | None = None) -> None:
        """Update progress status."""

    def log(self, message: str) -> None:
        """Write one status message."""

    def finish(self, message: str | None = None) -> None:
        """Finalize progress reporting."""


class NullProgressReporter:
    """No-op progress reporter used when no output adapter is provided."""

    def update(self, message: str, progress: float | None = None) -> None:
        del message, progress

    def log(self, message: str) -> None:
        del message

    def finish(self, message: str | None = None) -> None:
        del message


@dataclass(frozen=True)
class ScrapeResultsPage:
    """One listing-page scrape outcome."""

    restaurant_rows: list[dict[str, Any]]
    next_url: str | None
    estimated_total_pages: int | None
    total_restaurants: int | None
    fetch_failed: bool
