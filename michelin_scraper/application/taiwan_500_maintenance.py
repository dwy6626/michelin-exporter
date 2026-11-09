"""Maintain the two current official Taiwan award Saved Lists."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ..adapters.award_list_inventory import (
    ensure_viewer_link,
    list_id_from_url,
    read_anonymous_list_inventory,
    read_list_inventory,
    remove_owned_stale_rows,
    rename_owned_list,
    sanitize_inventory_html,
    save_inventory_html,
)
from ..adapters.google_maps_driver import (
    GoogleMapsDriver,
    GoogleMapsDriverConfig,
    GoogleMapsNoteWriteError,
    GoogleMapsTransientError,
)
from ..adapters.google_maps_sync_writer import GoogleMapsSyncWriter
from ..config.runtime import AWARDS_MAINTENANCE_MODES, DEFAULT_AWARDS_STATE_DIR
from ..sources.my_maps_note_formatter import build_my_maps_note_text
from ..sources.taiwan_500_awards import (
    Taiwan500AwardRoster,
    discover_taiwan_500_awards,
    roster_manifest_entry,
    write_roster_kml,
)
from .html_redaction import redact_html_text
from .place_matcher import PlaceCandidate, assess_place_match
from .place_query_builder import build_place_query_attempts
from .row_identity import build_row_identity_key
from .sync_models import SOURCE_MY_MAPS

IMPORTED_BUCKET = "imported"
CRON_CDP_URL = "http://127.0.0.1:9223"
INTERACTIVE_CDP_URL = "http://127.0.0.1:9222"
MANAGED_MARKER_FIELD = "管理代碼"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"State must be a JSON object: {path}")
    return value


def _assert_browser_lane(endpoint: str) -> None:
    if endpoint not in (INTERACTIVE_CDP_URL, CRON_CDP_URL):
        raise ValueError("Award maintenance requires the existing interactive 9222 or admitted cron 9223 browser.")
    if os.environ.get("HERMES_EXECUTION_KIND") == "cron" and endpoint != CRON_CDP_URL:
        raise ValueError("Cron execution must use the admitted 9223 browser.")
    if endpoint != CRON_CDP_URL:
        return
    token = os.environ.get("HERMES_CRON_BROWSER_LANE_TOKEN", "")
    if not token:
        raise ValueError("9223 requires the external runner's existing browser admission lease.")
    lock_path = Path.home() / ".hermes/cron/locks/browser-cdp.lock"
    with lock_path.open("r") as handle:
        before = json.load(handle)
        if before.get("token") != token or not before.get("job_id"):
            raise ValueError("Cron browser lease metadata does not match this execution.")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(handle, fcntl.LOCK_UN)
            raise ValueError("Cron browser mutex is not held by the external runner.")
        handle.seek(0)
        if json.load(handle) != before:
            raise ValueError("Cron browser lease changed during validation.")


def source_row_key(row: dict[str, Any]) -> str:
    return str(row.get("AwardSourceKey") or build_row_identity_key(IMPORTED_BUCKET, row))


def user_skip_decisions(roster: Taiwan500AwardRoster, policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply user decisions only to their exact official edition, revision and row."""

    decisions = policy.get("decisions", [])
    if not isinstance(decisions, list):
        raise ValueError("User skip decisions must be an array.")
    current = {source_row_key(row): row for row in roster.parsed.rows}
    selected = []
    seen = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("User skip decision must be an object.")
        if (decision.get("award"), decision.get("edition")) != (roster.award, roster.edition):
            continue
        key = decision.get("source_key")
        if not isinstance(key, str):
            raise ValueError("User skip requires an exact source key.")
        original = current.get(key)
        if (decision.get("source_fingerprint") != roster.source_fingerprint or original is None
                or decision.get("original_row") != original or decision.get("source_name") != original["Name"]):
            raise ValueError(f"User skip needs renewed approval for changed official source identity: {decision.get('source_name')!r}")
        if (key in seen or decision.get("decision") != "user_authorized_exclusion"
                or not all(decision.get(field) for field in ("reason", "authorization_quote", "provenance"))):
            raise ValueError("User skip requires a unique exact row, explicit authorization, reason and provenance.")
        seen.add(key)
        selected.append(decision)
    return selected


