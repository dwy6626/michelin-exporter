"""Maps sync writer implementations."""

import asyncio
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from ..application.html_redaction import find_unredacted_sensitive_markers, redact_html_text
from ..application.place_matcher import PlaceCandidate, assess_place_match
from ..application.place_query_builder import build_place_query_attempts
from ..application.row_identity import build_row_identity_key
from ..application.sync_enums import SyncRowStatus
from ..application.sync_models import SyncBatchResult, SyncItemFailure, SyncRowResult
from ..catalog import LEVEL_BADGES, LEVEL_LABELS
from ..config import (
    MISSING_LIST_POLICY_STOP,
)
from .google_maps_driver import (
    GoogleMapsAuthRequiredError,
    GoogleMapsDriver,
    GoogleMapsDriverConfig,
    GoogleMapsDriverPort,
    GoogleMapsListAlreadyExistsError,
    GoogleMapsListMissingDuringRunError,
    GoogleMapsNoteWriteError,
    GoogleMapsPlaceAlreadySavedError,
    GoogleMapsSelectorError,
    GoogleMapsTransientError,
)
from .path_builder import resolve_debug_html_path, safe_filename

_ROW_STATUS_ADDED = "added"
_ROW_STATUS_SKIPPED = "skipped"
_ROW_STATUS_PROBED = "probed"
_ROW_STATUS_FAILED = "failed"
_ROW_PROGRESS_STATUS_ADDED = "added"
_ROW_PROGRESS_STATUS_SKIPPED = "skipped"
_ROW_PROGRESS_STATUS_PROBED = "probed"
_ROW_PROGRESS_STATUS_SKIPPED_MISSING_LIST = "skipped-missing-list"
_ROW_PROGRESS_STATUS_FAILED = "failed"
_ROW_PROGRESS_STATUS_DRY_RUN = "skipped-dry-run"
_ROW_PROGRESS_STATUS_PROCESSING = "processing"


@dataclass(frozen=True)
class _RowSyncOutcome:
    status: str
    failure: SyncItemFailure | None


class GoogleMapsRowSyncFailFastError(RuntimeError):
    """Raised when fail-fast policy stops sync on the first row failure."""

    def __init__(
        self,
        *,
        failure: SyncItemFailure,
        added_count_by_level: Mapping[str, int],
        skipped_count_by_level: Mapping[str, int],
    ) -> None:
        self.failure = failure
        self.added_count_by_level = dict(added_count_by_level)
        self.skipped_count_by_level = dict(skipped_count_by_level)
        super().__init__(
            f"Fail-fast policy stopped sync at row '{failure.restaurant_name}': {failure.reason}"
        )


RowProgressCallback = Callable[[int, int, str, str], None]
SyncDebugLogCallback = Callable[[str], None]
RowFailureCallback = Callable[[SyncItemFailure], Any]
RowSyncedCallback = Callable[[str, str, str], None]
_LEVEL_LABELS_BY_LANGUAGE = {
    "zh_tw": {
        "stars": "星級",
        "one-star": "1 \u661f",
        "two-star": "2 \u661f",
        "three-star": "3 \u661f",
        "bib-gourmand": "\u5fc5\u6bd4\u767b",
        "selected": "\u5165\u9078",
    }
}
_LEVEL_BADGES_BY_LANGUAGE = {
    "zh_tw": {
        "stars": "星級",
        "one-star": "\u2b50",
        "two-star": "\u2b50\u2b50",
        "three-star": "\u2b50\u2b50\u2b50",
        "bib-gourmand": "\u5fc5\u6bd4\u767b",
        "selected": "\u5165\u9078",
    }
}


def _normalize_language_key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _resolve_level_label_map(language: str) -> Mapping[str, str]:
    return _LEVEL_LABELS_BY_LANGUAGE.get(_normalize_language_key(language), LEVEL_LABELS)


def _resolve_level_badge_map(language: str) -> Mapping[str, str]:
    return _LEVEL_BADGES_BY_LANGUAGE.get(_normalize_language_key(language), LEVEL_BADGES)


