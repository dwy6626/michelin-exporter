"""Resume an exact field-only migration of positively owned, accepted Dishes notes."""

from asyncio import sleep
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlparse
from weakref import WeakKeyDictionary

from ..adapters.award_list_inventory import (
    PLACE_CARD_SELECTOR,
    attach_physical_ids,
    owned_source_key,
    read_anonymous_list_inventory,
    read_list_inventory,
    save_inventory_html,
)
from ..sources.my_maps import parse_my_maps_kml_text
from ..sources.taiwan_500_awards import (
    TAIWAN_500_AWARD_DEFINITIONS,
    Taiwan500AwardRoster,
    _fingerprint_roster,
    dishes_public_note,
)
from .taiwan_500_maintenance import (
    CRON_CDP_URL,
    AwardMapsDriver,
    GoogleMapsDriverConfig,
    _assert_browser_lane,
    _now,
    _place_id,
    _read_json,
    _write_json,
    audit_inventory,
    managed_rows,
    roster_note,
    source_row_key,
)


def accepted_dishes_roster(state_dir: Path) -> Taiwan500AwardRoster:
    """Load and fingerprint the already accepted local source, without network discovery."""

    applied = _read_json(state_dir / "applied.json")
    if applied.get("status") != "complete" or not applied.get("full_pair_acceptance"):
        raise ValueError("Note cleanup requires existing complete pair acceptance.")
    source = applied["lists"]["dishes"]
    edition = source["edition"]
    kml = (state_dir / "sources" / f"dishes-{edition}.kml").read_text(encoding="utf-8")
    parsed = parse_my_maps_kml_text(kml)
    fingerprint = _fingerprint_roster(
        award="dishes", edition=edition, document_name=source["document_name"],
        folder_name=source["folder_name"], rows=parsed.rows,
    )
    if (fingerprint != source["source_fingerprint"]
            or fingerprint != applied["source_fingerprints"]["dishes"]
            or parsed.document_name != source["document_name"]
            or parsed.skipped_rows or parsed.unsupported_rows
            or len(parsed.rows) != source["source_point_count"]):
        raise ValueError("Accepted local Dishes source identity changed; refusing note cleanup.")
    definition = next(d for d in TAIWAN_500_AWARD_DEFINITIONS if d.award == "dishes")
    return Taiwan500AwardRoster(
        award="dishes", edition=edition, document_name=source["document_name"], folder_name=source["folder_name"],
        source_urls=tuple(source["source_urls"]), map_url=source["map_url"], kml_url=source["kml_url"],
        source_point_count=source["source_point_count"], total_kml_point_count=source["total_kml_point_count"],
        excluded_folder_counts=tuple(source["excluded_folder_counts"].items()), parsed=parsed, filtered_kml=kml,
        source_fingerprint=fingerprint, note_template=definition.note_template.replace("{edition}", str(edition)),
    )


