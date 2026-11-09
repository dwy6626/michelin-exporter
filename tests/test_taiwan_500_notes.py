"""Exact note cleanup and marker-free ownership using recorded real memberships and DOM."""

import copy
import importlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from playwright.async_api import async_playwright

from michelin_scraper.adapters.award_list_inventory import (
    attach_physical_ids,
    owned_source_key,
    remove_owned_stale_rows,
)
from michelin_scraper.application import taiwan_500_maintenance as maintenance
from michelin_scraper.application import taiwan_500_notes as notes
from michelin_scraper.application.taiwan_500_notes import (
    check_cleanup_inventory,
    cleanup_targets,
    edit_existing_note,
)
from michelin_scraper.sources.taiwan_500_awards import TAIWAN_500_AWARD_DEFINITIONS, _discover_award, dishes_public_note

source_fixtures = importlib.import_module("tests.test_taiwan_500_awards")
FIXTURES = source_fixtures.FIXTURES


class DishesNoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recording = json.loads((FIXTURES / "dishes-note-cleanup-2026-11-09.json").read_text())
        self.records = self.recording["records"]
        roster = _discover_award(TAIWAN_500_AWARD_DEFINITIONS[1], source_fixtures.Taiwan500AwardsTests().fetch)
        self.roster = replace(roster, parsed=replace(roster.parsed, rows=tuple(r["source_row"] for r in self.records)))
        self.ledger = {
            "created_by_task": True, "list_name": roster.document_name, "list_id": "<redacted-list-id>",
            "confirmed": {r["source_key"]: r["confirmed"] for r in self.records},
        }
        self.targets = {r["source_key"]: r["target"] for r in self.records}
        self.inventory = {"rows": [dict(r["public_card"], note=r["target"]["target_note"], source_markers=[]) for r in self.records]}

    def test_exact_three_fields_only_and_identity_explanation_preserved(self) -> None:
        for record in self.records:
            target = record["target"]
            self.assertEqual(dishes_public_note(target["original_note"]), target["target_note"])
            self.assertEqual(dishes_public_note(target["target_note"]), target["target_note"])
            self.assertEqual(maintenance.roster_note(self.roster, record["source_row"]), target["target_note"])
            self.assertEqual(
                [p for p in target["original_note"].split(" | ") if not p.startswith(("區域:", "官方:", "管理:"))],
                target["target_note"].split(" | "),
            )
        self.assertIn("定位查核:", self.records[1]["target"]["target_note"])
        bowls = _discover_award(TAIWAN_500_AWARD_DEFINITIONS[0], source_fixtures.Taiwan500AwardsTests().fetch)
        row = maintenance.managed_rows(bowls)[0]
        self.assertEqual(maintenance.roster_note_template(bowls), bowls.note_template + " | 定位查核: {定位查核} | 管理: {管理代碼}")
        self.assertIn("官方:", maintenance.roster_note(bowls, row))
        self.assertIn("管理:", maintenance.roster_note(bowls, row))

    def test_markerless_audit_retry_and_duplicate_unknown_conflict_rejection(self) -> None:
        audit = maintenance.audit_inventory(roster=self.roster, ledger=self.ledger, inventory=self.inventory)
        self.assertEqual(audit["physical_memberships"], len(self.records))
        maintenance.retain_inventory_retry_work(self.roster, self.ledger, self.inventory)
        self.assertEqual(self.ledger["retry_source_keys"], [])
        maintenance.retain_inventory_retry_work(self.roster, self.ledger, {"rows": self.inventory["rows"][:1]})
        self.assertEqual(self.ledger["retry_source_keys"], [self.records[1]["source_key"]])
        duplicate = {"rows": [self.inventory["rows"][0]] * 2}
        with self.assertRaisesRegex(ValueError, "duplicate physical"):
            maintenance.audit_inventory(roster=self.roster, ledger=self.ledger, inventory=duplicate)
        first = self.inventory["rows"][0]
        with self.assertRaisesRegex(ValueError, "Unknown"):
            owned_source_key(first, award="dishes", confirmed={})
        conflicting = dict(first, source_markers=[f"dishes/{self.records[1]['source_key']}"])
        with self.assertRaisesRegex(ValueError, "conflicts"):
            owned_source_key(conflicting, award="dishes", confirmed=self.ledger["confirmed"])
        same_place = dict(self.ledger["confirmed"], **{self.records[1]["source_key"]: self.records[0]["confirmed"]})
        with self.assertRaisesRegex(ValueError, "duplicate confirmed"):
            owned_source_key(first, award="dishes", confirmed=same_place)
        wrong_name = copy.deepcopy(self.inventory)
        wrong_name["rows"][0]["name"] = self.records[1]["public_card"]["name"]
        wrong_ledger = copy.deepcopy(self.ledger)
        wrong_ledger["confirmed"][self.records[0]["source_key"]]["candidate"] = self.records[1]["confirmed"]["candidate"]
        with self.assertRaisesRegex(ValueError, "restaurant name identity"):
            maintenance.audit_inventory(roster=self.roster, ledger=wrong_ledger, inventory=wrong_name)
        old_note = copy.deepcopy(self.inventory)
        old_note["rows"][0]["note"] = self.records[0]["target"]["original_note"]
        with self.assertRaisesRegex(ValueError, "note readback differs"):
            maintenance.audit_inventory(roster=self.roster, ledger=self.ledger, inventory=old_note)

    def test_exact_signed_public_join_rejects_missing_ambiguous_or_changed_notes(self) -> None:
        public = copy.deepcopy(self.inventory)
        signed = {"rows": [{k: v for k, v in row.items() if k != "place_id"} for row in public["rows"]]}
        attach_physical_ids(signed, public)
        self.assertEqual([r["place_id"] for r in signed["rows"]], [r["place_id"] for r in public["rows"]])
        with self.assertRaisesRegex(ValueError, "duplicate or ambiguous"):
            attach_physical_ids(signed, {"rows": [public["rows"][0]] * 2})
        with self.assertRaisesRegex(ValueError, "counts differ"):
            attach_physical_ids(signed, {"rows": public["rows"][:1]})
        signed["rows"][0]["note"] = self.records[0]["target"]["original_note"]
        with self.assertRaisesRegex(ValueError, "no unique exact"):
            attach_physical_ids(signed, public)

    def test_checkpoint_preserves_original_and_resumes_uncertain_real_write(self) -> None:
        targets = copy.deepcopy(self.targets)
        progress = {"source_fingerprint": self.roster.source_fingerprint, "list_id": self.ledger["list_id"], "targets": targets}
        self.assertEqual(cleanup_targets(self.roster, self.ledger, progress), targets)
        self.assertEqual(targets[self.records[0]["source_key"]]["original_note"], self.records[0]["target"]["original_note"])
        cards = check_cleanup_inventory(self.roster, self.ledger, self.inventory, targets)
        self.assertEqual(set(cards), set(targets))
        self.ledger["confirmed"][self.records[1]["source_key"]]["note"] = self.records[1]["target"]["target_note"]
        self.assertEqual(cleanup_targets(self.roster, self.ledger, progress), targets)
        changed = copy.deepcopy(self.inventory)
        changed["rows"][0]["note"] = self.records[1]["target"]["target_note"]
        with self.assertRaisesRegex(ValueError, "unexpected note"):
            check_cleanup_inventory(self.roster, self.ledger, changed, targets)
        progress["list_id"] = "<redacted-other-list-id>"
        with self.assertRaisesRegex(ValueError, "different source or list"):
            cleanup_targets(self.roster, self.ledger, progress)