class DryRunSyncWriter:
    """No-op destination writer used by --dry-run mode."""

    def __init__(
        self,
        list_name_template: str,
        list_name_prefix: str,
        *,
        language: str = "en",
    ) -> None:
        self._list_name_template = list_name_template
        self._list_name_prefix = list_name_prefix
        self._language = language
        self._list_names_by_level: dict[str, str] = {}
        self._missing_row_counts_by_level: dict[str, int] = {}
        self._row_progress_callback: RowProgressCallback | None = None

    @property
    def list_names_by_level(self) -> Mapping[str, str]:
        return dict(self._list_names_by_level)

    @property
    def missing_row_counts_by_level(self) -> Mapping[str, int]:
        return dict(self._missing_row_counts_by_level)

    async def initialize_run(self, *, scope_name: str, level_slugs: Sequence[str]) -> None:
        self._list_names_by_level = {
            level_slug: _render_list_name(
                template=self._list_name_template,
                prefix=self._list_name_prefix,
                scope_name=scope_name,
                level_slug=level_slug,
                language=self._language,
            )
            for level_slug in level_slugs
        }
        self._missing_row_counts_by_level = {level_slug: 0 for level_slug in level_slugs}

    async def sync_row(
        self,
        level_slug: str,
        row: dict[str, Any],
    ) -> SyncRowResult:
        if self._row_progress_callback is not None:
            self._row_progress_callback(
                1,
                1,
                _ROW_PROGRESS_STATUS_DRY_RUN,
                str(row.get("Name", "")),
            )
        return SyncRowResult(status=SyncRowStatus.SKIPPED)

    async def sync_rows_by_level(
        self,
        rows_by_level: Mapping[str, list[dict[str, Any]]],
    ) -> SyncBatchResult:
        total_rows = sum(len(rows) for rows in rows_by_level.values())
        processed_rows = 0

        def report_row_progress(*, row: dict[str, Any], status: str) -> None:
            nonlocal processed_rows
            if self._row_progress_callback is None or total_rows <= 0:
                return
            processed_rows += 1
            self._row_progress_callback(
                processed_rows,
                total_rows,
                status,
                str(row.get("Name", "")),
            )

        added_count_by_level = {level_slug: 0 for level_slug in self._list_names_by_level}
        skipped_count_by_level = {
            level_slug: len(rows_by_level.get(level_slug, []))
            for level_slug in self._list_names_by_level
        }
        for rows in rows_by_level.values():
            for row in rows:
                report_row_progress(row=row, status=_ROW_PROGRESS_STATUS_DRY_RUN)
        return SyncBatchResult(
            added_count_by_level=added_count_by_level,
            skipped_count_by_level=skipped_count_by_level,
            failed_items=(),
        )

    async def finalize_run(self) -> None:
        return

    async def dump_debug_html(self, path: Path) -> bool:
        del path
        return False

    def set_row_progress_callback(self, callback: RowProgressCallback | None) -> None:
        self._row_progress_callback = callback


