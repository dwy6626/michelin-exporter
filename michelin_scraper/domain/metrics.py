"""Domain metrics for scrape runs."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScrapeRunMetrics:
    """Run-level scrape metrics."""

    total_restaurants: int
    processed_pages: int
    fetch_failures: int = 0