def cleanup_targets(roster: Taiwan500AwardRoster, ledger: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    """Pin exact source/physical identities and the permitted old-to-new transformation."""

    rows = {source_row_key(row): row for row in managed_rows(
        roster, ledger.get("verified_resolutions", {}), ledger.get("user_skip_policy"),
    )}
    confirmed = ledger["confirmed"]
    if set(rows) != set(confirmed) or not ledger.get("created_by_task") or ledger.get("list_name") != roster.document_name:
        raise ValueError("Cleanup requires the exact complete accepted owned membership set.")
    if progress and (progress.get("source_fingerprint") != roster.source_fingerprint
                     or progress.get("list_id") != ledger["list_id"] or set(progress["targets"]) != set(rows)):
        raise ValueError("Cleanup checkpoint belongs to a different source or list.")
    targets = progress.get("targets", {})
    physical_ids = set()
    for key, row in rows.items():
        saved = confirmed[key]
        place_id = saved.get("place_id")
        if not place_id or place_id != _place_id(saved["place_url"]) or place_id in physical_ids:
            raise ValueError("Cleanup requires unique confirmed physical place identities.")
        physical_ids.add(place_id)
        original = targets.get(key, {}).get("original_note", saved["note"])
        target = dishes_public_note(original)
        if target != roster_note(roster, row) or saved["note"] not in (original, target):
            raise ValueError("Existing note is not the exact accepted source note transformation.")
        expected = {"place_id": place_id, "original_note": original, "target_note": target}
        if key in targets and any(targets[key].get(field) != value for field, value in expected.items()):
            raise ValueError("Cleanup source/physical identity or transformation conflicts with its checkpoint.")
        targets.setdefault(key, expected)
    return targets


def check_cleanup_inventory(
    roster: Taiwan500AwardRoster, ledger: dict[str, Any], inventory: dict[str, Any], targets: dict[str, Any],
) -> dict[str, Any]:
    """Accept only original or target notes on exactly the confirmed physical memberships."""

    cards = {}
    for card in inventory["rows"]:
        key = owned_source_key(card, award="dishes", confirmed=ledger["confirmed"])
        if key in cards or key not in targets:
            raise ValueError("Duplicate or unexpected physical membership during note cleanup.")
        target = targets[key]
        if card["note"] not in (target["original_note"], target["target_note"]):
            raise ValueError(f"Concurrent or unexpected note change for source key {key}.")
        cards[key] = card
    if set(cards) != set(targets):
        raise ValueError("A confirmed physical membership is missing during note cleanup.")
    # Validate normal name, address, venue and physical-identity vetoes even for
    # rows whose original public note still needs the three fields removed.
    audit_inventory(roster=roster, ledger=ledger, inventory=dict(inventory, rows=[
        dict(card, note=targets[key]["target_note"]) for key, card in cards.items()
    ]))
    return cards


MIN_WRITE_INTERVAL_SECONDS = 3.0
MAX_NOTES_PER_RUN = 25
_LAST_WRITE: WeakKeyDictionary[Any, float] = WeakKeyDictionary()


class NoteRecoveryError(ValueError):
    """An occurrence must end without retrying this browser restriction or failure."""

    def __init__(self, message: str, *, status: str = "failed", retry_after_seconds: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after_seconds = retry_after_seconds


def retry_after_seconds(value: str | None) -> float | None:
    """Honor either HTTP Retry-After form; retain the backoff floor externally."""

    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError):
            return None


async def assert_unrestricted(page: Any) -> None:
    body = await page.locator("body").inner_text()
    if "Our systems have detected unusual traffic" in body or "/sorry/" in str(page.url):
        raise NoteRecoveryError("Google unusual-traffic challenge; ending this occurrence without retries", status="challenge")


async def edit_existing_note(page: Any, card: dict[str, Any], target_note: str) -> dict[str, Any]:
    """Pace every normal typed edit and require the recorded server save acknowledgment."""

    await assert_unrestricted(page)
    previous = _LAST_WRITE.get(page)
    if previous is not None:
        await sleep(max(0.0, MIN_WRITE_INTERVAL_SECONDS - (monotonic() - previous)))
    button = page.locator(PLACE_CARD_SELECTOR).nth(card["index"])
    container = button.locator("../..")
    editor = container.get_by_role("textbox", name="Note", exact=True)
    if await button.locator(".fontHeadlineSmall").inner_text() != card["name"] or await editor.input_value() != card["note"]:
        raise ValueError("Saved List card changed immediately before its note edit.")
    # Register before blur: this exact POST/status was recorded during the real
    # first-row save. Acknowledgment permits the next write, never verification.
    async with page.expect_response(lambda response: (
        urlparse(response.url).netloc == "www.google.com"
        and urlparse(response.url).path == "/maps/preview/entitylist/updateitem"
        and response.request.method == "POST"
    ), timeout=20000) as saved:
        await editor.click()
        await editor.press("ControlOrMeta+A")
        _LAST_WRITE[page] = monotonic()
        await page.keyboard.type(target_note)
        await editor.press("Tab")
    response = await saved.value
    if response.status == 429:
        raise NoteRecoveryError("Google note save returned HTTP 429", status="challenge",
                                retry_after_seconds=retry_after_seconds(response.headers.get("retry-after")))
    if response.status in (401, 403):
        await assert_unrestricted(page)
        raise NoteRecoveryError(f"Google note save returned HTTP {response.status}; no login retry", status="auth_blocked")
    if response.status != 200:
        raise NoteRecoveryError(f"Google note save returned HTTP {response.status}")
    await assert_unrestricted(page)
    return {"endpoint": "/maps/preview/entitylist/updateitem", "method": "POST", "status": response.status, "at_utc": _now()}


async def read_list_metadata(page: Any) -> dict[str, Any]:
    """Read the icon and sharing header from the recorded real owned-list DOM."""

    control = page.get_by_role("button", name="Edit icon", exact=True).or_(
        page.get_by_role("button", name="Choose icon", exact=True)
    ).or_(page.get_by_role("button", name="選擇圖示", exact=True))
    headers = await page.locator("h2.HUaNde").all_text_contents()
    if await control.count() != 1 or len(headers) != 1:
        raise NoteRecoveryError("List icon/sharing metadata is unavailable in the current signed DOM")
    images = control.locator("img")
    if await images.count() == 1:
        image = await images.get_attribute("src")
        if not image:
            raise NoteRecoveryError("List icon image has no comparable source")
        icon = {"kind": "emoji", "src": image}
    else:
        symbols = control.locator("span.google-symbols.SwaGS")
        if await symbols.count() != 1:
            raise NoteRecoveryError("List default icon has no comparable recorded symbol")
        icon = {"kind": "symbol", "text": await symbols.inner_text()}
    parts = [part.strip() for part in headers[0].split("·")]
    sharing = [part for part in parts if part in ("Shared", "Private", "已分享", "私人")]
    if len(sharing) != 1:
        raise NoteRecoveryError("List sharing metadata cannot be compared safely")
    return {"icon": icon, "sharing": "shared" if sharing[0] in ("Shared", "已分享") else "private"}



async def verify_cleanup_completion(
    *, driver: Any, state_dir: Path, baseline: dict[str, Any], evidence_dir: Path,
    dishes_metadata: dict[str, Any], progress: dict[str, Any], check_restriction: Any,
) -> dict[str, Any]:
    """Freshly verify unchanged Bowls and the pinned list metadata before completion."""

    bowls = baseline.get("bowls", {})
    if len(bowls.get("notes", {})) != 472:
        raise NoteRecoveryError("Completion requires the original 472-Bowls physical note baseline")
    ledger = _read_json(state_dir / "bowls/memberships.json")
    driver.owned_list_urls[bowls["name"]] = bowls["url"]
    page = await driver._require_page()
    await check_restriction(page)
    if not await driver.open_list(bowls["name"]):
        await assert_unrestricted(await driver._require_page())
        raise NoteRecoveryError("Cannot reopen the original physical Bowls list for completion")
    page = await driver._require_page()
    await check_restriction(page)
    signed = await read_list_inventory(page, bowls["name"], expected_count=472)
    await check_restriction(page)
    public = await read_anonymous_list_inventory(page, bowls["name"], expected_count=472)
    await check_restriction(page)
    bowls_metadata = await read_list_metadata(page)
    attach_physical_ids(signed, public)
    for inventory in (signed, public):
        notes = {row["place_id"]: row["note"] for row in inventory["rows"]}
        if notes != bowls["notes"] or len(inventory["rows"]) != 472:
            raise NoteRecoveryError("Bowls physical IDs or notes differ from the original baseline")
    if {saved["place_id"] for saved in ledger["confirmed"].values()} != set(bowls["notes"]):
        raise NoteRecoveryError("Bowls ownership ledger differs from the original baseline")
    # Other-list metadata must be supplied from recorded pre-migration evidence.
    # Missing evidence is a concrete completion blocker, never an implied pass.
    metadata_baseline = baseline.get("list_metadata")
    if (not isinstance(metadata_baseline, list) or len(metadata_baseline) < 5
            or len({item["name"] for item in metadata_baseline}) != len(metadata_baseline)
            or len([item for item in metadata_baseline if item.get("notes_by_name")]) < 3):
        raise NoteRecoveryError("All Dishes notes are clean; completion blocked: missing recorded other-list icon/sharing baseline")
    metadata_evidence = []
    for item in metadata_baseline:
        if item["name"] == baseline["dishes"]["name"]:
            actual = dishes_metadata
        elif item["name"] == bowls["name"]:
            actual = bowls_metadata
        else:
            await check_restriction(page)
            driver.owned_list_urls[item["name"]] = item["url"]
            if not await driver.open_list(item["name"]):
                await assert_unrestricted(page)
                raise NoteRecoveryError(f"Cannot read original list metadata: {item['name']}")
            await check_restriction(page)
            actual = await read_list_metadata(page)
        if actual != item["metadata"]:
            raise NoteRecoveryError(f"List icon/sharing differs from its original baseline: {item['name']}")
        if item.get("notes_by_name") is not None:
            expected = item["notes_by_name"]
            await check_restriction(page)
            inventory = await read_list_inventory(page, item["name"], expected_count=len(expected))
            await check_restriction(page)
            anonymous = await read_anonymous_list_inventory(page, item["name"], expected_count=len(expected))
            await check_restriction(page)
            attach_physical_ids(inventory, anonymous)
            for snapshot in (inventory, anonymous):
                actual_notes = {row["name"]: row["note"] for row in snapshot["rows"]}
                if len(actual_notes) != len(snapshot["rows"]) or actual_notes != expected:
                    raise NoteRecoveryError(f"Other-list notes differ from their original baseline: {item['name']}")
            _write_json(evidence_dir / f"other-list-{len(metadata_evidence)}.json", {"signed_in": inventory, "anonymous": anonymous})
        metadata_evidence.append(dict(item, readback=actual))
    evidence = {"at_utc": _now(), "dishes_verified": progress["verified"], "dishes_pending": progress["pending"],
                "bowls_signed": signed, "bowls_anonymous": public, "bowls_unchanged": True,
                "list_metadata": metadata_evidence, "icons_sharing_unchanged": True}
    path = evidence_dir / "completion.json"
    _write_json(path, evidence)
    return {"path": str(path), "dishes_signed_anonymous_clean": True, "physical_ids_unchanged": True,
            "bowls_unchanged": True, "other_lists_unchanged": True, "icons_sharing_unchanged": True}


async def clean_dishes_notes(
    *, state_dir: Path, endpoint: str, evidence_dir: Path, note_limit: int | None = None,
    baseline_path: Path | None = None,
) -> dict[str, Any]:
    """Run one bounded batch with fresh physical readbacks and no same-occurrence retries."""

    if endpoint != CRON_CDP_URL:
        raise ValueError("Dishes note cleanup requires the externally leased cron CDP 9223 browser.")
    _assert_browser_lane(endpoint, expected_job_id="7f9de040f528")
    roster = accepted_dishes_roster(state_dir)
    ledger_path = state_dir / "dishes/memberships.json"
    ledger = _read_json(ledger_path)
    ledger["user_skip_policy"] = _read_json(state_dir / "user-skips.json")
    progress_path = state_dir / "dishes/note-cleanup.json"
    progress = _read_json(progress_path)
    targets = cleanup_targets(roster, ledger, progress)
    progress.update(source_fingerprint=roster.source_fingerprint, list_id=ledger["list_id"], targets=targets)
    limit = min(MAX_NOTES_PER_RUN, note_limit if note_limit is not None else MAX_NOTES_PER_RUN)
    evidence_dir = evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    driver = AwardMapsDriver(GoogleMapsDriverConfig(
        user_data_dir=Path("/unused-cdp-profile"), headless=True, sync_delay_seconds=3, google_cdp_url=endpoint,
    ))
    driver.owned_list_urls = {roster.document_name: ledger["list_url"]}
    writes = 0
    sequence = 0
    observed_restriction: dict[str, Any] = {}

    def observe_response(response: Any) -> None:
        if response.status == 429:
            observed_restriction.update(status="challenge", retry_after_seconds=retry_after_seconds(response.headers.get("retry-after")))

    async def check_restriction(page: Any) -> None:
        if observed_restriction:
            raise NoteRecoveryError("Google returned HTTP 429; ending this occurrence", **observed_restriction)
        await assert_unrestricted(page)

    async def readback() -> tuple[dict[str, Any], str, dict[str, Any]]:
        nonlocal sequence
        opened = await driver.open_list(roster.document_name)
        page = await driver._require_page()
        await check_restriction(page)
        if not opened:
            count = await page.locator(PLACE_CARD_SELECTOR).count()
            raise NoteRecoveryError(f"Accepted physical Dishes list did not reopen: owned list title or physical identity mismatch; "
                                    f"list_id={ledger['list_id']}, rendered_cards={count}")
        if not await driver.is_authenticated(refresh=False):
            raise NoteRecoveryError("Current signed-in Maps DOM is required; no login retry is permitted", status="auth_blocked")
        signed = await read_list_inventory(page, roster.document_name, expected_count=len(targets))
        await check_restriction(page)
        public = await read_anonymous_list_inventory(page, roster.document_name, expected_count=len(targets))
        attach_physical_ids(signed, public)
        cards = check_cleanup_inventory(roster, ledger, signed, targets)
        check_cleanup_inventory(roster, ledger, public, targets)
        metadata = await read_list_metadata(page)
        if progress.get("list_metadata") is not None and metadata != progress["list_metadata"]:
            raise NoteRecoveryError("Dishes icon or sharing changed from the pre-batch checkpoint")
        progress.setdefault("list_metadata", metadata)
        sequence += 1
        prefix = evidence_dir / f"readback-{_now().replace(':', '-').replace('.', '-')}-{sequence}"
        _write_json(prefix.with_suffix(".json"), {"signed_in": signed, "anonymous": public, "metadata": metadata})
        await save_inventory_html(page, prefix.with_suffix(".html"))
        return cards, str(prefix.with_suffix(".json")), metadata

    def checkpoint(cards: dict[str, Any], evidence: str) -> list[str]:
        verified = [key for key, card in cards.items() if card["note"] == targets[key]["target_note"]]
        for key in verified:
            targets[key]["verified_at_utc"] = _now()
            targets[key]["readback_evidence"] = evidence
            ledger["confirmed"][key]["note"] = targets[key]["target_note"]
            ledger["confirmed"][key]["note_readback_evidence"] = evidence
        pending = [key for key in targets if key not in verified]
        progress.update(verified=len(verified), pending=len(pending), last_readback=evidence, updated_at_utc=_now())
        progress.pop("in_flight", None)
        _write_json(progress_path, progress)
        _write_json(ledger_path, ledger)
        return pending

    try:
        await driver.start()
        page = await driver._require_page()
        page.on("response", observe_response)
        await page.add_init_script("performance.setResourceTimingBufferSize(10000)")
        cards, evidence, metadata = await readback()
        pending = checkpoint(cards, evidence)
        # The original physical note baseline is immutable. Where old icon
        # evidence predates an authorized icon change, pin current metadata once
        # before any recovery write, then compare it at completion.
        baseline = _read_json(baseline_path) if baseline_path else {}
        pinned = False
        for item in baseline.get("list_metadata", []):
            if item.get("capture_before_first_write") and not item.get("recovery_metadata_pinned_at"):
                await check_restriction(page)
                driver.owned_list_urls[item["name"]] = item["url"]
                if not await driver.open_list(item["name"]):
                    await check_restriction(page)
                    raise NoteRecoveryError(f"Cannot capture current list metadata before writes: {item['name']}")
                await check_restriction(page)
                item["historical_metadata"] = item["metadata"]
                item["metadata"] = await read_list_metadata(page)
                item["recovery_metadata_pinned_at"] = _now()
                pinned = True
        if pinned:
            if baseline_path is None:
                raise NoteRecoveryError("A persistent baseline path is required to pin list metadata")
            _write_json(baseline_path, baseline)
            cards, evidence, metadata = await readback()
            pending = checkpoint(cards, evidence)
        # Existing verified first-row evidence is reusable. Without it the first
        # write gets its own reopen barrier before any remaining bounded writes.
        while pending and writes < limit:
            batch_size = limit - writes if progress.get("first_save_verified") else 1
            batch = pending[:batch_size]
            for key in batch:
                await check_restriction(page)
                progress["in_flight"] = {"source_key": key, "place_id": targets[key]["place_id"], "started_at_utc": _now()}
                _write_json(progress_path, progress)
                ack = await edit_existing_note(page, cards[key], targets[key]["target_note"])
                writes += 1
                progress["in_flight"]["save_acknowledgment"] = ack
                progress.setdefault("unverified_writes", {})[key] = progress["in_flight"].copy()
                _write_json(progress_path, progress)
            cards, evidence, metadata = await readback()
            if any(cards[key]["note"] != targets[key]["target_note"] for key in batch):
                raise NoteRecoveryError("A note did not persist after reopening; checkpoint retained for read-before-write continuation")
            pending = checkpoint(cards, evidence)
            progress.pop("unverified_writes", None)
            if not progress.get("first_save_verified"):
                progress["first_save_verified"] = {"source_key": batch[0], "place_id": targets[batch[0]]["place_id"],
                                                   "readback_evidence": evidence, "memberships_unchanged": True}
            _write_json(progress_path, progress)
        completion = None
        if not pending:
            baseline = _read_json(baseline_path) if baseline_path else {}
            if len(targets) != 317 or set(baseline.get("dishes", {}).get("notes", {})) != {t["place_id"] for t in targets.values()}:
                raise NoteRecoveryError("Completion requires all original 317 physical Dishes IDs in the baseline")
            completion = await verify_cleanup_completion(driver=driver, state_dir=state_dir, baseline=baseline,
                                                         evidence_dir=evidence_dir, dishes_metadata=metadata, progress=progress,
                                                         check_restriction=check_restriction)
        receipt = {
            "status": "completed" if completion else "partial", "browser_opened": True,
            "verified": progress["verified"], "pending": progress["pending"], "writes_this_run": writes,
            "source_fingerprint": roster.source_fingerprint, "list_id": ledger["list_id"],
            "checkpoint": str(progress_path.resolve()), "readback_evidence": evidence,
            "first_save_verified": progress.get("first_save_verified"), "completion_evidence": completion,
        }
        _write_json(evidence_dir / "receipt.json", receipt)
        return receipt
    except BaseException as exc:
        # Navigation can throw while the challenge DOM is already committed.
        # Classify that same page locally; never perform another request.
        if not isinstance(exc, NoteRecoveryError):
            try:
                await check_restriction(await driver._require_page())
            except NoteRecoveryError as restriction:
                exc = restriction
            except Exception:
                pass
        try:
            await save_inventory_html(await driver._require_page(), evidence_dir / "failure.html")
        except Exception:
            pass
        _write_json(progress_path, progress)
        _write_json(evidence_dir / "failure.json", {
            "at_utc": _now(), "status": getattr(exc, "status", "failed"), "error": str(exc),
            "retry_after_seconds": getattr(exc, "retry_after_seconds", retry_after_seconds(getattr(exc, "retry_after", None))),
            "writes_this_run": writes, "in_flight": progress.get("in_flight"),
        })
        raise
    finally:
        await driver.close()
