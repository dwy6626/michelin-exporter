"""Maintain only the three existing, accepted Taiwan Michelin Saved Lists."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ..adapters.award_list_inventory import PLACE_CARD_SELECTOR, read_anonymous_list_inventory, read_list_inventory, save_inventory_html
from ..adapters.google_maps_driver import GoogleMapsDriver, GoogleMapsDriverConfig
from ..adapters.google_maps_sync_writer import GoogleMapsSyncWriter
from ..config.runtime import DEFAULT_MICHELIN_STATE_DIR, MICHELIN_MAINTENANCE_MODES
from ..sources.taiwan_michelin import SOURCE_ID, semantic_fingerprint, write_probe
from .sync_models import SOURCE_MICHELIN


def _read(path: Path) -> Any:
    return json.loads(path.read_text())


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def row_key(row: dict[str, Any]) -> str:
    return semantic_fingerprint([row])


def validate_acceptance(state_dir: Path) -> tuple[dict[str, Any], Path, list[dict[str, Any]]]:
    """Require actual complete source, signed-in and anonymous accepted evidence."""

    path = state_dir / "acceptance-manifest.json"
    acceptance = _read(path)
    source_path = Path(acceptance.get("source_rows_file", state_dir / "acceptance/source-434-rows.jsonl"))
    rows = _rows(source_path)
    expected = acceptance.get("source", {}).get("total")
    membership = acceptance.get("membership_audit", {})
    if (acceptance.get("status") != "complete" or not expected or len(rows) != expected
            or membership.get("missing") != 0 or membership.get("duplicates", membership.get("duplicate_memberships")) != 0
            or membership.get("unidentified", membership.get("unidentified_memberships")) != 0 or membership.get("source_rows") != expected
            or membership.get("physical_memberships") != expected):
        raise ValueError("Michelin acceptance is absent, partial or does not reconcile every source row.")
    counts = Counter(row["level_slug"] for row in rows)
    if set(acceptance.get("lists", {})) != set(counts):
        raise ValueError("Michelin acceptance does not contain the exact accepted list buckets.")
    for level, count in counts.items():
        record = acceptance["lists"][level]
        if (record.get("source_count") != count or record.get("signed_in_physical_count") != count
                or record.get("anonymous_physical_count") != count or record.get("visibility") != "anyone-with-link-view"
                or not record.get("url")):
            raise ValueError("Michelin acceptance lacks complete signed-in/anonymous list evidence.")
    artifacts = acceptance.get("artifacts", {})
    if not artifacts:
        raise ValueError("Michelin acceptance has no bound original evidence artifacts.")
    for name, proof in artifacts.items():
        artifact_path = state_dir / "acceptance" / name
        if not artifact_path.is_file() or _sha(artifact_path) != proof.get("sha256"):
            raise ValueError(f"Michelin acceptance artifact changed or is missing: {name}")
    if source_path.name not in artifacts or _sha(source_path) != artifacts[source_path.name].get("sha256"):
        raise ValueError("Michelin accepted source rows are not bound to acceptance.")
    semantic_fingerprint(rows)
    return acceptance, source_path, rows


def applied_from_acceptance(state_dir: Path) -> dict[str, Any]:
    """Derive the baseline solely from the existing complete accepted source rows."""

    acceptance, source_path, rows = validate_acceptance(state_dir)
    applied = {
        "status": "complete", "source_id": SOURCE_ID, "source_fingerprint": semantic_fingerprint(rows),
        "source_point_count": len(rows), "acceptance_manifest": str((state_dir / "acceptance-manifest.json").resolve()),
        "acceptance_sha256": _sha(state_dir / "acceptance-manifest.json"),
        "source_rows_file": str(source_path.resolve()), "source_rows_sha256": _sha(source_path),
        "verified_at_utc": acceptance.get("completed_at_utc"),
    }
    return applied


def bootstrap_applied(state_dir: Path) -> dict[str, Any]:
    applied = applied_from_acceptance(state_dir)
    existing = state_dir / "applied.json"
    if existing.exists() and _read(existing) != applied:
        raise ValueError("Existing Michelin applied metadata disagrees with acceptance; reconcile it before bootstrap.")
    _write(existing, applied)
    return applied


def _assert_admitted(endpoint: str) -> None:
    if endpoint != "http://127.0.0.1:9223":
        raise ValueError("Michelin production maintenance requires the existing admitted cron 9223 browser.")
    token = os.environ.get("HERMES_CRON_BROWSER_LANE_TOKEN")
    with (Path.home() / ".hermes/cron/locks/browser-cdp.lock").open() as lock:
        proof = json.load(lock)
        if not token or proof.get("token") != token or not proof.get("job_id"):
            raise ValueError("Michelin browser admission lease is missing or does not match this job.")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(lock, fcntl.LOCK_UN)
            raise ValueError("The external browser mutex must cover the complete Michelin maintenance run.")


def _aliases(state_dir: Path) -> dict[str, str]:
    aliases = {}
    for name in ("final-point-verification.json", "public-point-verification.json"):
        path = state_dir / "acceptance" / name
        if path.exists():
            for record in _read(path):
                if record.get("source_url") and record.get("list_label"):
                    aliases[record["source_url"]] = record["list_label"]
    return aliases


def audit_memberships(
    rows: list[dict[str, Any]], inventory: dict[str, Any], aliases: dict[str, str], *,
    allow_stale: bool = False, optional_missing_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Associate every source row once; ambiguous or extra physical memberships fail closed."""

    associations = {}
    used: set[int] = set()
    for row in rows:
        description = " ".join(str(row.get("Description", "")).split())
        name = aliases.get(str(row.get("Michelin Website", "")), str(row.get("NameLocal") or row["Name"]))
        matches = []
        for card in inventory["rows"]:
            note = " ".join(str(card.get("note") or card.get("text", "")).split())
            if ((len(description) >= 24 and description[:min(80, len(description))] in note)
                    or (card["name"] == name and (len(description) < 24 or description[:24] in note))):
                matches.append(card)
        if not matches and optional_missing_keys and row_key(row) in optional_missing_keys:
            continue
        if len(matches) != 1 or matches[0]["index"] in used:
            raise ValueError(f"Michelin membership is missing, duplicate or ambiguous for {row['Name']!r}.")
        card = matches[0]
        used.add(card["index"])
        associations[row_key(row)] = card
    if not allow_stale and len(used) != inventory["physical_count"]:
        raise ValueError("Michelin list contains stale or unidentified physical memberships.")
    return associations


