"""Resume policy for checkpoint-aware maps sync runs."""

from collections.abc import Sequence

from .sync_models import ResumePlan, create_empty_row_counts
from .sync_ports import CheckpointStorePort, SyncOutputPort


def prepare_resume_plan(
    *,
    start_url: str,
    level_slugs: Sequence[str],
    checkpoint_store: CheckpointStorePort,
    output: SyncOutputPort,
    ignore_checkpoint: bool,
) -> ResumePlan:
    """Resolve whether this run should start fresh or resume from checkpoint."""

    row_counts = create_empty_row_counts(level_slugs)
    default_plan = ResumePlan(
        start_scrape_url=start_url,
        start_page_number=1,
        start_estimated_total_pages=None,
        initial_total_restaurants=0,
        row_counts=row_counts,
    )

    if ignore_checkpoint:
        checkpoint_store.clear()
        output.warn("Ignoring checkpoint because --ignore-checkpoint is set. Starting a new run.")
        return default_plan

    resume_state, warning_message = checkpoint_store.load(expected_start_url=start_url)
    if warning_message:
        output.warn(f"Ignoring checkpoint: {warning_message} Starting a new run.")

    if resume_state is None:
        checkpoint_store.clear()
        return default_plan

    output.show_resume(
        next_page_number=resume_state.next_page_number,
        next_url=resume_state.next_url,
        scraped_before_resume=sum(resume_state.rows_per_level.values()),
        synced_before_resume=len(resume_state.synced_row_keys),
        rows_per_level=dict(resume_state.rows_per_level),
    )
    return ResumePlan(
        start_scrape_url=resume_state.next_url,
        start_page_number=resume_state.next_page_number,
        start_estimated_total_pages=resume_state.estimated_total_pages,
        initial_total_restaurants=resume_state.total_restaurants,
        row_counts=dict(resume_state.rows_per_level),
        synced_row_keys=resume_state.synced_row_keys,
    )
