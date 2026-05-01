"""Use-case orchestration for syncing Michelin listings to Google Maps lists."""

import asyncio
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, cast

import urllib3

from ..adapters.checkpoint_store import JsonCheckpointStore
from ..adapters.google_maps_driver import GoogleMapsAuthRequiredError
from ..adapters.google_maps_sync_writer import (
    DryRunSyncWriter,
    GoogleMapsRowSyncFailFastError,
    GoogleMapsSyncWriter,
    RowFailureCallback,
)
from ..adapters.path_builder import (
    resolve_checkpoint_path,
    resolve_debug_html_path,
    resolve_error_report_path,
)
from ..catalog import LEVEL_LABELS, normalize_target, resolve_language, resolve_target
from ..catalog.levels import build_rating_to_output_level_slug_map, resolve_output_level_slug
from ..config import (
    CRAWL_DELAY_OPTION_FLAGS,
    HEADERS,
    MAPS_DELAY_OPTION_FLAGS,
    MAX_SAVE_RETRIES_OPTION_FLAGS,
    MISSING_LIST_POLICY_CHOICES,
    SANDBOX_DEFAULT_MAX_PAGES,
    SANDBOX_LIST_NAME_PREFIX,
)
from ..domain import ScrapeRunMetrics
from ..scraping import crawl, resolve_listing_scope_name
from .html_redaction import find_unredacted_sensitive_markers, redact_html_text
from .row_router import LevelRowRouter
from .sync_models import (
    MissingListReport,
    ScrapeSyncCommand,
    SyncAccumulation,
    SyncItemFailure,
    SyncSummary,
    create_empty_row_counts,
)
from .sync_page_handler import SyncPageHandler
from .sync_pipeline import SyncPipeline
from .sync_ports import LevelSyncWriterPort, SyncOutputPort
from .sync_progress import SyncProgressCoordinator
from .sync_resume_service import prepare_resume_plan

AUTH_REQUIRED_EXIT_CODE = 3
_SCOPE_NAME_FROM_LISTING_LANGUAGES = frozenset({"zh_tw"})
PLAYWRIGHT_BROWSER_SETUP_GUIDE = (
    "Playwright browser runtime is missing or not installed correctly.\n"
    "Run:\n"
    "  uv sync\n"
    "  uv run playwright install chromium\n"
    "If the error persists, clear browser cache and reinstall:\n"
    "  rm -rf ~/Library/Caches/ms-playwright\n"
    "  uv run playwright install chromium"
)
SyncDebugLogCallback = Callable[[str], None]
SyncDebugLogCallbackSetter = Callable[[SyncDebugLogCallback | None], None]
SyncRowProgressCallbackSetter = Callable[[Callable[[int, int, str, str], None] | None], None]
RowFailureCallbackSetter = Callable[[RowFailureCallback | None], None]
FastShutdownCallback = Callable[[], None]
PageFailureDebugSnapshotCallback = Callable[[int, Sequence[SyncItemFailure]], Any]


def _resolve_tls_verify(ca_bundle: str, insecure: bool) -> bool | str:
    return ca_bundle if ca_bundle else (not insecure)


def _resolve_scope_name_for_lists(
    *,
    fallback_scope_name: str,
    start_url: str,
    use_listing_scope_name: bool,
    tls_verify: bool | str,
    output: SyncOutputPort,
) -> str:
    if not use_listing_scope_name:
        return fallback_scope_name

    try:
        listing_scope_name = resolve_listing_scope_name(
            url=start_url,
            headers=HEADERS,
            tls_verify=tls_verify,
        )
    except Exception as exc:  # noqa: BLE001
        output.warn(
            
                "Failed to resolve language-specific list scope name from Michelin listing page. "
                f"Using fallback scope name '{fallback_scope_name}'. Error: {exc}"
            
        )
        return fallback_scope_name

    if listing_scope_name:
        if (_contains_ascii_letters(listing_scope_name) and not _is_ascii_only(listing_scope_name)
                and not _is_ascii_only(fallback_scope_name)):
            return fallback_scope_name
        return listing_scope_name

    output.warn(
        
            "Unable to extract language-specific list scope name from Michelin listing page. "
            f"Using fallback scope name '{fallback_scope_name}'."
        
    )
    return fallback_scope_name


def _normalize_language_key(language: str) -> str:
    resolved_language = resolve_language(language)
    return resolved_language.lower().replace("-", "_")


def _is_ascii_only(value: str) -> bool:
    return value.isascii()


def _contains_ascii_letters(value: str) -> bool:
    return any("a" <= character.lower() <= "z" for character in value)


