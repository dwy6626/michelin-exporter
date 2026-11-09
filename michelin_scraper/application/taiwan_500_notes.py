"""Resume an exact field-only migration of positively owned, accepted Dishes notes."""

import asyncio
import time
from pathlib import Path
from typing import Any

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


async def edit_existing_note(page: Any, card: dict[str, Any], target_note: str) -> None:
    """Use the recorded Saved List textarea's normal input and blur save events."""

    button = page.locator(PLACE_CARD_SELECTOR).nth(card["index"])
    container = button.locator("../..")
    editor = container.get_by_role("textbox", name="Note", exact=True)
    if await button.locator(".fontHeadlineSmall").inner_text() != card["name"] or await editor.input_value() != card["note"]:
        raise ValueError("Saved List card changed immediately before its note edit.")
    # Use the production writer's keyboard path so every edit traverses the
    # focused Maps editor, including its normal key and change handlers.
    await editor.click()
    await editor.press("ControlOrMeta+A")
    await page.keyboard.type(target_note)
    await editor.press("Tab")


async def wait_existing_note_saved(page: Any, list_name: str, card: dict[str, Any], target_note: str, count: int) -> None:
    """Wait for the actual physical membership's public note, independent of debounced HTTP events."""

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        public = await read_anonymous_list_inventory(page, list_name, expected_count=count)
        matches = [row for row in public["rows"] if row["place_id"] == card["place_id"]]
        if len(matches) != 1:
            raise ValueError("Edited physical membership disappeared or became duplicated during note save.")
        if matches[0]["note"] == target_note:
            return
        if matches[0]["note"] != card["note"]:
            raise ValueError("Concurrent unexpected note change while waiting for the physical membership save.")
        await asyncio.sleep(0.2)
    raise ValueError(f"Physical membership note did not persist before deadline: {card['place_id']}")


async def clean_dishes_notes(
    *, state_dir: Path, endpoint: str, evidence_dir: Path, note_limit: int | None = None,
) -> dict[str, Any]:
    """Edit existing notes only, with a one-row reopen barrier and resumable real readbacks."""

    if endpoint != CRON_CDP_URL:
        raise ValueError("Dishes note cleanup requires the externally leased cron CDP 9223 browser.")
    _assert_browser_lane(endpoint)
    roster = accepted_dishes_roster(state_dir)
    ledger_path = state_dir / "dishes/memberships.json"
    ledger = _read_json(ledger_path)
    ledger["user_skip_policy"] = _read_json(state_dir / "user-skips.json")
    progress_path = state_dir / "dishes/note-cleanup.json"
    progress = _read_json(progress_path)
    targets = cleanup_targets(roster, ledger, progress)
    progress.update(source_fingerprint=roster.source_fingerprint, list_id=ledger["list_id"], targets=targets)
    evidence_dir = evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    driver = AwardMapsDriver(GoogleMapsDriverConfig(
        user_data_dir=Path("/unused-cdp-profile"), headless=True, sync_delay_seconds=0.1, google_cdp_url=endpoint,
    ))
    driver.owned_list_urls = {roster.document_name: ledger["list_url"]}
    writes = 0
    sequence = 0

    async def readback() -> tuple[dict[str, Any], str]:
        nonlocal sequence
        if not await driver.open_list(roster.document_name):
            page = await driver._require_page()
            body = await page.locator("body").inner_text()
            count = await page.locator(PLACE_CARD_SELECTOR).count()
            reason = (
                "Google unusual-traffic challenge; clear it manually in the existing cron browser before resuming"
                if "Our systems have detected unusual traffic" in body
                else "owned list title or physical list identity did not match"
            )
            raise ValueError(
                f"Accepted physical Dishes list did not reopen: {reason}; "
                f"list_id={ledger['list_id']}, rendered_cards={count}; inspect {evidence_dir / 'failure.html'}"
            )
        page = await driver._require_page()
        signed = await read_list_inventory(page, roster.document_name, expected_count=len(targets))
        public = await read_anonymous_list_inventory(page, roster.document_name, expected_count=len(targets))
        attach_physical_ids(signed, public)
        cards = check_cleanup_inventory(roster, ledger, signed, targets)
        check_cleanup_inventory(roster, ledger, public, targets)
        sequence += 1
        prefix = evidence_dir / f"readback-{_now().replace(':', '-').replace('.', '-')}-{sequence}"
        _write_json(prefix.with_suffix(".json"), {"signed_in": signed, "anonymous": public})
        await save_inventory_html(page, prefix.with_suffix(".html"))
        return cards, str(prefix.with_suffix(".json"))

    try:
        await driver.start()
        page = await driver._require_page()
        await page.add_init_script("performance.setResourceTimingBufferSize(10000)")
        cards, evidence = await readback()
        if not await driver.is_authenticated(refresh=False):
            raise ValueError("Current signed-in Maps DOM is required; no login retry is permitted.")
        _write_json(progress_path, progress)
        while True:
            # Read actual notes on every restart, including uncertain earlier writes.
            verified = [key for key, card in cards.items() if card["note"] == targets[key]["target_note"]]
            for key in verified:
                targets[key]["verified_at_utc"] = _now()
                targets[key]["readback_evidence"] = evidence
                ledger["confirmed"][key]["note"] = targets[key]["target_note"]
                ledger["confirmed"][key]["note_readback_evidence"] = evidence
            pending = [key for key in targets if key not in verified]
            progress.update(verified=len(verified), pending=len(pending), last_readback=evidence, updated_at_utc=_now())
            _write_json(progress_path, progress)
            _write_json(ledger_path, ledger)
            print(f"Dishes note readback: verified={len(verified)}, pending={len(pending)}, writes={writes}", flush=True)
            if not pending or (note_limit is not None and writes >= note_limit):
                break
            batch_size = 20 if progress.get("first_save_verified") else 1
            if note_limit is not None:
                batch_size = min(batch_size, note_limit - writes)
            batch = pending[:batch_size]
            for key in batch:
                progress["in_flight"] = {"source_key": key, "place_id": targets[key]["place_id"], "started_at_utc": _now()}
                _write_json(progress_path, progress)
                await edit_existing_note(page, cards[key], targets[key]["target_note"])
                await wait_existing_note_saved(page, roster.document_name, cards[key], targets[key]["target_note"], len(targets))
                writes += 1
            cards, evidence = await readback()
            if any(cards[key]["note"] != targets[key]["target_note"] for key in batch):
                raise ValueError("A note did not persist after reopening; checkpoint retained for read-before-write continuation.")
            if not progress.get("first_save_verified"):
                progress["first_save_verified"] = {"source_key": batch[0], "place_id": targets[batch[0]]["place_id"],
                                                   "readback_evidence": evidence, "memberships_unchanged": True}
        receipt = {
            "verified": progress["verified"], "pending": progress["pending"], "writes_this_run": writes,
            "source_fingerprint": roster.source_fingerprint, "list_id": ledger["list_id"],
            "checkpoint": str(progress_path.resolve()), "readback_evidence": evidence,
            "first_save_verified": progress.get("first_save_verified"),
        }
        _write_json(evidence_dir / "receipt.json", receipt)
        return receipt
    except BaseException as exc:
        try:
            await save_inventory_html(await driver._require_page(), evidence_dir / "failure.html")
        except Exception:
            pass
        _write_json(evidence_dir / "failure.json", {
            "at_utc": _now(), "error": str(exc), "writes_this_run": writes, "in_flight": progress.get("in_flight"),
        })
        raise
    finally:
        await driver.close()