class GoogleMapsSyncWriter:
    """Sync rows to Google Maps lists by Michelin level."""

    def __init__(
        self,
        *,
        user_data_dir: Path,
        headless: bool,
        sync_delay_seconds: float,
        max_save_retries: int,
        list_name_template: str,
        list_name_prefix: str,
        on_missing_list: str,
        ignore_existing_lists_check: bool,
        on_row_synced: RowSyncedCallback | None = None,
        initial_synced_row_keys: frozenset[str] = frozenset(),
        probe_only: bool = False,
        language: str = "en",
        state_dir: str = "",
        driver: GoogleMapsDriverPort | None = None,
    ) -> None:
        self._driver: GoogleMapsDriverPort = driver or GoogleMapsDriver(
            GoogleMapsDriverConfig(
                user_data_dir=user_data_dir,
                headless=headless,
                sync_delay_seconds=sync_delay_seconds,
            )
        )
        self._max_save_retries = max(max_save_retries, 0)
        self._list_name_template = list_name_template
        self._list_name_prefix = list_name_prefix
        self._on_missing_list = on_missing_list
        self._ignore_existing_lists_check = ignore_existing_lists_check
        self._probe_only = probe_only
        self._language = language
        self._on_row_synced = on_row_synced
        self._synced_row_keys: set[str] = set(initial_synced_row_keys)
        self._state_dir = state_dir
        self._scope_name: str = ""

        self._list_names_by_level: dict[str, str] = {}
        self._list_created_by_level: dict[str, bool] = {}
        self._list_prepared_levels: set[str] = set()
        self._missing_levels: set[str] = set()
        self._missing_row_counts_by_level: dict[str, int] = {}
        self._row_progress_callback: RowProgressCallback | None = None
        self._debug_log_callback: SyncDebugLogCallback | None = None
        self._row_failure_callback: RowFailureCallback | None = None
        self._fast_shutdown_requested = False

    @property
    def list_names_by_level(self) -> Mapping[str, str]:
        return dict(self._list_names_by_level)

    @property
    def missing_row_counts_by_level(self) -> Mapping[str, int]:
        return dict(self._missing_row_counts_by_level)

    @property
    def list_created_by_level(self) -> Mapping[str, bool]:
        return dict(self._list_created_by_level)

    async def initialize_run(self, *, scope_name: str, level_slugs: Sequence[str]) -> None:
        self._scope_name = scope_name
        self._list_names_by_level = {
            level_slug: _render_list_name(
                template=self._list_name_template,
                prefix=self._list_name_prefix,
                scope_name=scope_name,
                level_slug=level_slug,
                language=self._language,
            )
            for level_slug in level_slugs
        }
        self._list_created_by_level = {level_slug: False for level_slug in level_slugs}
        self._list_prepared_levels = set()
        self._missing_row_counts_by_level = {level_slug: 0 for level_slug in level_slugs}
        self._missing_levels = set()

        await self._driver.start()
        await self._driver.open_maps_home()
        if self._probe_only:
            self._debug_log("Probe-only mode active; skipping auth check and list preparation.")
            return
        if not await self._driver.is_authenticated(refresh=False):
            raise GoogleMapsAuthRequiredError(
                "Google Maps session is not authenticated."
            )

    async def sync_row(
        self,
        level_slug: str,
        row: dict[str, Any],
    ) -> SyncRowResult:
        list_name = self._list_names_by_level[level_slug]

        def report_row_progress(status: str) -> None:
            if self._row_progress_callback is None:
                return
            self._row_progress_callback(
                1,
                1,
                status,
                str(row.get("Name", "")),
            )

        report_row_progress(_ROW_PROGRESS_STATUS_PROCESSING)

        if self._probe_only:
            outcome = await self._sync_one_row(
                level_slug=level_slug,
                list_name="",
                row=row,
                probe_only=True,
            )
        else:
            if level_slug in self._missing_levels:
                self._missing_row_counts_by_level[level_slug] += 1
                self._debug_log(
                    f"Skip row because target list is missing: level={level_slug} name='{row.get('Name', '')}'"
                )
                report_row_progress(_ROW_PROGRESS_STATUS_SKIPPED_MISSING_LIST)
                return SyncRowResult(status=SyncRowStatus.SKIPPED)

            if not await self._prepare_list_for_level(
                level_slug=level_slug,
                list_name=list_name,
                row_count=1,
            ):
                report_row_progress(_ROW_PROGRESS_STATUS_SKIPPED_MISSING_LIST)
                return SyncRowResult(status=SyncRowStatus.SKIPPED)

            outcome = await self._sync_one_row(level_slug=level_slug, list_name=list_name, row=row)

        if outcome.status == _ROW_STATUS_ADDED:
            self._debug_log(f"Row synced successfully: level={level_slug} name='{row.get('Name', '')}'")
            report_row_progress(_ROW_PROGRESS_STATUS_ADDED)
            return SyncRowResult(status=SyncRowStatus.ADDED)
        if outcome.status in (_ROW_STATUS_SKIPPED, _ROW_STATUS_PROBED):
            if outcome.status == _ROW_STATUS_PROBED:
                self._debug_log(
                    "Probe matched row without write: "
                    f"level={level_slug} name='{row.get('Name', '')}'"
                )
                report_row_progress(_ROW_PROGRESS_STATUS_PROBED)
            else:
                self._debug_log(f"Row skipped: level={level_slug} name='{row.get('Name', '')}'")
                report_row_progress(_ROW_PROGRESS_STATUS_SKIPPED)
            return SyncRowResult(status=SyncRowStatus.SKIPPED)
        if outcome.failure is not None:
            if self._on_missing_list == MISSING_LIST_POLICY_STOP:
                report_row_progress(_ROW_PROGRESS_STATUS_FAILED)
                raise GoogleMapsRowSyncFailFastError(
                    failure=outcome.failure,
                    added_count_by_level={level_slug: 0},
                    skipped_count_by_level={level_slug: 0},
                )
            self._debug_log(
                f"Row failed: level={level_slug} name='{row.get('Name', '')}' "
                f"reason='{outcome.failure.reason}'"
            )
            report_row_progress(_ROW_PROGRESS_STATUS_FAILED)
            return SyncRowResult(status=SyncRowStatus.FAILED, failure=outcome.failure)
        return SyncRowResult(status=SyncRowStatus.FAILED)

    async def sync_rows_by_level(
        self,
        rows_by_level: Mapping[str, list[dict[str, Any]]],
    ) -> SyncBatchResult:
        total_rows = sum(len(rows) for rows in rows_by_level.values())
        processed_rows = 0

        def report_row_progress(*, row: dict[str, Any], status: str) -> None:
            nonlocal processed_rows
            if self._row_progress_callback is None or total_rows <= 0:
                return
            processed_rows += 1
            self._row_progress_callback(
                processed_rows,
                total_rows,
                status,
                str(row.get("Name", "")),
            )

        def report_row_processing(*, row: dict[str, Any]) -> None:
            if self._row_progress_callback is None or total_rows <= 0:
                return
            self._row_progress_callback(
                processed_rows,
                total_rows,
                _ROW_PROGRESS_STATUS_PROCESSING,
                str(row.get("Name", "")),
            )

        added_count_by_level = {level_slug: 0 for level_slug in self._list_names_by_level}
        skipped_count_by_level = {level_slug: 0 for level_slug in self._list_names_by_level}
        failed_items: list[SyncItemFailure] = []

        for level_slug, list_name in self._list_names_by_level.items():
            rows = rows_by_level.get(level_slug, [])
            if not rows:
                continue

            if self._probe_only:
                for row in rows:
                    report_row_processing(row=row)
                    outcome = await self._sync_one_row(
                        level_slug=level_slug,
                        list_name="",
                        row=row,
                        probe_only=True,
                    )
                    if outcome.status == _ROW_STATUS_PROBED:
                        skipped_count_by_level[level_slug] += 1
                        self._debug_log(
                            "Probe matched row without write: "
                            f"level={level_slug} name='{row.get('Name', '')}'"
                        )
                        report_row_progress(row=row, status=_ROW_PROGRESS_STATUS_PROBED)
                        continue
                    if outcome.status == _ROW_STATUS_SKIPPED:
                        skipped_count_by_level[level_slug] += 1
                        self._debug_log(
                            f"Row skipped: level={level_slug} name='{row.get('Name', '')}'"
                        )
                        report_row_progress(row=row, status=_ROW_PROGRESS_STATUS_SKIPPED)
                        continue
                    if outcome.failure is not None:
                        if self._on_missing_list == MISSING_LIST_POLICY_STOP:
                            report_row_progress(row=row, status=_ROW_PROGRESS_STATUS_FAILED)
                            raise GoogleMapsRowSyncFailFastError(
                                failure=outcome.failure,
                                added_count_by_level=added_count_by_level,
                                skipped_count_by_level=skipped_count_by_level,
                            )
                        failed_items.append(outcome.failure)
                        self._debug_log(
                            f"Row failed: level={level_slug} name='{row.get('Name', '')}' "
                            f"reason='{outcome.failure.reason}'"
                        )
                        report_row_progress(row=row, status=_ROW_PROGRESS_STATUS_FAILED)
                continue

            if level_slug in self._missing_levels:
                skipped_count_by_level[level_slug] += len(rows)
                self._missing_row_counts_by_level[level_slug] += len(rows)
                for row in rows:
                    self._debug_log(

                            f"Skip row because target list is missing: "
                            f"level={level_slug} name='{row.get('Name', '')}'"

                    )
                    report_row_progress(
                        row=row,
                        status=_ROW_PROGRESS_STATUS_SKIPPED_MISSING_LIST,
                    )
                continue

            if not await self._prepare_list_for_level(
                level_slug=level_slug,
                list_name=list_name,
                row_count=len(rows),
            ):
                skipped_count_by_level[level_slug] += len(rows)
                for row in rows:
                    report_row_progress(
                        row=row,
                        status=_ROW_PROGRESS_STATUS_SKIPPED_MISSING_LIST,
                    )
                continue

            for row in rows:
                report_row_processing(row=row)
                outcome = await self._sync_one_row(level_slug=level_slug, list_name=list_name, row=row)
                if outcome.status == _ROW_STATUS_ADDED:
                    added_count_by_level[level_slug] += 1
                    self._debug_log(
                        f"Row synced successfully: level={level_slug} name='{row.get('Name', '')}'"
                    )
                    report_row_progress(row=row, status=_ROW_PROGRESS_STATUS_ADDED)
                elif outcome.status in (_ROW_STATUS_SKIPPED, _ROW_STATUS_PROBED):
                    skipped_count_by_level[level_slug] += 1
                    if outcome.status == _ROW_STATUS_PROBED:
                        self._debug_log(

                                "Probe matched row without write: "
                                f"level={level_slug} name='{row.get('Name', '')}'"

                        )
                        report_row_progress(row=row, status=_ROW_PROGRESS_STATUS_PROBED)
                    else:
                        self._debug_log(
                            f"Row skipped: level={level_slug} name='{row.get('Name', '')}'"
                        )
                        report_row_progress(row=row, status=_ROW_PROGRESS_STATUS_SKIPPED)
                elif outcome.failure is not None:
                    if self._on_missing_list == MISSING_LIST_POLICY_STOP:
                        report_row_progress(row=row, status=_ROW_PROGRESS_STATUS_FAILED)
                        raise GoogleMapsRowSyncFailFastError(
                            failure=outcome.failure,
                            added_count_by_level=added_count_by_level,
                            skipped_count_by_level=skipped_count_by_level,
                        )
                    failed_items.append(outcome.failure)
                    self._debug_log(

                            f"Row failed: level={level_slug} name='{row.get('Name', '')}' "
                            f"reason='{outcome.failure.reason}'"

                    )
                    report_row_progress(row=row, status=_ROW_PROGRESS_STATUS_FAILED)

        return SyncBatchResult(
            added_count_by_level=added_count_by_level,
            skipped_count_by_level=skipped_count_by_level,
            failed_items=tuple(failed_items),
        )

    async def finalize_run(self) -> None:
        await self._driver.close(force=self._fast_shutdown_requested)

    async def dump_debug_html(self, path: Path) -> bool:
        return await self._driver.dump_page_html(path)

    def set_row_progress_callback(self, callback: RowProgressCallback | None) -> None:
        self._row_progress_callback = callback

    def set_debug_log_callback(self, callback: SyncDebugLogCallback | None) -> None:
        self._debug_log_callback = callback

    def set_row_failure_callback(self, callback: RowFailureCallback | None) -> None:
        self._row_failure_callback = callback

    def set_initial_synced_row_keys(self, row_keys: frozenset[str]) -> None:
        self._synced_row_keys = set(row_keys)

    def request_fast_shutdown(self) -> None:
        self._fast_shutdown_requested = True

    async def _invoke_row_failure_callback(self, failure: SyncItemFailure) -> None:
        """Invoke row failure callback to trigger snapshot capture at row level."""
        if self._row_failure_callback is None:
            return
        try:
            result = self._row_failure_callback(failure)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:  # noqa: BLE001
            self._debug_log(f"Row failure callback error: {exc}")

    async def _capture_debug_snapshot(self, *, context: str, row_name: str = "") -> None:
        """Capture debug HTML snapshot immediately for non-expected outcomes.

        This is called at the point of failure, before retries, to capture
        the exact state when search fails, save fails, or note write fails.
        """
        if not self._scope_name:
            return
        if self._debug_log_callback is None and self._row_failure_callback is None:
            return
        try:
            safe_row_name = safe_filename(row_name) if row_name else "unknown"
            snapshot_context = f"{context}-{safe_row_name}"
            debug_html_path = resolve_debug_html_path(
                state_dir=self._state_dir,
                scope_name=self._scope_name,
                context=snapshot_context,
            )
            if await self.dump_debug_html(debug_html_path):
                self._sanitize_debug_snapshot(debug_html_path)
                self._debug_log(f"Debug HTML snapshot written to: {debug_html_path}")
        except Exception as exc:  # noqa: BLE001
            self._debug_log(f"Failed to capture debug snapshot: {exc}")

    def _sanitize_debug_snapshot(self, debug_html_path: Path) -> None:
        try:
            raw_html = debug_html_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self._debug_log(f"Failed to read debug snapshot for redaction: {exc}")
            return

        redacted_html = redact_html_text(raw_html)
        try:
            debug_html_path.write_text(redacted_html, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self._debug_log(f"Failed to write redacted debug snapshot: {exc}")
            return

        markers = find_unredacted_sensitive_markers(redacted_html)
        if markers:
            marker_text = ", ".join(markers)
            self._debug_log(
                "Debug snapshot redaction incomplete; sensitive markers remain: "
                f"{marker_text}"
            )

    async def _prepare_list_for_level(
        self,
        *,
        level_slug: str,
        list_name: str,
        row_count: int,
    ) -> bool:
        if level_slug not in self._list_prepared_levels:
            if await self._driver.list_exists(list_name):
                if not self._ignore_existing_lists_check:
                    raise GoogleMapsListAlreadyExistsError(
                        f"List already exists when first needed: {list_name}"
                    )
                self._debug_log(
                    f"Reusing existing list at first use because ignore-existing-lists-check is enabled: {list_name}"
                )
            else:
                await self._driver.create_list(list_name)
                self._list_created_by_level[level_slug] = True
                self._debug_log(
                    f"Created target list on first use: level={level_slug} list='{list_name}'"
                )
            self._list_prepared_levels.add(level_slug)

        if await self._driver.open_list(list_name):
            return True
        self._debug_log(
            f"Unable to open target list '{list_name}' for level '{level_slug}'."
        )
        if self._on_missing_list == MISSING_LIST_POLICY_STOP:
            raise GoogleMapsListMissingDuringRunError(
                f"Target list is missing during active run: {list_name}"
            )
        self._missing_levels.add(level_slug)
        self._missing_row_counts_by_level[level_slug] += row_count
        return False

    def _record_row_synced(self, row_key: str, *, level_slug: str, list_name: str) -> None:
        self._synced_row_keys.add(row_key)
        if self._on_row_synced is not None:
            self._on_row_synced(row_key, level_slug, list_name)

    async def _sync_one_row(
        self,
        *,
        level_slug: str,
        list_name: str,
        row: dict[str, Any],
        probe_only: bool = False,
    ) -> _RowSyncOutcome:
        row_key = build_row_identity_key(level_slug, row)
        if not probe_only and row_key in self._synced_row_keys:
            self._debug_log(
                f"Checkpoint hit; skip already-synced row: level={level_slug} key={row_key}"
            )
            return _RowSyncOutcome(status=_ROW_STATUS_SKIPPED, failure=None)

        attempted_queries: tuple[str, ...] = ()
        last_transient_error: str | None = None
        place_saved_in_prior_attempt = False
        for attempt_index in range(self._max_save_retries + 1):
            self._debug_log(

                    f"Sync attempt {attempt_index + 1}/{self._max_save_retries + 1}: "
                    f"level={level_slug} name='{row.get('Name', '')}'"

            )
            try:
                return await self._sync_one_row_without_retry(
                    level_slug=level_slug,
                    list_name=list_name,
                    row=row,
                    row_key=row_key,
                    probe_only=probe_only,
                )
            except GoogleMapsListMissingDuringRunError:
                if self._on_missing_list == MISSING_LIST_POLICY_STOP:
                    raise
                self._missing_levels.add(level_slug)
                self._missing_row_counts_by_level[level_slug] += 1
                self._debug_log(
                    f"List disappeared while syncing row; continue policy active for '{list_name}'."
                )
                return _RowSyncOutcome(status=_ROW_STATUS_SKIPPED, failure=None)
            except GoogleMapsTransientError as exc:
                attempted_queries = build_place_query_attempts(row)
                last_transient_error = str(exc)
                self._debug_log(

                        f"Transient Maps error on row '{row.get('Name', '')}': "
                        f"{last_transient_error}"

                )
                await self._capture_debug_snapshot(
                    context="transient-error",
                    row_name=str(row.get("Name", "")),
                )
                if attempt_index < self._max_save_retries:
                    continue
            except GoogleMapsSelectorError as exc:
                attempted_queries = build_place_query_attempts(row)
                self._debug_log(

                        f"Selector failure while syncing row '{row.get('Name', '')}': "
                        f"{exc}"

                )
                failure = SyncItemFailure(
                    level_slug=level_slug,
                    row_key=row_key,
                    restaurant_name=str(row.get("Name", "")),
                    reason=f"SelectorRuntimeFailure: {exc}",
                    attempted_queries=attempted_queries,
                )
                await self._invoke_row_failure_callback(failure)
                return _RowSyncOutcome(
                    status=_ROW_STATUS_FAILED,
                    failure=failure,
                )
            except GoogleMapsNoteWriteError as exc:
                attempted_queries = build_place_query_attempts(row)
                self._debug_log(

                        f"Note write failure while syncing row '{row.get('Name', '')}': "
                        f"{exc}"

                )
                await self._capture_debug_snapshot(
                    context="note-write-failed",
                    row_name=str(row.get("Name", "")),
                )

                if attempt_index < self._max_save_retries:
                    self._debug_log(
                        f"Reloading Maps page before retry after note write failure. "
                        f"level={level_slug} name='{row.get('Name', '')}'"
                    )
                    # The place is saved to the list before the note write is attempted,
                    # so a note write failure means the place may already be in the list.
                    # The exc.place_saved flag can be unreliable (false negative), so we
                    # always mark this to treat a PlaceAlreadySaved error on the next
                    # attempt as a success rather than a fatal error.
                    place_saved_in_prior_attempt = True
                    await self._driver.open_maps_home()
                    continue
                failure = SyncItemFailure(
                    level_slug=level_slug,
                    row_key=row_key,
                    restaurant_name=str(row.get("Name", "")),
                    reason=f"NoteWriteFailed: {exc}",
                    attempted_queries=attempted_queries,
                )
                await self._invoke_row_failure_callback(failure)
                return _RowSyncOutcome(
                    status=_ROW_STATUS_FAILED,
                    failure=failure,
                )
            except GoogleMapsPlaceAlreadySavedError as exc:
                attempted_queries = build_place_query_attempts(row)
                if place_saved_in_prior_attempt:
                    self._debug_log(
                        f"Row already exists in list after note write failure retry; "
                        f"treating as success. "
                        f"level={level_slug} name='{row.get('Name', '')}'"
                    )
                    if not probe_only:
                        self._record_row_synced(row_key, level_slug=level_slug, list_name=list_name)
                    return _RowSyncOutcome(status=_ROW_STATUS_SKIPPED, failure=None)
                if self._ignore_existing_lists_check:
                    self._debug_log(
                        f"Row already exists in list; skipping write because "
                        f"ignore-existing-lists-check is enabled. "
                        f"level={level_slug} name='{row.get('Name', '')}'"
                    )
                    if not probe_only:
                        self._record_row_synced(row_key, level_slug=level_slug, list_name=list_name)
                    return _RowSyncOutcome(status=_ROW_STATUS_SKIPPED, failure=None)
                await self._capture_debug_snapshot(
                    context="place-already-saved",
                    row_name=str(row.get("Name", "")),
                )
                raise GoogleMapsPlaceAlreadySavedError(

                        f"Place already exists in list '{list_name}' for row "
                        f"'{row.get('Name', '')}'. Re-run with --ignore-existing-lists-check "
                        "to skip already-saved places."

                ) from exc

        if place_saved_in_prior_attempt:
            self._debug_log(
                f"Retry failed with transient error, but place was already saved "
                f"in a prior attempt; treating as success. "
                f"level={level_slug} name='{row.get('Name', '')}'"
            )
            if not probe_only:
                self._record_row_synced(row_key, level_slug=level_slug, list_name=list_name)
            return _RowSyncOutcome(status=_ROW_STATUS_SKIPPED, failure=None)

        failure = SyncItemFailure(
            level_slug=level_slug,
            row_key=row_key,
            restaurant_name=str(row.get("Name", "")),
            reason=f"TransientNavigationError: {last_transient_error or 'retry budget exhausted'}",
            attempted_queries=attempted_queries,
        )
        await self._invoke_row_failure_callback(failure)
        return _RowSyncOutcome(
            status=_ROW_STATUS_FAILED,
            failure=failure,
        )

    async def _sync_one_row_without_retry(
        self,
        *,
        level_slug: str,
        list_name: str,
        row: dict[str, Any],
        row_key: str,
        probe_only: bool,
    ) -> _RowSyncOutcome:
        attempted_queries = build_place_query_attempts(row)
        note_text = _build_place_note_text(row)
        saw_candidate = False
        saw_matchable_candidate = False
        _row_name = str(row.get("Name", ""))
        _t_row_start = monotonic()

        _previous_candidate_signature: str | None = None
        for query in attempted_queries:
            self._debug_log(
                f"Search query for row '{_row_name}': {query}"
            )
            _tq0 = monotonic()
            candidate = await self._driver.search_and_open_first_result(query)
            _tq1 = monotonic()

            # Stale candidate detection: when consecutive searches return
            # the exact same place, the browser page did not actually update.
            # Navigate to Maps home to force a fresh search on the next query.
            if candidate is not None:
                _candidate_signature = f"{candidate.name}|{candidate.address}"
                if _candidate_signature == _previous_candidate_signature:
                    self._debug_log(
                        f"Stale candidate detected for row '{_row_name}' on query '{query}' "
                        f"(same as previous: '{candidate.name}'). "
                        "Navigating to maps home before next query."
                    )
                    await self._driver.open_maps_home()
                    continue
                _previous_candidate_signature = _candidate_signature

            if candidate is None:
                self._debug_log(
                    f"[timing] search_and_match: {(_tq1-_tq0)*1000:.0f}ms  query={query!r}  result=no_candidate"
                )
                self._debug_log(
                    f"No Maps candidate for query: {query}"
                )
                await self._capture_debug_snapshot(
                    context="search-no-result",
                    row_name=_row_name,
                )
                continue

            if _is_saved_list_landing_candidate(candidate=candidate, list_name=list_name):
                self._debug_log(
                    f"[timing] search_and_match: {(_tq1-_tq0)*1000:.0f}ms  query={query!r}  result=list_candidate"
                )
                self._debug_log(
                    f"Search candidate for row '{_row_name}' matched list '{list_name}' instead of a place. "
                    "Navigating to maps home before next query."
                )
                await self._driver.open_maps_home()
                continue

            saw_candidate = True
            match_assessment = assess_place_match(row, candidate)
            match_strength = match_assessment.strength
            if (
                match_strength == "weak"
                and _is_coordinate_query(query)
                and not match_assessment.coordinate_like_candidate_name
            ):
                # Coordinate lookup is a high-signal fallback when Maps returns local-script names.
                match_strength = "medium"
            if (
                match_strength == "weak"
                and match_assessment.city_in_candidate_address
                and candidate.address
                and not match_assessment.coordinate_like_candidate_name
            ):
                # Cross-script fallback: trust the search engine's name matching
                # when the candidate is in the correct city and has a loaded place detail.
                # This handles cases where Maps returns English-translated names for
                # CJK restaurants (e.g. 南村 → "Nancun" or 無名推車燒餅 → "No Name Cart").
                match_strength = "medium"
                self._debug_log(
                    f"Cross-script city fallback: upgraded '{_row_name}' from weak to medium "
                    f"(city_in_address=True, candidate='{candidate.name}')."
                )
            if match_strength == "weak":
                location_overlap = ", ".join(match_assessment.location_overlap_tokens) or "<none>"
                cuisine_overlap = ", ".join(match_assessment.cuisine_overlap_tokens) or "<none>"
                postal_overlap = ", ".join(match_assessment.postal_code_overlap_tokens) or "<none>"
                self._debug_log(
                    f"[timing] search_and_match: {(_tq1-_tq0)*1000:.0f}ms  query={query!r}  result=weak_match"
                )
                self._debug_log(

                        f"Weak place match for row '{_row_name}' on query '{query}'. "
                        "Trying next query. "
                        f"Candidate(name='{candidate.name}', address='{candidate.address}', "
                        f"category='{candidate.category}'). "
                        f"Signals(name_match={match_assessment.name_match}, "
                        f"city_in_address={match_assessment.city_in_candidate_address}, "
                        f"coordinate_like_name={match_assessment.coordinate_like_candidate_name}, "
                        f"location_overlap={location_overlap}, "
                        f"postal_overlap={postal_overlap}, "
                        f"cuisine_overlap={cuisine_overlap})."

                )
                await self._capture_debug_snapshot(
                    context="search-weak-match",
                    row_name=_row_name,
                )
                continue
            saw_matchable_candidate = True
            self._debug_log(
                f"[timing] search_and_match: {(_tq1-_tq0)*1000:.0f}ms  query={query!r}  result={match_strength}_match"
            )

            if probe_only:
                self._debug_log(
                    f"[timing] TOTAL sync_one_row: {(monotonic()-_t_row_start)*1000:.0f}ms  name={_row_name!r}  outcome=probed"
                )
                return _RowSyncOutcome(status=_ROW_STATUS_PROBED, failure=None)

            _ts0 = monotonic()
            save_result = await self._driver.save_current_place_to_list(list_name, note_text=note_text)
            _ts1 = monotonic()
            self._debug_log(
                f"[timing] save_current_place: {(_ts1-_ts0)*1000:.0f}ms  list={list_name!r}  result={save_result!r}"
            )
            if not save_result:
                self._debug_log(

                        f"Save dialog list option not found for '{list_name}' "
                        f"while syncing row '{_row_name}'."

                )
                await self._capture_debug_snapshot(
                    context="save-failed",
                    row_name=_row_name,
                )
                if not await self._driver.open_list(list_name):
                    raise GoogleMapsListMissingDuringRunError(
                        f"List missing while saving row into {list_name}."
                    )
                continue

            self._record_row_synced(row_key, level_slug=level_slug, list_name=list_name)
            self._debug_log(
                f"[timing] TOTAL sync_one_row: {(monotonic()-_t_row_start)*1000:.0f}ms  name={_row_name!r}  outcome=added"
            )
            return _RowSyncOutcome(status=_ROW_STATUS_ADDED, failure=None)

        if saw_matchable_candidate:
            failure = SyncItemFailure(
                level_slug=level_slug,
                row_key=row_key,
                restaurant_name=_row_name,
                reason="SaveActionFailed",
                attempted_queries=attempted_queries,
            )
            await self._invoke_row_failure_callback(failure)
            self._debug_log(
                f"[timing] TOTAL sync_one_row: {(monotonic()-_t_row_start)*1000:.0f}ms  name={_row_name!r}  outcome=save_failed"
            )
            return _RowSyncOutcome(
                status=_ROW_STATUS_FAILED,
                failure=failure,
            )

        failure_reason = "AmbiguousPlaceMatch" if saw_candidate else "PlaceNotFound"
        failure = SyncItemFailure(
            level_slug=level_slug,
            row_key=row_key,
            restaurant_name=_row_name,
            reason=failure_reason,
            attempted_queries=attempted_queries,
        )
        await self._invoke_row_failure_callback(failure)
        self._debug_log(
            f"[timing] TOTAL sync_one_row: {(monotonic()-_t_row_start)*1000:.0f}ms  name={_row_name!r}  outcome={failure_reason}"
        )
        return _RowSyncOutcome(
            status=_ROW_STATUS_FAILED,
            failure=failure,
        )

    def _debug_log(self, message: str) -> None:
        if self._debug_log_callback is None:
            return
        self._debug_log_callback(message)




def _render_list_name(
    *,
    template: str,
    prefix: str,
    scope_name: str,
    level_slug: str,
    language: str = "en",
) -> str:
    level_labels = _resolve_level_label_map(language)
    level_badges = _resolve_level_badge_map(language)
    level_label = level_labels.get(level_slug, LEVEL_LABELS.get(level_slug, level_slug))
    level_badge = level_badges.get(level_slug, level_label)
    return template.format(
        prefix=prefix,
        scope=scope_name,
        level=level_slug,
        level_slug=level_slug,
        level_label=level_label,
        level_badge=level_badge,
    )


_COORDINATE_QUERY_PATTERN = re.compile(
    r"^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$"
)


def _is_coordinate_query(query: str) -> bool:
    return bool(_COORDINATE_QUERY_PATTERN.fullmatch(query))


def _is_saved_list_landing_candidate(*, candidate: PlaceCandidate, list_name: str) -> bool:
    candidate_name = _normalize_note_line(candidate.name).lower()
    normalized_list_name = _normalize_note_line(list_name).lower()
    if not candidate_name or not normalized_list_name:
        return False
    if candidate_name != normalized_list_name:
        return False
    # List-landing cards typically have no place metadata, unlike restaurants.
    return not _normalize_note_line(candidate.address) and not _normalize_note_line(candidate.category)


def _normalize_note_line(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _build_place_note_text(row: dict[str, Any]) -> str:
    cuisine = _normalize_note_line(row.get("Cuisine", ""))
    description = _normalize_note_line(row.get("Description", ""))
    if cuisine and description:
        return f"{cuisine}\n{description}"
    return cuisine or description