def _build_row_router(level_slugs: Sequence[str]) -> LevelRowRouter:
    # Register ALL known Michelin ratings so the router can distinguish
    # "known but not selected" (skip) from "truly unknown" (error).
    return LevelRowRouter(
        level_slugs=level_slugs,
        rating_to_level_slug=build_rating_to_output_level_slug_map(tuple(level_slugs)),
    )


def _create_sync_writer(
    command: ScrapeSyncCommand,
    scope_name: str,
    *,
    language: str,
    checkpoint_store: JsonCheckpointStore | None = None,
) -> LevelSyncWriterPort:
    if command.dry_run:
        return DryRunSyncWriter(
            list_name_template=command.list_name_template,
            list_name_prefix=command.list_name_prefix,
            language=language,
        )

    def _on_row_synced(row_key: str, level_slug: str, list_name: str) -> None:
        del level_slug, list_name
        if checkpoint_store is not None:
            checkpoint_store.add_synced_row(row_key)

    return GoogleMapsSyncWriter(
        user_data_dir=Path(command.google_user_data_dir).expanduser(),
        headless=command.headless,
        sync_delay_seconds=command.sync_delay_seconds,
        max_save_retries=command.max_save_retries,
        list_name_template=command.list_name_template,
        list_name_prefix=command.list_name_prefix,
        on_missing_list=command.on_missing_list,
        ignore_existing_lists_check=command.ignore_existing_lists_check,
        on_row_synced=_on_row_synced,
        probe_only=(command.maps_probe_only or bool(command.maps_probe_rows_file)),
        language=language,
        state_dir=command.state_dir,
    )


def _build_missing_list_reports(
    *,
    level_slugs: Sequence[str],
    list_names_by_level: dict[str, str],
    missing_row_counts_by_level: dict[str, int],
) -> tuple[MissingListReport, ...]:
    reports: list[MissingListReport] = []
    for level_slug in level_slugs:
        skipped_rows = missing_row_counts_by_level.get(level_slug, 0)
        if skipped_rows <= 0:
            continue
        reports.append(
            MissingListReport(
                level_slug=level_slug,
                list_name=list_names_by_level[level_slug],
                skipped_rows=skipped_rows,
            )
        )
    return tuple(reports)