class _MaintenanceDriver(GoogleMapsDriver):
    """Share one owned page across per-list production writers for this run."""

    _maintenance_started = False

    async def start(self) -> None:
        if not self._maintenance_started:
            await super().start()
            self._maintenance_started = True

    async def close(self, *, force: bool = False) -> None:
        try:
            await super().close(force=force)
        finally:
            self._maintenance_started = False


async def sync_and_verify(*, state_dir: Path, probe_dir: Path, endpoint: str) -> dict[str, Any]:
    """Use the production writer, then reconcile complete signed-in and anonymous inventories."""

    _assert_admitted(endpoint)
    acceptance, _, old_rows = validate_acceptance(state_dir)
    membership_path = Path(acceptance["membership_rows_file"]) if acceptance.get("membership_rows_file") else None
    membership_rows = _rows(membership_path) if membership_path else old_rows
    if ({row_key(row) for row in membership_rows} != {row_key(row) for row in old_rows}
            or (membership_path and acceptance["artifacts"].get(membership_path.name, {}).get("sha256") != _sha(membership_path))):
        raise ValueError("Accepted Michelin note membership bindings do not match source identities.")
    source = _read(probe_dir / "source-probe.json")
    source_path = Path(source["rows_file"])
    new_rows = _rows(source_path)
    if (source.get("source_id") != SOURCE_ID or source.get("complete") is not True
            or source.get("source_point_count") != len(new_rows) or source.get("rows_sha256") != _sha(source_path)
            or source.get("source_fingerprint") != semantic_fingerprint(new_rows)):
        raise ValueError("Michelin sync requires a complete verified source probe.")
    _write(state_dir / "pending.json", source)
    old_by_key = {row_key(row): row for row in membership_rows}
    new_by_key = {row_key(row): row for row in new_rows}
    # Retain already accepted notes on unchanged restaurants; description-only edits
    # are deliberately outside the meaningful business revision trigger.
    expected_rows = [old_by_key.get(key, row) for key, row in new_by_key.items()]
    aliases = _aliases(state_dir)
    driver = _MaintenanceDriver(GoogleMapsDriverConfig(
        user_data_dir=Path("/unused-cdp-profile"), headless=True, sync_delay_seconds=0.3, google_cdp_url=endpoint,
    ))
    ledger_path = state_dir / "maintenance-memberships.json"
    ledger = _read(ledger_path) if ledger_path.exists() else {}
    before: dict[str, Any] = {}
    required_place_ids: dict[str, set[str]] = {}
    already_confirmed: set[str] = set()
    try:
        await driver.start()
        await driver.open_maps_home()
        if not await driver.is_authenticated(refresh=False):
            raise ValueError("Google Maps sign-in is required in the admitted cron browser.")
        # Establish ownership of every prior physical row before writes/removal.
        for level, record in acceptance["lists"].items():
            if not await driver.open_list(record["name"]):
                raise ValueError(f"Existing Michelin task list is unavailable: {record['name']}")
            inventory = await read_list_inventory(await driver._require_page(), record["name"])
            level_rows = [row for row in membership_rows if row["level_slug"] == level]
            audit_memberships(level_rows, inventory, aliases, allow_stale=True, optional_missing_keys=set(old_by_key) - set(new_by_key))
            public = await read_anonymous_list_inventory(await driver._require_page(), record["name"])
            before[level] = audit_memberships(
                level_rows, public, aliases, allow_stale=True, optional_missing_keys=set(old_by_key) - set(new_by_key),
            )
            present = audit_memberships(
                [row for row in expected_rows if row["level_slug"] == level], public, aliases,
                allow_stale=True, optional_missing_keys=set(new_by_key),
            )
            already_confirmed.update(present)
        # Use each list's existing exact name and production writer/matcher.
        for level, record in acceptance["lists"].items():
            pending = [(key, row.copy()) for key, row in new_by_key.items()
                       if key not in old_by_key and key not in already_confirmed and row["level_slug"] == level]
            if not pending:
                continue
            for _key, row in pending:
                for correction in acceptance.get("explicit_verified_input_corrections", []):
                    original = next((old for old in old_rows if old["Name"] == correction["source_name"]), None)
                    if original and all(original.get(field) == row.get(field) for field in ("Name", "Address")):
                        row["NameLocal"] = correction["name_local"]
                        if correction.get("address"):
                            row["Address"] = correction["address"]
            writer = GoogleMapsSyncWriter(
                user_data_dir=Path("/unused-cdp-profile"), headless=True, sync_delay_seconds=0.3,
                max_save_retries=1, list_name_template=record["name"], list_name_prefix="",
                on_missing_list="stop", ignore_existing_lists_check=True,
                google_cdp_url=endpoint, source=SOURCE_MICHELIN, language="zh_TW", state_dir=str(state_dir), driver=driver,
            )
            await writer.initialize_run(scope_name="臺灣", level_slugs=(level,))
            for key, row in pending:
                result = await writer.sync_row(level, row)
                if result.failure:
                    raise ValueError(result.failure.reason)
                page = await driver._require_page()
                match = re.search(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", unquote(str(page.url)))
                if not match:
                    raise ValueError("Confirmed Michelin write has no physical place identity.")
                ledger[key] = {"level": level, "source_row": row, "place_id": match[1]}
                _write(ledger_path, ledger)
        # Every replacement must be present across all three lists before cleanup.
        for level, record in acceptance["lists"].items():
            if not await driver.open_list(record["name"]):
                raise ValueError("Michelin list disappeared before reconciliation.")
            inventory = await read_list_inventory(await driver._require_page(), record["name"])
            required_rows = [row for row in expected_rows if row["level_slug"] == level]
            audit_memberships(required_rows, inventory, aliases, allow_stale=True)
            public = await read_anonymous_list_inventory(await driver._require_page(), record["name"])
            required = audit_memberships(required_rows, public, aliases, allow_stale=True)
            required_place_ids[level] = {card["place_id"] for card in required.values()}
        for level, record in acceptance["lists"].items():
            stale = [card for key, card in before[level].items()
                     if key not in new_by_key and card["place_id"] not in required_place_ids[level]]
            if not stale:
                continue
            await driver.open_list(record["name"])
            page = await driver._require_page()
            for saved in stale:
                inventory = await read_list_inventory(page, record["name"])
                matches = [card for card in inventory["rows"] if card["name"] == saved["name"] and card["note"] == saved["note"]]
                if len(matches) != 1:
                    raise ValueError("Owned stale Michelin membership changed before removal.")
                card = page.locator(PLACE_CARD_SELECTOR).nth(matches[0]["index"])
                await card.hover()
                await card.locator("../..").get_by_role("button", name="Delete", exact=True).click()
                await page.wait_for_timeout(150)
        lists = {}
        evidence = state_dir / "acceptance"
        for level, record in acceptance["lists"].items():
            level_rows = [row for row in expected_rows if row["level_slug"] == level]
            await driver.open_maps_home()
            if not await driver.open_list(record["name"]):
                raise ValueError("Michelin list unavailable for final acceptance.")
            page = await driver._require_page()
            signed = await read_list_inventory(page, record["name"], expected_count=len(level_rows))
            signed_map = audit_memberships(level_rows, signed, aliases)
            public = await read_anonymous_list_inventory(page, record["name"], expected_count=len(level_rows))
            public_map = audit_memberships(level_rows, public, aliases)
            for key in signed_map:
                if (signed_map[key]["name"] != public_map[key]["name"]
                        or (signed_map[key].get("place_id") and signed_map[key]["place_id"] != public_map[key]["place_id"])
                        or (key in ledger and ledger[key].get("place_id") != public_map[key]["place_id"])):
                    raise ValueError("Signed-in and anonymous Michelin physical identities disagree.")
            if len({card["place_id"] for card in public_map.values()}) != len(public_map):
                raise ValueError("Different Michelin source rows resolve to duplicate physical memberships.")
            _write(evidence / f"daily-{level}-signed.json", signed)
            _write(evidence / f"daily-{level}-anonymous.json", public)
            lists[level] = dict(record, source_count=len(level_rows), signed_in_physical_count=len(level_rows),
                                anonymous_physical_count=len(level_rows), rendered_rows=len(level_rows))
        revision_path = evidence / "daily-source-rows.jsonl"
        revision_path.write_bytes(source_path.read_bytes())
        membership_path = evidence / "daily-membership-rows.jsonl"
        membership_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in expected_rows))
        artifacts = {name: proof for name, proof in acceptance["artifacts"].items() if not name.startswith("daily-")}
        for artifact in evidence.glob("daily-*"):
            artifacts[artifact.name] = {"sha256": _sha(artifact)}
        updated = dict(acceptance, completed_at_utc=datetime.now(UTC).isoformat(), lists=lists, artifacts=artifacts,
                       source={"url": source["source_url"], "total": len(new_rows), "bucket_counts": source["bucket_counts"]},
                       source_rows_file=str(revision_path.resolve()), membership_rows_file=str(membership_path.resolve()),
                       membership_audit={"source_rows": len(new_rows), "physical_memberships": len(new_rows),
                                         "missing": 0, "duplicates": 0, "unidentified": 0})
        _write(state_dir / "acceptance-manifest.json", updated)
        applied = applied_from_acceptance(state_dir)
        _write(state_dir / "applied.json", applied)
        (state_dir / "pending.json").unlink(missing_ok=True)
        return applied
    except BaseException as exc:
        debug_path = state_dir / "debug" / f"michelin-maintenance-failure-{datetime.now(UTC):%Y%m%dT%H%M%S}.html"
        try:
            await save_inventory_html(await driver._require_page(), debug_path)
        except Exception:
            pass
        _write(state_dir / "last-maintenance-failure.json", {
            "at_utc": datetime.now(UTC).isoformat(), "error": str(exc), "debug_path": str(debug_path),
            "source_applied": False, "failure_class": "required",
        })
        raise
    finally:
        await driver.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=DEFAULT_MICHELIN_STATE_DIR, help="Existing accepted Taiwan source/list state directory.")
    parser.add_argument("--probe-dir", help="Fresh source-only probe directory; sync discovers a fresh source when omitted.")
    parser.add_argument("--google-cdp-url", default=os.environ.get("BROWSER_CDP_URL", "http://127.0.0.1:9223"),
                        help="Existing cron browser; sync requires its live external-runner browser mutex and admission lease.")
    parser.add_argument("--mode", choices=MICHELIN_MAINTENANCE_MODES, default=MICHELIN_MAINTENANCE_MODES[0],
                        help=("bootstrap derives applied only from complete actual acceptance; "
                              "sync writes and fully verifies a changed revision; validate-applied prints verified metadata."))
    args = parser.parse_args()
    state_dir = Path(args.state_dir).expanduser().resolve()
    if args.mode == "validate-applied":
        path = state_dir / "applied.json"
        if not path.exists():
            print("null")
            return 0
        applied = applied_from_acceptance(state_dir)
        if _read(path) != applied:
            raise ValueError("Michelin applied metadata disagrees with complete accepted evidence.")
        print(json.dumps(applied))
        return 0
    with (state_dir / "maintenance.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.mode == "bootstrap":
            bootstrap_applied(state_dir)
            return 0
        if args.probe_dir:
            probe_dir = Path(args.probe_dir).resolve()
        else:
            import tempfile
            probe_dir = Path(tempfile.mkdtemp(prefix="michelin-maintenance-", dir=os.environ["TMPDIR"]))
            write_probe(probe_dir)
        applied = bootstrap_applied(state_dir)
        source = _read(probe_dir / "source-probe.json")
        if source.get("source_fingerprint") == applied["source_fingerprint"]:
            return 0
        asyncio.run(sync_and_verify(state_dir=state_dir, probe_dir=probe_dir, endpoint=args.google_cdp_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
