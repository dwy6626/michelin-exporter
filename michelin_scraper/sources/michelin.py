"""Michelin source adapter for generic Google Maps list sync."""

from collections.abc import Sequence
from typing import Any

import urllib3

from ..application.row_router import LevelRowRouter
from ..application.source_models import (
    SourceBucket,
    SourcePlan,
    SourceRunHandlers,
    SourceRunResult,
)
from ..application.sync_models import ScrapeSyncCommand
from ..application.sync_ports import SyncOutputPort
from ..catalog import LEVEL_BADGES, LEVEL_LABELS, normalize_target, resolve_language, resolve_target
from ..catalog.levels import build_rating_to_output_level_slug_map, resolve_output_level_slug
from ..config import HEADERS
from ..scraping import crawl, resolve_listing_scope_name

_SCOPE_NAME_FROM_LISTING_LANGUAGES = frozenset({"zh_tw"})


class MichelinSourceAdapter:
    """Source adapter for Michelin Guide restaurant listings."""

    def __init__(self, *, tls_verify: bool | str = True) -> None:
        self._tls_verify = tls_verify
        self._resolved_language = "en"
        self._local_language: str | None = None
        self._local_country_code: str | None = None

    def prepare(self, command: ScrapeSyncCommand, output: SyncOutputPort) -> SourcePlan:
        resolved_language = resolve_language(command.language)
        self._resolved_language = resolved_language
        language_key = _normalize_language_key(resolved_language)
        target = resolve_target(
            normalize_target(command.target),
            language=resolved_language,
        )
        self._local_language = target.local_language
        self._local_country_code = target.local_country_code
        effective_scope_name = _resolve_scope_name_for_lists(
            fallback_scope_name=target.scope_name,
            start_url=target.start_url,
            use_listing_scope_name=language_key in _SCOPE_NAME_FROM_LISTING_LANGUAGES,
            tls_verify=self._tls_verify,
            output=output,
        )
        return SourcePlan(
            source_id="michelin",
            scope_name=effective_scope_name,
            checkpoint_scope=target.scope_name,
            start_url=target.start_url,
            buckets=tuple(
                SourceBucket(
                    slug=level_slug,
                    label=LEVEL_LABELS[level_slug],
                    badge=LEVEL_BADGES[level_slug],
                )
                for level_slug in command.levels
            ),
        )

    def run(
        self,
        *,
        command: ScrapeSyncCommand,
        plan: SourcePlan,
        handlers: SourceRunHandlers,
    ) -> SourceRunResult:
        if plan.start_url is None:
            raise ValueError("Michelin source plan is missing start_url.")
        metrics = crawl(
            start_url=handlers.start_cursor or plan.start_url,
            on_page=handlers.on_page,
            on_item=self._build_bucketed_item_handler(
                handlers=handlers,
                bucket_slugs=tuple(bucket.slug for bucket in plan.buckets),
            ),
            sleep_seconds=command.sleep_seconds,
            tls_verify=self._tls_verify,
            start_page_number=handlers.start_page_number,
            start_estimated_total_pages=handlers.start_estimated_total_pages,
            initial_total_restaurants=handlers.initial_total_rows,
            progress_reporter=handlers.progress_reporter,
            max_pages=command.max_pages,
            on_interrupt=handlers.on_interrupt,
            local_language=self._local_language,
            local_country_code=self._local_country_code,
            requested_language=self._resolved_language,
        )
        return SourceRunResult.from_scrape_metrics(metrics)

    def group_local_rows_by_bucket(
        self,
        *,
        rows: list[dict[str, Any]],
        bucket_slugs: tuple[str, ...],
    ) -> dict[str, list[dict[str, Any]]]:
        grouped_rows: dict[str, list[dict[str, Any]]] = {bucket_slug: [] for bucket_slug in bucket_slugs}
        rows_for_rating_router: list[dict[str, Any]] = []

        for row in rows:
            explicit_level_slug = str(row.get("LevelSlug", "")).strip().lower()
            if not explicit_level_slug:
                rows_for_rating_router.append(row)
                continue
            resolved_output_level_slug = resolve_output_level_slug(
                explicit_level_slug,
                bucket_slugs,
            )
            if resolved_output_level_slug is None:
                allowed_values = ", ".join(bucket_slugs)
                raise ValueError(
                    f"Unsupported LevelSlug value in maps-probe-rows-file: '{explicit_level_slug}'. "
                    f"Allowed values: {allowed_values}."
                )
            grouped_rows[resolved_output_level_slug].append(row)

        if rows_for_rating_router:
            routed_rows = _build_row_router(bucket_slugs).group_rows_by_level(rows_for_rating_router)
            for bucket_slug in bucket_slugs:
                grouped_rows[bucket_slug].extend(routed_rows.get(bucket_slug, []))

        return grouped_rows

    def _build_bucketed_item_handler(
        self,
        *,
        handlers: SourceRunHandlers,
        bucket_slugs: tuple[str, ...],
    ) -> Any:
        row_router = _build_row_router(bucket_slugs)

        def _on_item(
            page_number: int,
            estimated_total_pages: int | None,
            total_restaurants_expected: int | None,
            row: dict[str, Any],
        ) -> None:
            rows_by_bucket = row_router.group_rows_by_level([row])
            for bucket_slug, rows in rows_by_bucket.items():
                for routed_row in rows:
                    handlers.on_item(
                        page_number,
                        estimated_total_pages,
                        total_restaurants_expected,
                        bucket_slug,
                        routed_row,
                    )

        return _on_item


def _resolve_tls_verify(ca_bundle: str, insecure: bool) -> bool | str:
    return ca_bundle if ca_bundle else (not insecure)


def create_michelin_source_adapter(command: ScrapeSyncCommand) -> MichelinSourceAdapter:
    """Create a Michelin source adapter from command-level HTTP options."""

    if command.insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return MichelinSourceAdapter(
        tls_verify=_resolve_tls_verify(ca_bundle=command.ca_bundle, insecure=command.insecure),
    )


def _build_row_router(bucket_slugs: Sequence[str]) -> LevelRowRouter:
    # Register all known Michelin ratings so the router can distinguish
    # "known but not selected" (skip) from "truly unknown" (error).
    return LevelRowRouter(
        level_slugs=bucket_slugs,
        rating_to_level_slug=build_rating_to_output_level_slug_map(tuple(bucket_slugs)),
    )


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
        if (
            _contains_ascii_letters(listing_scope_name)
            and not _is_ascii_only(listing_scope_name)
            and not _is_ascii_only(fallback_scope_name)
        ):
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