def managed_rows(
    roster: Taiwan500AwardRoster, resolutions: dict[str, Any] | None = None,
    user_skip_policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Keep official provenance and apply only exact-row, evidenced identity corrections."""

    skipped = {decision["source_key"] for decision in user_skip_decisions(roster, user_skip_policy or {})}
    rows = []
    for original in roster.parsed.rows:
        if source_row_key(original) in skipped:
            continue
        row = dict(original)
        row_key = build_row_identity_key(IMPORTED_BUCKET, row)
        row["AwardSourceKey"] = row_key
        fields = dict(row["DescriptionFields"])
        fields[MANAGED_MARKER_FIELD] = f"500-awards/{roster.award}/{row_key}"
        resolution = (resolutions or {}).get(row_key)
        if resolution is not None:
            if resolution.get("original_name") != original["Name"] or resolution.get("original_address") != original["Address"]:
                raise ValueError("Verified place evidence does not match the exact current official row.")
            evidence_urls = resolution.get("evidence_urls", [])
            if not evidence_urls or not all(str(url).startswith("https://") for url in evidence_urls):
                raise ValueError("Verified place evidence requires retained public source URLs.")
            if resolution.get("match_address"):
                row["AwardOriginalAddress"] = original["Address"]
                row["Address"] = resolution["match_address"]
            if resolution.get("aliases"):
                row["Aliases"] = tuple(dict.fromkeys((*row.get("Aliases", ()), *resolution["aliases"])))
            if resolution.get("required_names"):
                row["AwardRequiredNames"] = tuple(resolution["required_names"])
            fields["定位查核"] = str(resolution["reason"]) + "；查核: " + " ".join(evidence_urls)
        row["DescriptionFields"] = fields
        rows.append(row)
    return rows


def roster_note(roster: Taiwan500AwardRoster, row: dict[str, Any]) -> str:
    return build_my_maps_note_text(
        row, note_format="template",
        note_template=roster.note_template + " | 定位查核: {定位查核} | 管理: {管理代碼}",
    )


class AwardMapsDriver(GoogleMapsDriver):
    """Keep the chosen real place identity alongside each normal verified save."""

    last_confirmed_save: dict[str, Any] | None = None
    owned_list_urls: dict[str, str] | None = None
    required_names_by_query: dict[str, tuple[str, ...]] | None = None
    source_rows_by_query: dict[str, dict[str, Any]] | None = None

    async def dump_page_html(self, path: Path) -> bool:
        html = self._last_page_html_snapshot
        if self._page is not None:
            try:
                html = str(await self._page.content())
            except Exception:
                pass
        if html is None:
            return False
        redacted = sanitize_inventory_html(html)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(redacted, encoding="utf-8")
        return True

    async def open_maps_home(self) -> None:
        """Recover the Maps application in a fresh owned tab of the same browser."""

        if (self._config.google_cdp_url is not None and self._page is not None
                and str(self._page.url) != "about:blank"):
            await self.close()
            await self.start()
        await super().open_maps_home()

    async def search_and_open_first_acceptable_result(
        self, query: str, accept_candidate: Callable[[PlaceCandidate], bool], *, max_candidates: int = 5,
    ) -> PlaceCandidate | None:
        required = (self.required_names_by_query or {}).get(query, ())
        row = (self.source_rows_by_query or {}).get(query)
        return await super().search_and_open_first_acceptable_result(
            query, lambda candidate: (
                (row is None or _has_award_name_identity(row, candidate))
                and _has_required_venue_name(required, candidate) and accept_candidate(candidate)
            ),
            max_candidates=max_candidates,
        )

    async def open_list(self, list_name: str) -> bool:
        """Reopen a positively owned physical list without the Saved dashboard overlay."""

        page = await self._require_page()
        owned_url = (self.owned_list_urls or {}).get(list_name)
        if owned_url:
            if not owned_url.startswith("https://www.google.com/maps/"):
                raise ValueError("Owned list navigation requires its recorded public Maps URL.")
            expected_id = list_id_from_url(owned_url)
            await page.goto(owned_url, wait_until="commit")
            await self._wait_for_opened_list_context(page, list_name=list_name)
            return list_id_from_url(str(page.url)) == expected_id and await self._active_list_title_matches(page, list_name)
        opened = await super().open_list(list_name)
        if opened and await self._active_list_title_matches(page, list_name):
            list_id_from_url(str(page.url))
            self.owned_list_urls = dict(self.owned_list_urls or {})
            self.owned_list_urls[list_name] = str(page.url)
            return True
        return False

    async def save_current_place_to_list(self, list_name: str, note_text: str = "") -> bool:
        self.last_confirmed_save = None
        page = await self._require_page()
        candidate = await self._extract_current_place_candidate(page)
        place_url = str(page.url)
        place_id = _place_id(place_url)
        if candidate is None or not candidate.address or not place_id or "/maps/place/" not in place_url:
            raise GoogleMapsTransientError("Award save requires a rendered place address and concrete physical place URL.")
        try:
            result = await super().save_current_place_to_list(list_name, note_text)
        except GoogleMapsNoteWriteError as exc:
            # Award acceptance requires the note as well as membership. Keep the
            # confirmed save on Google, but never checkpoint an unverified note.
            raise GoogleMapsTransientError(f"Award note remains unverified for {candidate.name!r}: {exc}") from exc
        if result:
            self.last_confirmed_save = {
                "candidate": asdict(candidate) if candidate is not None else None,
                "place_url": place_url,
                "place_id": place_id,
                "note": note_text,
                "list_name": list_name,
                "confirmed_at_utc": _now(),
            }
        return result


def _place_id(url: str) -> str:
    match = re.search(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)(?:!|[/?&#]|$)", unquote(url), re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _has_required_venue_name(names: tuple[str, ...], candidate: PlaceCandidate) -> bool:
    """Keep an evidenced restaurant distinct from its containing hotel or building."""

    rendered = "".join(f"{candidate.name} {candidate.subtitle}".split()).casefold()
    return not names or any("".join(name.split()).casefold() in rendered for name in names)


def _has_award_name_identity(row: dict[str, Any], candidate: PlaceCandidate) -> bool:
    """Require source name evidence even when generic cuisine/address overlap scores medium."""

    if assess_place_match(row, candidate).name_match:
        return True
    names = (str(row["Name"]), *row.get("Aliases", ()))
    return any(
        name.strip() and re.match(re.escape(name.strip()) + r"(?:\s|$|[（(｜|])", candidate.name, re.IGNORECASE)
        for name in names
    )


async def sync_award_rows(
    *, roster: Taiwan500AwardRoster, endpoint: str, state_dir: Path,
    single_write: bool = False,
) -> dict[str, Any]:
    """Use the production writer and persist every confirmed source-row save."""

    award_dir = state_dir / roster.award
    ledger_path = award_dir / "memberships.json"
    ledger = _read_json(ledger_path)
    old_name = ledger.get("list_name")
    ledger.setdefault("confirmed", {})
    ledger.setdefault("list_name", roster.document_name)
    ledger["pending_source"] = roster_manifest_entry(roster)
    resolutions = _read_json(state_dir / "verified-place-evidence.json").get(roster.award, {})
    ledger["verified_resolutions"] = resolutions
    policy = _read_json(state_dir / "user-skips.json")
    ledger["user_skip_policy"] = {"decisions": user_skip_decisions(roster, policy)}
    rows = managed_rows(roster, resolutions, ledger["user_skip_policy"])
    rows_by_key = {build_row_identity_key(IMPORTED_BUCKET, row): row for row in rows}
    retry_keys = set(ledger.get("retry_source_keys", ())) | set(ledger.get("removed_owned_keys", ()))
    initial_keys = frozenset(
        transport_key for transport_key, row in rows_by_key.items()
        if (saved := ledger["confirmed"].get(source_row_key(row))) is not None
        and source_row_key(row) not in retry_keys
        and saved.get("note") == roster_note(roster, row)
    )
    driver = AwardMapsDriver(GoogleMapsDriverConfig(
        user_data_dir=Path("/unused-cdp-profile"), headless=True,
        sync_delay_seconds=0.1, google_cdp_url=endpoint,
    ))
    driver.required_names_by_query = {
        query: tuple(row["AwardRequiredNames"])
        for row in rows if row.get("AwardRequiredNames")
        for query in build_place_query_attempts(row)
    }
    driver.source_rows_by_query = {query: row for row in rows for query in build_place_query_attempts(row)}
    if ledger.get("created_by_task") and ledger.get("list_url"):
        driver.owned_list_urls = {str(ledger["list_name"]): str(ledger["list_url"]), roster.document_name: str(ledger["list_url"])}

    def confirmed(row_key: str, level_slug: str, list_name: str) -> None:
        del level_slug
        saved = driver.last_confirmed_save
        if saved is None or saved["list_name"] != list_name:
            raise RuntimeError("Confirmed source row has no corresponding verified place-save evidence.")
        row = rows_by_key[row_key]
        if saved["note"] != roster_note(roster, row):
            raise RuntimeError("Confirmed place note does not belong to the source row.")
        ledger["confirmed"][source_row_key(row)] = dict(saved, source_row=row)
        if owned_url := (driver.owned_list_urls or {}).get(list_name):
            ledger["list_url"] = owned_url
            ledger["list_id"] = list_id_from_url(owned_url)
        for retry_field in ("retry_source_keys", "removed_owned_keys"):
            ledger[retry_field] = [key for key in ledger.get(retry_field, ()) if key != source_row_key(row)]
        ledger["created_by_task"] = bool(
            ledger.get("created_by_task") or writer.list_created_by_level.get(IMPORTED_BUCKET)
        )
        ledger["updated_at_utc"] = _now()
        _write_json(ledger_path, ledger)

    writer = GoogleMapsSyncWriter(
        user_data_dir=Path("/unused-cdp-profile"), google_cdp_url=endpoint,
        headless=True, sync_delay_seconds=0.1, max_save_retries=1,
        list_name_template="{scope}", list_name_prefix="", on_missing_list="stop",
        ignore_existing_lists_check=True, on_row_synced=confirmed,
        initial_synced_row_keys=initial_keys, source=SOURCE_MY_MAPS,
        note_format="template", note_template=roster.note_template + " | 定位查核: {定位查核} | 管理: {管理代碼}",
        language="zh_TW", state_dir=str(award_dir), driver=driver,
    )
    writer.set_debug_log_callback(lambda message: print(f"[{roster.award}] {message}", flush=True))
    _write_json(ledger_path, ledger)
    try:
        if old_name and old_name != roster.document_name:
            if not ledger.get("created_by_task"):
                raise ValueError("Refusing to rename a list without positive task ownership.")
            await driver.start()
            await driver.open_maps_home()
            if not await driver.is_authenticated(refresh=False):
                raise ValueError("Google sign-in is required in the admitted browser.")
            await rename_owned_list(
                driver, str(old_name), roster.document_name, expected_list_id=ledger.get("list_id"),
            )
            await driver.close()
            ledger["list_name"] = roster.document_name
            _write_json(ledger_path, ledger)
        await writer.initialize_run(scope_name=roster.document_name, level_slugs=(IMPORTED_BUCKET,))
        if not ledger.get("created_by_task") and await driver.list_exists(roster.document_name):
            raise ValueError("Refusing to adopt an existing list without positive task ownership.")
        pending_rows = [row for row in rows if build_row_identity_key(IMPORTED_BUCKET, row) not in initial_keys]
        selected_rows = pending_rows[:1] if single_write else pending_rows
        if selected_rows:
            await writer.sync_rows_by_level({IMPORTED_BUCKET: selected_rows})
        ledger["created_by_task"] = bool(ledger.get("created_by_task") or writer.list_created_by_level.get(IMPORTED_BUCKET))
        _write_json(ledger_path, ledger)
        return ledger
    except BaseException as exc:
        if writer.list_created_by_level.get(IMPORTED_BUCKET):
            ledger["created_by_task"] = True
            if owned_url := (driver.owned_list_urls or {}).get(roster.document_name):
                ledger["list_url"] = owned_url
                ledger["list_id"] = list_id_from_url(owned_url)
            _write_json(ledger_path, ledger)
        debug_path = award_dir / "debug" / f"maintenance-failure-{datetime.now(UTC):%Y%m%dT%H%M%S}.html"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        if await writer.dump_debug_html(debug_path):
            debug_path.write_text(redact_html_text(debug_path.read_text()), encoding="utf-8")
        _write_json(award_dir / "last-failure.json", {
            "at_utc": _now(), "error": str(exc), "debug_path": str(debug_path),
            "source_applied": False,
        })
        raise
    finally:
        await writer.finalize_run()


def audit_inventory(
    *, roster: Taiwan500AwardRoster, ledger: dict[str, Any],
    inventory: dict[str, Any], allow_stale: bool = False,
) -> dict[str, Any]:
    """Reconcile every official row with an independently read physical card."""

    expected_rows = {
        source_row_key(row): row for row in managed_rows(roster, ledger.get("verified_resolutions", {}), ledger.get("user_skip_policy"))
    }
    associations: dict[str, Any] = {}
    stale = []
    physical_ids = set()
    for card in inventory["rows"]:
        markers = card["source_markers"]
        if len(markers) != 1:
            raise ValueError(f"Unidentified or duplicate source association in {roster.award}: {card['name']!r}")
        award, key = markers[0].split("/", 1)
        if award != roster.award or key not in ledger["confirmed"]:
            raise ValueError("Independent readback contains a membership not positively owned by this task.")
        if key not in expected_rows:
            if not allow_stale:
                raise ValueError("Independent readback still contains an owned stale membership.")
            stale.append(key)
            continue
        if key in associations:
            raise ValueError("Independent readback contains duplicate physical memberships for a source row.")
        row = expected_rows[key]
        saved = ledger["confirmed"][key]
        actual_note = card["note"] or card["text"]
        if " ".join(roster_note(roster, row).split()) not in " ".join(actual_note.split()):
            raise ValueError(f"Independent note readback differs for {row['Name']!r}.")
        candidate_data = saved.get("candidate")
        if not candidate_data or not saved.get("place_id"):
            raise ValueError(f"Confirmed save lacks a physical place identity for {row['Name']!r}.")
        candidate = PlaceCandidate(**dict(candidate_data, name=card["name"]))
        if not _has_award_name_identity(row, candidate):
            raise ValueError(f"Independent card lacks the award restaurant name identity for {row['Name']!r}.")
        if not _has_required_venue_name(tuple(row.get("AwardRequiredNames", ())), candidate):
            raise ValueError(f"Independent card identifies the containing building instead of {row['Name']!r}.")
        assessment = assess_place_match(row, candidate)
        if assessment.hard_veto or assessment.house_number_conflict or assessment.strength == "weak":
            raise ValueError(f"Independent card identity needs review for {row['Name']!r}: {card['name']!r}.")
        if saved["place_id"] in physical_ids:
            raise ValueError("Different official rows resolved to the same physical place; reconcile their award notes before acceptance.")
        physical_ids.add(saved["place_id"])
        if card.get("place_id") and card["place_id"] != saved["place_id"]:
            raise ValueError(f"Anonymous physical place identity differs for {row['Name']!r}.")
        associations[key] = {
            "source_name": row["Name"], "source_address": row.get("AwardOriginalAddress", row["Address"]),
            "matching_address": row["Address"],
            "readback_name": card["name"], "physical_index": card["index"],
            "place_id": saved["place_id"], "candidate_address": candidate.address,
            "match_strength": assessment.strength,
        }
    missing = set(expected_rows) - set(associations)
    if missing:
        names = [expected_rows[key]["Name"] for key in sorted(missing)]
        raise ValueError(f"Independent readback is missing {len(missing)} official rows: {names[:8]}")
    return {"source_rows": len(expected_rows), "physical_memberships": len(physical_ids),
            "missing": 0, "duplicates": 0, "unidentified": 0,
            "owned_stale_keys": stale, "associations": associations}


def retain_inventory_retry_work(
    roster: Taiwan500AwardRoster, ledger: dict[str, Any], inventory: dict[str, Any],
) -> None:
    """Keep ownership evidence while retrying absent memberships or nonpersistent notes."""

    expected = {source_row_key(row): row for row in managed_rows(
        roster, ledger.get("verified_resolutions", {}), ledger.get("user_skip_policy"),
    )}
    readable = {}
    for card in inventory["rows"]:
        if len(card["source_markers"]) == 1:
            award, key = card["source_markers"][0].split("/", 1)
            if award == roster.award:
                readable[key] = card["note"] or card["text"]
    retry: set[str] = set(ledger.get("retry_source_keys", ()))
    for key, row in expected.items():
        if " ".join(roster_note(roster, row).split()) not in " ".join(readable.get(key, "").split()):
            retry.add(key)
    ledger["retry_source_keys"] = sorted(retry)


async def verify_awards(
    *, rosters: tuple[Taiwan500AwardRoster, ...], endpoint: str,
    state_dir: Path, reconcile_stale: bool,
) -> dict[str, Any]:
    """Verify complete replacements before cleanup, then independently read both viewer lists."""

    driver = AwardMapsDriver(GoogleMapsDriverConfig(
        user_data_dir=Path("/unused-cdp-profile"), headless=True,
        sync_delay_seconds=0.1, google_cdp_url=endpoint,
    ))
    report: dict[str, Any] = {}
    ledgers = {roster.award: _read_json(state_dir / roster.award / "memberships.json") for roster in rosters}
    policy = _read_json(state_dir / "user-skips.json")
    for roster in rosters:
        ledgers[roster.award]["user_skip_policy"] = {"decisions": user_skip_decisions(roster, policy)}
    driver.owned_list_urls = {
        str(ledger["list_name"]): str(ledger["list_url"])
        for ledger in ledgers.values() if ledger.get("created_by_task") and ledger.get("list_url")
    }
    for award, ledger in ledgers.items():
        for saved in ledger.get("confirmed", {}).values():
            saved["place_id"] = _place_id(saved["place_url"])
        _write_json(state_dir / award / "memberships.json", ledger)
    try:
        await driver.start()
        await driver.open_maps_home()
        if not await driver.is_authenticated(refresh=False):
            raise ValueError("Google sign-in is required in the admitted browser.")
        for roster in rosters:
            ledger = ledgers[roster.award]
            if not ledger.get("created_by_task"):
                raise ValueError("Award list ownership was not established by its initial production creation.")
            if not await driver.open_list(roster.document_name):
                raise ValueError(f"Owned award list is unavailable: {roster.document_name}")
            page = await driver._require_page()
            inventory = await read_list_inventory(page, roster.document_name)
            _write_json(state_dir / roster.award / "inventory-before-cleanup.json", inventory)
            retain_inventory_retry_work(roster, ledger, inventory)
            _write_json(state_dir / roster.award / "memberships.json", ledger)
            audit_inventory(roster=roster, ledger=ledger, inventory=inventory, allow_stale=reconcile_stale)

        # Both complete replacement inventories passed before any removal.
        if reconcile_stale:
            for roster in rosters:
                ledger = ledgers[roster.award]
                current_keys = {source_row_key(row) for row in managed_rows(
                    roster, ledger.get("verified_resolutions", {}), ledger["user_skip_policy"],
                )}
                removed = await remove_owned_stale_rows(
                    driver, list_name=roster.document_name, award=roster.award,
                    current_keys=current_keys, owned_keys=set(ledger["confirmed"]),
                )
                ledger["removed_owned_keys"] = sorted(set(ledger.get("removed_owned_keys", ())) | set(removed))
                _write_json(state_dir / roster.award / "memberships.json", ledger)

        for roster in rosters:
            ledger = ledgers[roster.award]
            award_dir = state_dir / roster.award
            # A fresh owned document keeps the observed renderer request available
            # after the earlier complete inventory's many lazy photo requests.
            await driver.open_maps_home()
            viewer_url = await ensure_viewer_link(
                driver, roster.document_name, award_dir / "viewer-link-dialog.html", allow_sharing_changes=reconcile_stale,
                existing_viewer_url=ledger.get("viewer_url"),
            )
            ledger["viewer_url"] = viewer_url
            _write_json(award_dir / "memberships.json", ledger)
            if not await driver.open_list(roster.document_name):
                raise ValueError("Owned list disappeared after viewer-link verification.")
            page = await driver._require_page()
            ledger["list_id"] = list_id_from_url(str(page.url))
            ledger["list_url"] = str(page.url)
            _write_json(award_dir / "memberships.json", ledger)
            eligible_count = len(managed_rows(roster, ledger.get("verified_resolutions", {}), ledger["user_skip_policy"]))
            public = await read_anonymous_list_inventory(page, roster.document_name, expected_count=eligible_count)
            public_audit = audit_inventory(roster=roster, ledger=ledger, inventory=public)
            _write_json(award_dir / "final-anonymous.json", dict(public, audit=public_audit, viewer_url=viewer_url))
            signed = await read_list_inventory(page, roster.document_name, expected_count=eligible_count)
            signed_audit = audit_inventory(roster=roster, ledger=ledger, inventory=signed)
            await save_inventory_html(page, award_dir / "final-signed-in.html")
            _write_json(award_dir / "final-signed-in.json", dict(signed, audit=signed_audit))

            report[roster.award] = {
                **roster_manifest_entry(roster), "viewer_url": viewer_url,
                "visibility": "anyone-with-link-view", "signed_in_count": signed["physical_count"],
                "anonymous_count": public["physical_count"], "failure_count": 0,
                "anonymous_transport": public["transport"],
                "missing": 0, "duplicates": 0, "unidentified": 0,
                "status": "complete", "authoritative_source_rows": roster.source_point_count,
                "unique_physical_rows": eligible_count, "verified_source_rows": public_audit["source_rows"],
                "user_skipped_source_rows": ledger["user_skip_policy"]["decisions"],
            }
            _write_json(state_dir / "verification-in-progress.json", report)
        return report
    except BaseException as exc:
        debug_path = state_dir / "debug" / f"verification-failure-{datetime.now(UTC):%Y%m%dT%H%M%S}.html"
        try:
            await save_inventory_html(await driver._require_page(), debug_path)
        except Exception:
            pass
        _write_json(state_dir / "last-verification-failure.json", {
            "at_utc": _now(), "error": str(exc), "source_applied": False,
        })
        raise
    finally:
        await driver.close()


def source_version_is_applied(
    applied: dict[str, Any], *, source_fingerprints: dict[str, str],
    resolution_fingerprint: str, user_skip_fingerprint: str,
) -> bool:
    """Compare official identity and local policy without browser or state writes."""

    return (applied.get("status") == "complete"
            and applied.get("source_fingerprints") == source_fingerprints
            and applied.get("resolution_fingerprint") == resolution_fingerprint
            and applied.get("user_skip_fingerprint") == user_skip_fingerprint)


async def run_maintenance(*, state_dir: Path, endpoint: str, mode: str = "sync") -> bool:
    """Check both sources, resume pending work, and apply only a fully verified pair."""

    _assert_browser_lane(endpoint)
    rosters = await asyncio.to_thread(discover_taiwan_500_awards)
    fingerprints = {roster.award: roster.source_fingerprint for roster in rosters}
    resolutions = _read_json(state_dir / "verified-place-evidence.json")
    resolution_fingerprint = hashlib.sha256(json.dumps(resolutions, sort_keys=True).encode()).hexdigest()
    policy = _read_json(state_dir / "user-skips.json")
    skips = [decision for roster in rosters for decision in user_skip_decisions(roster, policy)]
    user_skip_fingerprint = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
    applied = _read_json(state_dir / "applied.json")
    if mode == "sync" and source_version_is_applied(
        applied, source_fingerprints=fingerprints, resolution_fingerprint=resolution_fingerprint,
        user_skip_fingerprint=user_skip_fingerprint,
    ):
        await verify_browser_auth(endpoint)
        _write_json(state_dir / "last-check.json", {"checked_at_utc": _now(), "changed": False, "source_fingerprints": fingerprints})
        return False
    for roster in rosters:
        write_roster_kml(roster, state_dir / "sources" / f"{roster.award}-{roster.edition}.kml")
    if mode != "verify":
        for roster in rosters:
            await sync_award_rows(roster=roster, endpoint=endpoint, state_dir=state_dir, single_write=mode == "single-write")
    if mode == "single-write":
        return True
    verified = await verify_awards(rosters=rosters, endpoint=endpoint, state_dir=state_dir, reconcile_stale=mode == "sync")
    acceptance = {
        "status": "complete", "verified_at_utc": _now(), "lists": verified,
        "source_fingerprints": fingerprints, "failure_count": 0,
        "resolution_fingerprint": resolution_fingerprint,
        "user_skip_fingerprint": user_skip_fingerprint,
        "source_version_applied": True, "full_pair_acceptance": True,
        "authoritative_source_rows": sum(roster.source_point_count for roster in rosters),
        "independently_verified_source_rows": sum(record["verified_source_rows"] for record in verified.values()),
        "unresolved_source_rows": 0, "user_skipped_source_rows": skips,
        "all_owned_tabs_closed": True,
    }
    _write_json(state_dir / "acceptance-manifest.json", acceptance)
    _write_json(state_dir / "applied.json", acceptance)
    return True


async def verify_browser_auth(endpoint: str) -> None:
    """Check the current browser DOM even on a silent no-change maintenance run."""

    driver = GoogleMapsDriver(GoogleMapsDriverConfig(
        user_data_dir=Path("/unused-cdp-profile"), headless=True,
        sync_delay_seconds=0.1, google_cdp_url=endpoint,
    ))
    try:
        await driver.start()
        await driver.open_maps_home()
        if not await driver.is_authenticated(refresh=False):
            raise ValueError(f"Google Maps DOM does not confirm sign-in at {endpoint}; parent authentication is required.")
    finally:
        await driver.close()


def main() -> int:
    """Run source maintenance under a per-task lock and the existing external browser lease."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=DEFAULT_AWARDS_STATE_DIR,
                        help="Persistent source, ownership, retry, and complete readback evidence directory.")
    parser.add_argument("--google-cdp-url", default=os.environ.get("BROWSER_CDP_URL", INTERACTIVE_CDP_URL),
                        help="Existing signed-in 9222 browser, or external-runner-admitted 9223 browser for cron.")
    parser.add_argument("--mode", choices=AWARDS_MAINTENANCE_MODES, default=AWARDS_MAINTENANCE_MODES[0],
                        help=("sync resumes the complete workload; single-write saves one pending row per list; "
                              "verify reads both complete lists."))
    args = parser.parse_args()
    state_dir = Path(args.state_dir).expanduser()
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "maintenance.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        changed = asyncio.run(run_maintenance(state_dir=state_dir, endpoint=args.google_cdp_url, mode=args.mode))
    if changed:
        print("Award maintenance completed." if args.mode != "single-write" else "One-row stage completed; full acceptance is pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