def _write_error_report(path: Path, failures: Sequence[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_handle:
        for failure in failures:
            file_handle.write(json.dumps(failure, ensure_ascii=True) + "\n")


def _serialize_failed_items(
    failed_items: Sequence[SyncItemFailure],
) -> list[dict[str, str]]:
    return [
        {
            "level_slug": failure.level_slug,
            "row_key": failure.row_key,
            "restaurant_name": failure.restaurant_name,
            "reason": failure.reason,
            "attempted_queries": ", ".join(failure.attempted_queries),
        }
        for failure in failed_items
    ]


def _summarize_failure_reasons(failed_items: Sequence[SyncItemFailure]) -> str:
    reason_counts: dict[str, int] = {}
    for failure in failed_items:
        reason_counts[failure.reason] = reason_counts.get(failure.reason, 0) + 1
    ordered_counts = sorted(reason_counts.items(), key=lambda item: item[1], reverse=True)
    if not ordered_counts:
        return "<none>"
    return ", ".join(f"{reason}={count}" for reason, count in ordered_counts[:8])


async def _capture_sync_failure_debug_html(
    *,
    sync_writer: LevelSyncWriterPort,
    state_dir: str,
    scope_name: str,
    context: str,
    language: str,
    record_fixtures_dir: str,
    output: SyncOutputPort,
) -> Path | None:
    maybe_dump_method: Any = getattr(sync_writer, "dump_debug_html", None)
    debug_html_path = resolve_debug_html_path(
        state_dir=state_dir,
        scope_name=scope_name,
        context=context,
    )
    output.warn(f"Attempting debug HTML snapshot: {debug_html_path}")

    if not callable(maybe_dump_method):
        output.warn(
            "Debug HTML snapshot capture is unavailable because sync writer does not "
            "implement dump_debug_html()."
        )
        return None

    try:
        saved = bool(await cast(Any, maybe_dump_method(debug_html_path)))
    except BaseException as exc:  # noqa: BLE001
        output.warn(f"Failed to write debug HTML snapshot to {debug_html_path}: {exc}")
        return None

    if not saved:
        output.warn(
            "Debug HTML snapshot was not written because no active browser page was available. "
            f"Target path: {debug_html_path}"
        )
        return None

    if _sanitize_debug_html_snapshot(debug_html_path, output):
        output.warn(f"Debug HTML snapshot written to: {debug_html_path} (de-identified).")
    else:
        output.warn(f"Debug HTML snapshot written to: {debug_html_path}")
    _record_debug_snapshot_fixture(
        debug_html_path=debug_html_path,
        context=context,
        language=language,
        record_fixtures_dir=record_fixtures_dir,
        output=output,
    )
    return debug_html_path


def _sanitize_debug_html_snapshot(path: Path, output: SyncOutputPort) -> bool:
    try:
        raw_html = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        output.warn(f"Failed to read debug HTML snapshot for de-identification: {exc}")
        return False

    redacted_html = redact_html_text(raw_html)
    if redacted_html == raw_html:
        return True

    try:
        path.write_text(redacted_html, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        output.warn(f"Failed to write de-identified debug HTML snapshot: {exc}")
        return False
    return True


def _record_debug_snapshot_fixture(
    *,
    debug_html_path: Path,
    context: str,
    language: str,
    record_fixtures_dir: str,
    output: SyncOutputPort,
) -> None:
    destination_root_arg = record_fixtures_dir.strip()
    if not destination_root_arg:
        return

    try:
        source_html = debug_html_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        output.warn(f"Failed to read debug snapshot for fixture recording: {exc}")
        return

    redacted_html = redact_html_text(source_html)
    markers = find_unredacted_sensitive_markers(redacted_html)
    if markers:
        marker_text = ", ".join(markers)
        output.warn(
            "Skipped fixture recording because sensitive markers remained after redaction: "
            f"{marker_text}"
        )
        return

    destination_root = Path(destination_root_arg).expanduser()
    destination_root.mkdir(parents=True, exist_ok=True)
    fixture_name = debug_html_path.stem
    fixture_html_path = destination_root / f"{fixture_name}.html"
    fixture_metadata_path = destination_root / f"{fixture_name}.metadata.json"
    metadata = {
        "scenario": context,
        "captured_at": date.today().isoformat(),
        "language": language,
        "source": "debug-sync-snapshot",
        "sanitized": True,
    }

    try:
        fixture_html_path.write_text(redacted_html, encoding="utf-8")
        fixture_metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        output.warn(f"Failed to record debug snapshot fixture: {exc}")
        return

    output.warn(f"Recorded debug fixture HTML: {fixture_html_path}")
    output.warn(f"Recorded debug fixture metadata: {fixture_metadata_path}")


def _slugify_debug_context(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned or "unknown"


def _register_debug_log_callback(
    *,
    sync_writer: LevelSyncWriterPort,
    output: SyncOutputPort,
    enabled: bool,
) -> None:
    if not enabled:
        return
    maybe_setter = getattr(sync_writer, "set_debug_log_callback", None)
    if not callable(maybe_setter):
        return
    callback_setter = cast(SyncDebugLogCallbackSetter, maybe_setter)
    callback_setter(lambda message: output.warn(f"[debug] {message}"))


def _register_row_progress_callback(
    *,
    sync_writer: LevelSyncWriterPort,
    on_sync_row_progress: Callable[[int, int, str, str], None],
) -> None:
    maybe_setter = getattr(sync_writer, "set_row_progress_callback", None)
    if not callable(maybe_setter):
        return
    callback_setter = cast(SyncRowProgressCallbackSetter, maybe_setter)
    callback_setter(on_sync_row_progress)


def _register_row_failure_callback(
    *,
    sync_writer: LevelSyncWriterPort,
    state_dir: str,
    scope_name: str,
    language: str,
    record_fixtures_dir: str,
    output: SyncOutputPort,
) -> None:
    maybe_setter = getattr(sync_writer, "set_row_failure_callback", None)
    if not callable(maybe_setter):
        return

    async def on_row_failure(failure: SyncItemFailure) -> None:
        """Capture HTML snapshot immediately when a row fails."""
        await _capture_sync_failure_debug_html(
            sync_writer=sync_writer,
            state_dir=state_dir,
            scope_name=scope_name,
            context=f"row-failure-{_slugify_debug_context(failure.reason.split(':', 1)[0])}",
            language=language,
            record_fixtures_dir=record_fixtures_dir,
            output=output,
        )

    callback_setter = cast(RowFailureCallbackSetter, maybe_setter)
    callback_setter(on_row_failure)


def _request_fast_shutdown_if_supported(sync_writer: LevelSyncWriterPort) -> None:
    maybe_callback = getattr(sync_writer, "request_fast_shutdown", None)
    if not callable(maybe_callback):
        return
    shutdown_callback = cast(FastShutdownCallback, maybe_callback)
    shutdown_callback()


def _is_playwright_target_closed_error(exc: BaseException) -> bool:
    return "target page, context or browser has been closed" in str(exc).lower()


def _is_missing_playwright_browser_error(message: str) -> bool:
    normalized_message = message.lower()
    has_missing_executable_tokens = (
        "browsertype.launch_persistent_context" in normalized_message
        and "executable doesn't exist" in normalized_message
    )
    has_playwright_banner = (
        "please run the following command to download new browsers" in normalized_message
        and "playwright install" in normalized_message
    )
    return has_missing_executable_tokens or has_playwright_banner


def _format_runtime_failure_message(message: str) -> str:
    if _is_missing_playwright_browser_error(message):
        return f"{message}\n\n{PLAYWRIGHT_BROWSER_SETUP_GUIDE}"
    return message


def _load_maps_probe_rows(rows_file: str) -> list[dict[str, Any]]:
    probe_path = Path(rows_file).expanduser()
    if not probe_path.exists():
        raise ValueError(f"maps-probe-rows-file does not exist: {probe_path}")
    if not probe_path.is_file():
        raise ValueError(f"maps-probe-rows-file is not a file: {probe_path}")

    parsed_rows: list[dict[str, Any]] = []
    with probe_path.open("r", encoding="utf-8") as file_handle:
        for line_number, raw_line in enumerate(file_handle, start=1):
            stripped_line = raw_line.strip()
            if not stripped_line:
                continue
            try:
                payload = json.loads(stripped_line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON in maps-probe-rows-file at line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(payload, dict):
                raise ValueError(
                    f"maps-probe-rows-file line {line_number} must be a JSON object."
                )
            if not str(payload.get("Name", "")).strip():
                raise ValueError(
                    f"maps-probe-rows-file line {line_number} is missing required field 'Name'."
                )
            parsed_rows.append(payload)

    if not parsed_rows:
        raise ValueError(
            f"maps-probe-rows-file has no usable rows: {probe_path}"
        )
    return parsed_rows


def _group_probe_rows_by_level(
    *,
    rows: Sequence[dict[str, Any]],
    level_slugs: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    grouped_rows: dict[str, list[dict[str, Any]]] = {level_slug: [] for level_slug in level_slugs}
    rows_for_rating_router: list[dict[str, Any]] = []

    for row in rows:
        explicit_level_slug = str(row.get("LevelSlug", "")).strip().lower()
        if not explicit_level_slug:
            rows_for_rating_router.append(row)
            continue
        resolved_output_level_slug = resolve_output_level_slug(
            explicit_level_slug,
            tuple(level_slugs),
        )
        if resolved_output_level_slug is None:
            allowed_values = ", ".join(level_slugs)
            raise ValueError(
                f"Unsupported LevelSlug value in maps-probe-rows-file: '{explicit_level_slug}'. "
                f"Allowed values: {allowed_values}."
            )
        grouped_rows[resolved_output_level_slug].append(row)

    if rows_for_rating_router:
        routed_rows = _build_row_router(level_slugs).group_rows_by_level(rows_for_rating_router)
        for level_slug in level_slugs:
            grouped_rows[level_slug].extend(routed_rows.get(level_slug, []))

    return grouped_rows


def _apply_sandbox_overrides(
    *,
    command: ScrapeSyncCommand,
    output: SyncOutputPort,
) -> ScrapeSyncCommand:
    if not command.sandbox:
        return command

    output.warn(
        "sandbox mode enabled: forcing test-list prefix and existing-list reuse checks "
        "for safer live validation."
    )
    effective_max_pages = command.max_pages
    if not command.max_pages_specified:
        effective_max_pages = SANDBOX_DEFAULT_MAX_PAGES
        output.warn(
            f"sandbox mode default applied: max-pages={SANDBOX_DEFAULT_MAX_PAGES}. "
            "Pass --max-pages explicitly to override."
        )

    return replace(
        command,
        list_name_prefix=SANDBOX_LIST_NAME_PREFIX,
        ignore_existing_lists_check=True,
        max_pages=effective_max_pages,
    )


def _warn_sandbox_created_lists(
    *,
    command: ScrapeSyncCommand,
    sync_writer: LevelSyncWriterPort,
    output: SyncOutputPort,
) -> None:
    if not command.sandbox:
        return

    list_names = dict(sync_writer.list_names_by_level)
    maybe_created_mapping = getattr(sync_writer, "list_created_by_level", None)
    if isinstance(maybe_created_mapping, Mapping):
        created_lists_set: set[str] = set()
        for level_slug, created in maybe_created_mapping.items():
            if not bool(created):
                continue
            list_name = list_names.get(str(level_slug), "")
            if list_name.startswith(SANDBOX_LIST_NAME_PREFIX):
                created_lists_set.add(list_name)
        created_lists = sorted(created_lists_set)
        created_lists = [list_name for list_name in created_lists if list_name]
        if created_lists:
            output.warn(
                "sandbox mode created test lists. Delete these lists manually: "
                + ", ".join(created_lists)
            )
            return

    output.warn(
        f"sandbox mode finished. Delete any '{SANDBOX_LIST_NAME_PREFIX}' "
        "Google Maps lists created for testing."
    )


async def _run_scrape_sync_async(command: ScrapeSyncCommand, output: SyncOutputPort) -> int:
    """Run Michelin scrape + Google Maps sync workflow for one command input."""

    if command.on_missing_list not in MISSING_LIST_POLICY_CHOICES:
        valid_values = ", ".join(MISSING_LIST_POLICY_CHOICES)
        output.show_failure(f"Invalid on-missing-list value: {command.on_missing_list}. Use: {valid_values}.")
        return 2
    if command.maps_probe_rows_file and command.dry_run:
        output.show_failure("maps-probe-rows-file cannot be combined with dry-run.")
        return 2

    command = _apply_sandbox_overrides(command=command, output=output)

    resolved_language = resolve_language(command.language)
    language_key = _normalize_language_key(resolved_language)

    target = resolve_target(
        normalize_target(command.target),
        language=resolved_language,
    )
    if command.insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    tls_verify = _resolve_tls_verify(ca_bundle=command.ca_bundle, insecure=command.insecure)
    use_listing_scope_name = language_key in _SCOPE_NAME_FROM_LISTING_LANGUAGES
    effective_scope_name = _resolve_scope_name_for_lists(
        fallback_scope_name=target.scope_name,
        start_url=target.start_url,
        use_listing_scope_name=use_listing_scope_name,
        tls_verify=tls_verify,
        output=output,
    )

    start_time = time.perf_counter()
    output_targets = tuple((level_slug, LEVEL_LABELS[level_slug]) for level_slug in command.levels)
    checkpoint_store = JsonCheckpointStore(
        path=resolve_checkpoint_path(output_dir=command.state_dir, scope_name=target.scope_name),
        level_slugs=command.levels,
    )
    sync_writer = _create_sync_writer(
        command,
        effective_scope_name,
        language=resolved_language,
        checkpoint_store=checkpoint_store,
    )
    if command.maps_probe_only and command.dry_run:
        output.warn("maps-probe-only is ignored because dry-run mode is active.")
    elif command.maps_probe_only:
        output.warn(
            "maps-probe-only mode enabled: running Maps search/match checks without list writes."
        )
    if command.maps_probe_rows_file:
        output.warn(
            
                "maps-probe-rows-file mode enabled: skipping Michelin crawl and probing Google Maps "
                "with local rows only."
            
        )
        if not command.maps_probe_only:
            output.warn("maps-probe-rows-file implies maps-probe-only (no list writes).")
        if command.ignore_checkpoint:
            output.warn("ignore-checkpoint is ignored in maps-probe-rows-file mode.")
        if command.max_pages_specified and command.max_pages > 0:
            output.warn("max-pages is ignored in maps-probe-rows-file mode.")
        if command.max_rows_per_page > 0:
            output.warn("max-rows-per-page is ignored in maps-probe-rows-file mode.")

    error_report_path = resolve_error_report_path(command.state_dir, target.scope_name)

    progress_coordinator = SyncProgressCoordinator(output.create_progress_reporter())
    progress_reporter = progress_coordinator.create_crawl_reporter()
    accumulation = SyncAccumulation(
        scraped_count_by_level=create_empty_row_counts(command.levels),
        added_count_by_level=create_empty_row_counts(command.levels),
        skipped_count_by_level=create_empty_row_counts(command.levels),
        sample_rows=[],
        failed_items=[],
    )
    resumed_synced_count: int = 0
    resumed_scraped_count_by_level: dict[str, int] = {}
    metrics: ScrapeRunMetrics
    interrupt_checkpoint: dict[str, object] | None = None
    try:
        page_failure_debug_snapshot: PageFailureDebugSnapshotCallback | None = None
        if command.debug_sync_failures:
            async def _capture_page_failure_debug_snapshot(
                page_number: int,
                failed_items: Sequence[SyncItemFailure],
            ) -> None:
                primary_reason = failed_items[0].reason if failed_items else "sync-failure"
                reason_prefix = str(primary_reason).split(":", 1)[0]
                await _capture_sync_failure_debug_html(
                    sync_writer=sync_writer,
                    state_dir=command.state_dir,
                    scope_name=target.scope_name,
                    context=f"page-{page_number}-{_slugify_debug_context(reason_prefix)}",
                    language=resolved_language,
                    record_fixtures_dir=command.record_fixtures_dir,
                    output=output,
                )

            page_failure_debug_snapshot = _capture_page_failure_debug_snapshot
        if command.debug_sync_failures:
            output.warn(
                
                    "[debug] Effective timing: "
                    f"{CRAWL_DELAY_OPTION_FLAGS[0]}={command.sleep_seconds}, "
                    f"{MAPS_DELAY_OPTION_FLAGS[0]}={command.sync_delay_seconds}, "
                    f"{MAX_SAVE_RETRIES_OPTION_FLAGS[0]}={command.max_save_retries}"
                
            )
        progress_coordinator.update_setup_progress(
            "Setting up Google Maps session...",
            completion=0.35,
        )
        _register_row_progress_callback(
            sync_writer=sync_writer,
            on_sync_row_progress=progress_coordinator.on_sync_row_progress,
        )
        _register_debug_log_callback(
            sync_writer=sync_writer,
            output=output,
            enabled=command.debug_sync_failures,
        )
        if command.debug_sync_failures:
            _register_row_failure_callback(
                sync_writer=sync_writer,
                state_dir=command.state_dir,
                scope_name=effective_scope_name,
                language=resolved_language,
                record_fixtures_dir=command.record_fixtures_dir,
                output=output,
            )

        await sync_writer.initialize_run(scope_name=effective_scope_name, level_slugs=command.levels)
        if command.maps_probe_rows_file:
            progress_coordinator.update_setup_progress(
                "Loading probe rows...",
                completion=0.75,
            )
            probe_rows = _load_maps_probe_rows(command.maps_probe_rows_file)
            rows_by_level = _group_probe_rows_by_level(
                rows=probe_rows,
                level_slugs=command.levels,
            )
            progress_coordinator.update_setup_progress(
                "Setup complete. Starting direct Maps probe...",
                completion=1.0,
            )
            batch_result = await sync_writer.sync_rows_by_level(rows_by_level)
            progress_reporter.finish()

            for level_slug in command.levels:
                accumulation.scraped_count_by_level[level_slug] = len(rows_by_level.get(level_slug, []))
                accumulation.added_count_by_level[level_slug] = int(
                    batch_result.added_count_by_level.get(level_slug, 0)
                )
                accumulation.skipped_count_by_level[level_slug] = int(
                    batch_result.skipped_count_by_level.get(level_slug, 0)
                )
            accumulation.sample_rows.extend(probe_rows[:5])
            accumulation.failed_items.extend(batch_result.failed_items)
            metrics = ScrapeRunMetrics(
                total_restaurants=len(probe_rows),
                processed_pages=0,
            )
        else:
            progress_coordinator.update_setup_progress(
                "Preparing checkpoint / resume plan...",
                completion=0.75,
            )
            resume_plan = prepare_resume_plan(
                start_url=target.start_url,
                level_slugs=command.levels,
                checkpoint_store=checkpoint_store,
                output=output,
                ignore_checkpoint=command.ignore_checkpoint,
            )
            progress_coordinator.update_setup_progress(
                "Setup complete. Starting scrape + sync...",
                completion=1.0,
            )
            accumulation.scraped_count_by_level = dict(resume_plan.row_counts)
            resumed_scraped_count_by_level = dict(resume_plan.row_counts)
            resumed_synced_count = len(resume_plan.synced_row_keys)
            checkpoint_store.initialize_synced_row_keys(resume_plan.synced_row_keys)
            if isinstance(sync_writer, GoogleMapsSyncWriter):
                sync_writer.set_initial_synced_row_keys(resume_plan.synced_row_keys)
            if command.debug_sync_failures and resume_plan.start_page_number > 1:
                scraped_total_at_resume = sum(resume_plan.row_counts.values())
                level_breakdown = ", ".join(
                    f"{slug}={count}"
                    for slug, count in resume_plan.row_counts.items()
                    if count > 0
                )
                output.warn(
                    f"[debug] Resuming from checkpoint: "
                    f"page={resume_plan.start_page_number}, "
                    f"scraped_before_resume={scraped_total_at_resume}, "
                    f"synced_before_resume={resumed_synced_count}, "
                    f"levels=({level_breakdown})"
                )
            page_handler = SyncPageHandler(
                start_url=target.start_url,
                checkpoint_store=checkpoint_store,
                sync_writer=sync_writer,
                row_router=_build_row_router(command.levels),
                accumulation=accumulation,
                output=output,
                debug_sync_failures=command.debug_sync_failures,
                max_rows_per_page=command.max_rows_per_page,
                on_page_sync_start=progress_coordinator.on_page_sync_start,
                on_page_sync_failures=page_failure_debug_snapshot,
            )

            def _handle_interrupt(
                current_url: str,
                page_number: int,
                estimated_total_pages: int | None,
                total_restaurants: int,
            ) -> None:
                nonlocal interrupt_checkpoint
                interrupt_checkpoint = {
                    "page_url": current_url,
                    "page_number": page_number,
                    "estimated_total_pages": estimated_total_pages,
                    "total_restaurants": total_restaurants,
                }

            pipeline = SyncPipeline(
                _page_handler=page_handler,
                _max_queue_size=10,
            )

            def _scrape_fn(on_item, on_page):  # type: ignore[no-untyped-def]
                return crawl(
                    start_url=resume_plan.start_scrape_url,
                    on_page=on_page,
                    on_item=on_item,
                    sleep_seconds=command.sleep_seconds,
                    tls_verify=tls_verify,
                    start_page_number=resume_plan.start_page_number,
                    start_estimated_total_pages=resume_plan.start_estimated_total_pages,
                    initial_total_restaurants=resume_plan.initial_total_restaurants,
                    progress_reporter=progress_reporter,
                    max_pages=command.max_pages,
                    on_interrupt=_handle_interrupt,
                    local_language=target.local_language,
                    local_country_code=target.local_country_code,
                    requested_language=resolved_language,
                )

            metrics = await pipeline.run_async(_scrape_fn)
    except KeyboardInterrupt:
        if interrupt_checkpoint is not None:
            try:
                checkpoint_store.save(
                    start_url=target.start_url,
                    page_number=int(interrupt_checkpoint["page_number"]),
                    page_url=str(interrupt_checkpoint["page_url"]),
                    next_url=str(interrupt_checkpoint["page_url"]),
                    next_page_number=int(interrupt_checkpoint["page_number"]),
                    estimated_total_pages=cast(int | None, interrupt_checkpoint["estimated_total_pages"]),
                    total_restaurants=int(interrupt_checkpoint["total_restaurants"]),
                    rows_per_level=dict(accumulation.scraped_count_by_level),
                )
            except Exception as exc:  # noqa: BLE001
                output.warn(f"Failed to save checkpoint on interrupt: {exc}")
        _request_fast_shutdown_if_supported(sync_writer)
        progress_reporter.finish()
        await _capture_sync_failure_debug_html(
            sync_writer=sync_writer,
            state_dir=command.state_dir,
            scope_name=effective_scope_name,
            context="keyboard-interrupt",
            language=resolved_language,
            record_fixtures_dir=command.record_fixtures_dir,
            output=output,
        )
        if accumulation.failed_items:
            _write_error_report(
                error_report_path,
                _serialize_failed_items(accumulation.failed_items),
            )
            output.warn(f"Partial failure report written to: {error_report_path}")
            output.warn(
                f"Partial failure reasons: {_summarize_failure_reasons(accumulation.failed_items)}"
            )
        output.show_interrupted(
            scraped_total=sum(accumulation.scraped_count_by_level.values()),
            added_total=sum(accumulation.added_count_by_level.values()),
            failed_total=len(accumulation.failed_items),
            skipped_total=sum(accumulation.skipped_count_by_level.values()),
        )
        _warn_sandbox_created_lists(
            command=command,
            sync_writer=sync_writer,
            output=output,
        )
        return 130
    except GoogleMapsAuthRequiredError as exc:
        progress_reporter.finish()
        await _capture_sync_failure_debug_html(
            sync_writer=sync_writer,
            state_dir=command.state_dir,
            scope_name=target.scope_name,
            context="auth-required",
            language=resolved_language,
            record_fixtures_dir=command.record_fixtures_dir,
            output=output,
        )
        output.warn(str(exc))
        _warn_sandbox_created_lists(
            command=command,
            sync_writer=sync_writer,
            output=output,
        )
        return AUTH_REQUIRED_EXIT_CODE
    except GoogleMapsRowSyncFailFastError as exc:
        progress_reporter.finish()
        if exc.failure not in accumulation.failed_items:
            accumulation.failed_items.append(exc.failure)
        reason_prefix = str(exc.failure.reason).split(":", 1)[0]
        await _capture_sync_failure_debug_html(
            sync_writer=sync_writer,
            state_dir=command.state_dir,
            scope_name=target.scope_name,
            context=f"fail-fast-{_slugify_debug_context(reason_prefix)}",
            language=resolved_language,
            record_fixtures_dir=command.record_fixtures_dir,
            output=output,
        )
        _write_error_report(
            error_report_path,
            _serialize_failed_items(accumulation.failed_items),
        )
        output.warn(f"Failure report written to: {error_report_path}")
        attempted_queries = ", ".join(exc.failure.attempted_queries[:3]) or "<none>"
        output.show_failure(
            "Fail-fast policy stopped sync immediately. "
            f"level={exc.failure.level_slug} "
            f"name='{exc.failure.restaurant_name}' "
            f"reason='{exc.failure.reason}' "
            f"queries='{attempted_queries}'"
        )
        _warn_sandbox_created_lists(
            command=command,
            sync_writer=sync_writer,
            output=output,
        )
        return 1
    except Exception as exc:
        if _is_playwright_target_closed_error(exc):
            _request_fast_shutdown_if_supported(sync_writer)
            progress_reporter.finish()
            if accumulation.failed_items:
                _write_error_report(
                    error_report_path,
                    _serialize_failed_items(accumulation.failed_items),
                )
                output.warn(f"Partial failure report written to: {error_report_path}")
                output.warn(
                    f"Partial failure reasons: {_summarize_failure_reasons(accumulation.failed_items)}"
                )
            output.show_interrupted(
                scraped_total=sum(accumulation.scraped_count_by_level.values()),
                added_total=sum(accumulation.added_count_by_level.values()),
                failed_total=len(accumulation.failed_items),
                skipped_total=sum(accumulation.skipped_count_by_level.values()),
            )
            _warn_sandbox_created_lists(
                command=command,
                sync_writer=sync_writer,
                output=output,
            )
            return 130
        progress_reporter.finish()
        await _capture_sync_failure_debug_html(
            sync_writer=sync_writer,
            state_dir=command.state_dir,
            scope_name=target.scope_name,
            context=exc.__class__.__name__,
            language=resolved_language,
            record_fixtures_dir=command.record_fixtures_dir,
            output=output,
        )
        output.show_failure(_format_runtime_failure_message(str(exc)))
        _warn_sandbox_created_lists(
            command=command,
            sync_writer=sync_writer,
            output=output,
        )
        return 1
    finally:
        try:
            await sync_writer.finalize_run()
        except BaseException as finalize_exc:  # noqa: BLE001
            output.warn(f"Failed to finalize Maps writer cleanly: {finalize_exc}")

    missing_reports = _build_missing_list_reports(
        level_slugs=command.levels,
        list_names_by_level=dict(sync_writer.list_names_by_level),
        missing_row_counts_by_level=dict(sync_writer.missing_row_counts_by_level),
    )
    if metrics.fetch_failures > 0:
        output.show_failure(
            "Scrape run had listing-page fetch failures. "
            f"Failed pages: {metrics.fetch_failures}."
        )
    summary = SyncSummary(
        metrics=metrics,
        sample_rows=tuple(accumulation.sample_rows),
        scraped_count_by_level=dict(accumulation.scraped_count_by_level),
        added_count_by_level=dict(accumulation.added_count_by_level),
        skipped_count_by_level=dict(accumulation.skipped_count_by_level),
        failed_items=tuple(accumulation.failed_items),
        missing_lists=missing_reports,
        list_names_by_level=dict(sync_writer.list_names_by_level),
        output_targets=output_targets,
        elapsed_seconds=time.perf_counter() - start_time,
        resumed_synced_count=resumed_synced_count,
        resumed_scraped_count_by_level=resumed_scraped_count_by_level,
    )
    output.show_final_results(summary)

    if accumulation.failed_items:
        _write_error_report(
            error_report_path,
            _serialize_failed_items(accumulation.failed_items),
        )
        output.warn(f"Failure report written to: {error_report_path}")

    _warn_sandbox_created_lists(
        command=command,
        sync_writer=sync_writer,
        output=output,
    )
    has_hard_failures = bool(accumulation.failed_items or missing_reports or metrics.fetch_failures > 0)
    return 1 if has_hard_failures else 0


def run_scrape_sync(command: ScrapeSyncCommand, output: SyncOutputPort) -> int:
    """Sync wrapper around the async implementation."""
    return asyncio.run(_run_scrape_sync_async(command=command, output=output))