class RecordedNoteDOMTests(unittest.IsolatedAsyncioTestCase):
    async def test_recorded_challenge_stops_before_notes_and_retains_failure_evidence(self) -> None:
        data = DishesNoteTests()
        data.setUp()
        data.ledger["list_url"] = "https://www.google.com/maps/data=!11m2!2s<redacted-list-id>"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dishes").mkdir()
            (root / "dishes/memberships.json").write_text(json.dumps(data.ledger))
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(root / "profile", channel="chrome", headless=True)
                try:
                    page = await context.new_page()
                    await page.set_content((FIXTURES / "dishes-note-cleanup-challenge-2026-11-09.html").read_text())
                    driver = MagicMock(
                        start=AsyncMock(), close=AsyncMock(), open_list=AsyncMock(return_value=False),
                        _require_page=AsyncMock(return_value=page),
                    )
                    with (patch.object(notes, "accepted_dishes_roster", return_value=data.roster),
                          patch.object(notes, "_assert_browser_lane"),
                          patch.object(notes, "AwardMapsDriver", return_value=driver),
                          patch.object(notes, "edit_existing_note", new=AsyncMock()) as edit,
                          patch.object(notes, "read_list_inventory", new=AsyncMock()) as read):
                        with self.assertRaisesRegex(ValueError, "Google unusual-traffic challenge"):
                            await notes.clean_dishes_notes(
                                state_dir=root, endpoint=maintenance.CRON_CDP_URL, evidence_dir=root / "evidence",
                            )
                        edit.assert_not_awaited()
                        read.assert_not_awaited()
                    failure = json.loads((root / "evidence/failure.json").read_text())
                    self.assertEqual(failure["writes_this_run"], 0)
                    self.assertIn("Our systems have detected unusual traffic", (root / "evidence/failure.html").read_text())
                    self.assertEqual(json.loads((root / "dishes/memberships.json").read_text()), data.ledger)
                    driver.close.assert_awaited_once()
                finally:
                    await context.close()

    async def test_partial_batch_uses_only_boundary_readbacks_and_reconciles_inflight(self) -> None:
        data = DishesNoteTests()
        data.setUp()
        data.ledger["list_url"] = "https://www.google.com/maps/data=!11m2!2s<redacted-list-id>"
        original = {"rows": [dict(r["public_card"], note=r["target"]["original_note"]) for r in data.records]}
        changed = copy.deepcopy(original)
        changed["rows"][0]["note"] = data.records[0]["target"]["target_note"]
        def signed(inventory):
            return copy.deepcopy(inventory)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dishes").mkdir()
            (root / "dishes/memberships.json").write_text(json.dumps(data.ledger))
            progress = {"source_fingerprint": data.roster.source_fingerprint, "list_id": data.ledger["list_id"],
                        "targets": data.targets, "first_save_verified": {"memberships_unchanged": True},
                        "in_flight": {"source_key": data.records[0]["source_key"]}}
            (root / "dishes/note-cleanup.json").write_text(json.dumps(progress))
            page = MagicMock(add_init_script=AsyncMock(),
                                 locator=MagicMock(return_value=MagicMock(inner_text=AsyncMock(return_value=""))))
            driver = MagicMock(start=AsyncMock(), close=AsyncMock(), open_list=AsyncMock(return_value=True),
                               is_authenticated=AsyncMock(return_value=True), _require_page=AsyncMock(return_value=page))
            with (patch.object(notes, "accepted_dishes_roster", return_value=data.roster),
                  patch.object(notes, "_assert_browser_lane"), patch.object(notes, "AwardMapsDriver", return_value=driver),
                  patch.object(notes, "save_inventory_html", new=AsyncMock()),
                  patch.object(notes, "read_list_metadata", new=AsyncMock(return_value={"sharing": "shared"})),
                  patch.object(notes, "read_list_inventory", new=AsyncMock(side_effect=[signed(original), signed(changed)])) as read,
                  patch.object(notes, "read_anonymous_list_inventory", new=AsyncMock(side_effect=[original, changed])) as public,
                  patch.object(notes, "edit_existing_note", new=AsyncMock(return_value={"status": 200})) as edit):
                result = await notes.clean_dishes_notes(state_dir=root, endpoint=maintenance.CRON_CDP_URL,
                                                       evidence_dir=root / "evidence", note_limit=1)
                self.assertEqual((result["status"], result["verified"], result["pending"], result["writes_this_run"]), ("partial", 1, 1, 1))
                self.assertEqual((read.await_count, public.await_count, driver.open_list.await_count), (2, 2, 2))
                edit.assert_awaited_once()
                # Restart sees the uncertain saved row first and writes only the
                # other mismatch. An exception retains that new uncertain row.
                driver.open_list.reset_mock()
                read.side_effect = [signed(changed)]
                public.side_effect = [changed]
                edit.side_effect = RuntimeError("interrupted before batch close")
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    await notes.clean_dishes_notes(state_dir=root, endpoint=maintenance.CRON_CDP_URL,
                                                   evidence_dir=root / "retry", note_limit=1)
                self.assertEqual(edit.call_args.args[1]["place_id"], data.records[1]["target"]["place_id"])
                current = json.loads((root / "dishes/note-cleanup.json").read_text())
                self.assertEqual(current["in_flight"]["source_key"], data.records[1]["source_key"])
                self.assertEqual(current["verified"], 1)

    async def test_real_317_row_budget_and_complete_physical_readback(self) -> None:
        recording = json.loads((FIXTURES / "dishes-note-recovery-physical-2026-11-09.json").read_text())
        base = _discover_award(TAIWAN_500_AWARD_DEFINITIONS[1], source_fixtures.Taiwan500AwardsTests().fetch)
        roster = base
        dishes = recording["baseline"]["dishes"]
        metadata = recording["baseline"]["list_metadata"]
        for initially_clean in (False, True):
            with self.subTest(initially_clean=initially_clean), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                confirmed = copy.deepcopy(recording["confirmed"])
                live = {key: recording["targets"][key]["target_note" if initially_clean else "original_note"] for key in confirmed}
                ledger = {"created_by_task": True, "list_name": roster.document_name, "list_id": "<redacted-dishes-list-id>",
                          "list_url": dishes["url"], "confirmed": confirmed,
                          "verified_resolutions": recording["verified_resolutions"]}
                (root / "dishes").mkdir()
                (root / "dishes/memberships.json").write_text(json.dumps(ledger))
                (root / "user-skips.json").write_text(json.dumps(recording["user_skip_policy"]))
                (root / "dishes/note-cleanup.json").write_text(json.dumps({
                    "source_fingerprint": roster.source_fingerprint, "list_id": ledger["list_id"],
                    "targets": recording["targets"], "first_save_verified": {"memberships_unchanged": True},
                }))
                (root / "bowls").mkdir()
                bowls = recording["baseline"]["bowls"]
                (root / "bowls/memberships.json").write_text(json.dumps({
                    "confirmed": {str(i): {"place_id": place_id} for i, place_id in enumerate(bowls["notes"])},
                }))
                (root / "baseline.json").write_text(json.dumps(recording["baseline"]))
                active = [roster.document_name]
                page = MagicMock(add_init_script=AsyncMock(),
                                 locator=MagicMock(return_value=MagicMock(inner_text=AsyncMock(return_value=""))))

                async def open_list(name, active=active):
                    active[0] = name
                    return True

                async def read(page, name, confirmed=confirmed, live=live, bowls=bowls, **kwargs):
                    if name == roster.document_name:
                        rows = [{"name": saved["candidate"]["name"], "note": live[key], "text": live[key], "source_markers": [],
                                 "place_id": saved["place_id"], "index": i} for i, (key, saved) in enumerate(confirmed.items())]
                    elif name == bowls["name"]:
                        rows = [{"name": recording["bowls_names"][place_id], "note": note, "place_id": place_id}
                                for place_id, note in bowls["notes"].items()]
                    else:
                        item = next(item for item in metadata if item["name"] == name)
                        rows = [{"name": name, "note": note, "place_id": name} for name, note in item["notes_by_name"].items()]
                    return {"rows": rows}

                async def edit(page, card, target, confirmed=confirmed, live=live):
                    key = next(key for key, value in confirmed.items() if value["place_id"] == card["place_id"])
                    live[key] = target
                    return {"status": 200}

                driver = MagicMock(start=AsyncMock(), close=AsyncMock(), open_list=AsyncMock(side_effect=open_list),
                                   is_authenticated=AsyncMock(return_value=True), _require_page=AsyncMock(return_value=page))
                with (patch.object(notes, "accepted_dishes_roster", return_value=roster), patch.object(notes, "_assert_browser_lane"),
                      patch.object(notes, "AwardMapsDriver", return_value=driver),
                      patch.object(notes, "save_inventory_html", new=AsyncMock()),
                      patch.object(notes, "read_list_metadata", new=AsyncMock(side_effect=lambda page, active=active: next(
                          item["metadata"] for item in metadata if item["name"] == active[0]))),
                      patch.object(notes, "read_list_inventory", new=AsyncMock(side_effect=read)) as signed,
                      patch.object(notes, "read_anonymous_list_inventory", new=AsyncMock(side_effect=read)) as anonymous,
                      patch.object(notes, "edit_existing_note", new=AsyncMock(side_effect=edit)) as writer):
                    receipt = await notes.clean_dishes_notes(state_dir=root, endpoint=maintenance.CRON_CDP_URL,
                                                           evidence_dir=root / "evidence", note_limit=99,
                                                           baseline_path=root / "baseline.json")
                    if initially_clean:
                        self.assertEqual((receipt["status"], receipt["verified"], receipt["pending"]), ("completed", 317, 0))
                        self.assertTrue(receipt["completion_evidence"]["bowls_unchanged"])
                        self.assertTrue(receipt["completion_evidence"]["icons_sharing_unchanged"])
                        writer.assert_not_awaited()
                    else:
                        self.assertEqual((receipt["status"], receipt["verified"], receipt["pending"]), ("partial", 25, 292))
                        self.assertEqual((writer.await_count, signed.await_count, anonymous.await_count), (25, 2, 2))

    async def test_recorded_default_icon_and_sharing_metadata(self) -> None:
        async with async_playwright() as playwright:
            with tempfile.TemporaryDirectory() as profile:
                context = await playwright.chromium.launch_persistent_context(profile, channel="chrome", headless=True)
                try:
                    page = await context.new_page()
                    await page.set_content((FIXTURES / "list-default-icon-header-2026-11-09.html").read_text())
                    self.assertEqual(await notes.read_list_metadata(page),
                                     {"icon": {"kind": "symbol", "text": "\ue1d3"}, "sharing": "shared"})
                finally:
                    await context.close()

    async def test_recorded_dom_uses_input_blur_and_markerless_stale_ownership(self) -> None:
        record = json.loads((FIXTURES / "dishes-note-cleanup-2026-11-09.json").read_text())["records"][0]
        async with async_playwright() as playwright:
            with tempfile.TemporaryDirectory() as profile:
                context = await playwright.chromium.launch_persistent_context(profile, channel="chrome", headless=True)
                try:
                    page = await context.new_page()
                    await page.route("https://www.google.com/maps/", lambda route: route.fulfill(
                        body=(FIXTURES / "dishes-note-cleanup-2026-11-09.html").read_text(), content_type="text/html",
                    ))
                    await page.goto("https://www.google.com/maps/")
                    # Live textarea properties are recorded separately from empty HTML defaults.
                    await page.get_by_role("textbox", name="Note", exact=True).fill(record["target"]["original_note"])
                    card = dict(record["signed_card"], index=0, note=record["target"]["original_note"])
                    # The endpoint/status are from the real one-row save probe;
                    # replay its acknowledgment without contacting Google.
                    await page.route("https://www.google.com/maps/preview/entitylist/updateitem",
                                     lambda route: route.fulfill(status=200, body=""))
                    await page.evaluate("""() => {
                        window.observedEvents = [];
                        const editor = document.querySelector('textarea');
                        for (const event of ['input', 'keyup', 'change', 'blur']) {
                            editor.addEventListener(event, () => window.observedEvents.push(event));
                        }
                        editor.addEventListener('blur', () => fetch(
                            'https://www.google.com/maps/preview/entitylist/updateitem', {method: 'POST', mode: 'no-cors'}));
                    }""")
                    clock = [100.0]
                    intervals = []

                    async def advance(seconds):
                        intervals.append(seconds)
                        clock[0] += seconds

                    with (patch.object(notes, "monotonic", side_effect=lambda: clock[0]),
                          patch.object(notes, "sleep", side_effect=advance)):
                        await edit_existing_note(page, card, record["target"]["target_note"])
                        await page.locator("textarea").fill(card["note"])
                        await edit_existing_note(page, card, record["target"]["target_note"])
                        self.assertEqual(intervals, [3.0])
                    self.assertEqual(await page.locator("textarea").input_value(), record["target"]["target_note"])
                    observed = await page.evaluate("window.observedEvents")
                    self.assertIn("input", observed)
                    self.assertIn("keyup", observed)
                    self.assertIn("change", observed)
                    self.assertEqual(observed[-1], "blur")
                    with self.assertRaisesRegex(ValueError, "changed immediately"):
                        await edit_existing_note(page, card, record["target"]["target_note"])
                    inventory = {"rows": [dict(card, note=record["target"]["target_note"], source_markers=[])]}
                    driver = MagicMock(open_list=AsyncMock(return_value=True), _require_page=AsyncMock(return_value=page))
                    await page.evaluate("""() => {
                        document.querySelector('button[aria-label="Delete"]').addEventListener('click',
                            () => window.observedEvents.push('delete'));
                    }""")
                    with patch("michelin_scraper.adapters.award_list_inventory.read_list_inventory",
                               new=AsyncMock(return_value=inventory)):
                        removed = await remove_owned_stale_rows(
                            driver, list_name="500盤2026 I 第六屆500盤電子地圖", award="dishes",
                            current_keys=set(), owned_keys={record["source_key"]},
                            confirmed={record["source_key"]: record["confirmed"]},
                        )
                    self.assertEqual(removed, [record["source_key"]])
                    self.assertEqual((await page.evaluate("window.observedEvents"))[-1], "delete")
                    await page.locator("textarea").fill(card["note"])
                    await page.unroute("https://www.google.com/maps/preview/entitylist/updateitem")
                    await page.route("https://www.google.com/maps/preview/entitylist/updateitem", lambda route: route.fulfill(
                        status=429, headers={"Retry-After": "7200"}, body=""))
                    with self.assertRaises(notes.NoteRecoveryError) as caught:
                        await edit_existing_note(page, card, record["target"]["target_note"])
                    self.assertEqual((caught.exception.status, caught.exception.retry_after_seconds), ("challenge", 7200))

                finally:
                    await context.close()


if __name__ == "__main__":
    unittest.main()
