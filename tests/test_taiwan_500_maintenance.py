"""Safety and independent inventory regressions using recorded real award data."""

import copy
import fcntl
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from bs4 import BeautifulSoup

from michelin_scraper.adapters.award_list_inventory import (
    MEMBERSHIP_MARKER_PATTERN,
    PLACE_CARD_SELECTOR,
    VIEW_MENU_NAME,
    parse_public_list_inventory,
    remove_owned_stale_rows,
    sanitize_inventory_html,
)
from michelin_scraper.application import taiwan_500_maintenance as maintenance
from michelin_scraper.application.place_matcher import PlaceCandidate, assess_place_match
from michelin_scraper.application.place_query_builder import build_place_query_attempts
from michelin_scraper.application.row_identity import build_row_identity_key
from michelin_scraper.sources.taiwan_500_awards import TAIWAN_500_AWARD_DEFINITIONS, _discover_award
from tests import test_taiwan_500_awards as source_fixtures

FIXTURES = source_fixtures.FIXTURES


class AwardInventoryTests(unittest.TestCase):
    def test_recorded_review_accounts_are_redacted_without_changing_place_identity(self) -> None:
        path = Path(__file__).parent / "fixtures/google_maps/search-stale-panel-2026-11-08.html"
        soup = BeautifulSoup(path.read_text(), "html.parser")
        title = soup.select_one("h1.DUwDvf").get_text()
        author = soup.select_one(".d4r55")
        author.string = "fixture-review-account"
        card = author.find_parent(class_="jftiEf")
        for node in card.select("[aria-label]"):
            node["aria-label"] = str(node["aria-label"]).replace("<redacted-account-name>", "fixture-review-account")
        redacted = sanitize_inventory_html(str(soup))
        self.assertNotIn("fixture-review-account", redacted)
        result = BeautifulSoup(redacted, "html.parser")
        self.assertEqual(result.select_one("h1.DUwDvf").get_text(), title)
        self.assertTrue(all(node.get_text() == "<redacted-account-name>" for node in result.select(".d4r55")))
        streamed = Path(__file__).parent / "fixtures/google_maps/home-streamed-2026-11-08.html"
        self.assertFalse(BeautifulSoup(sanitize_inventory_html(streamed.read_text()), "html.parser").select("script"))

    def test_recorded_award_name_identity_rejects_unrelated_cuisine_overlap(self) -> None:
        records = json.loads((FIXTURES / "award-name-identity-2026-11-08.json").read_text())
        for record in records:
            with self.subTest(name=record["source_row"]["Name"]):
                self.assertEqual(
                    maintenance._has_award_name_identity(record["source_row"], PlaceCandidate(**record["candidate"])),
                    record["expected_name_identity"],
                )

    def setUp(self) -> None:
        self.payload = json.loads((FIXTURES / "public-list-inventory-2026-11-08.json").read_text())["payload"]
        self.list_name = self.payload[0][4]

    def test_complete_recorded_anonymous_inventory_and_unsigned_physical_ids(self) -> None:
        result = parse_public_list_inventory(
            self.payload, list_name="500碗2026 I 第四屆500碗電子地圖",
            list_id="<redacted-list-id>", expected_count=50,
        )
        self.assertEqual(result["physical_count"], 50)
        self.assertFalse(result["credentials_sent"])
        self.assertTrue(all(len(row["source_markers"]) == 1 for row in result["rows"]))
        for row, entry in zip(result["rows"], self.payload[0][8], strict=True):
            halves = [int(part, 16) for part in row["place_id"].split(":")]
            self.assertEqual(halves, [int(part) % (1 << 64) for part in entry[1][6]])

    def test_wrong_list_partial_response_and_wrong_expected_count_fail(self) -> None:
        with self.assertRaisesRegex(ValueError, "different list"):
            parse_public_list_inventory(self.payload, list_name=self.list_name, list_id="wrong")
        partial = copy.deepcopy(self.payload)
        partial[0][8].pop()
        with self.assertRaisesRegex(ValueError, "partial"):
            parse_public_list_inventory(partial, list_name=self.list_name, list_id="<redacted-list-id>")
        with self.assertRaisesRegex(ValueError, "count mismatch"):
            parse_public_list_inventory(self.payload, list_name=self.list_name, list_id="<redacted-list-id>", expected_count=472)

    def test_recorded_saved_list_controls_and_note_ownership_contract(self) -> None:
        soup = BeautifulSoup((FIXTURES / "saved-list-one-row-2026-11-08.html").read_text(), "html.parser")
        cards = soup.select(PLACE_CARD_SELECTOR)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].select_one(".fontHeadlineSmall").get_text(), "許仔豬腳麵線")
        container = cards[0].parent.parent
        self.assertEqual(len(container.select('button[aria-label="Delete"]')), 1)
        markers = MEMBERSHIP_MARKER_PATTERN.findall(container.select_one("textarea").get_text())
        self.assertEqual(markers, [("bowls", "c2c03cf84e77650bdb914b3214cccfad63190c0065f8fc3774642072b3d512dd")])
        self.assertIn("Shared · 1 place", soup.select_one("h2").get_text())

    def test_place_identity_uses_active_restaurant_not_building_context(self) -> None:
        # Both identities occur in the captured Pizza Americano Maps URL.
        fixture = self.payload[0][8]
        entry = next(entry for entry in fixture if entry[2] == "Pizza Americano 老美披薩")
        expected = ":".join(hex(int(part) % (1 << 64)) for part in entry[1][6])
        url = json.loads((FIXTURES / "place-url-with-building-context-2026-11-08.json").read_text())["place_url"]
        self.assertEqual(maintenance._place_id(url), expected)
        self.assertEqual(maintenance._place_id("https://www.google.com/maps?hl=en"), "")

    def test_recorded_view_sharing_flow_excludes_edit_invite(self) -> None:
        menu = BeautifulSoup((FIXTURES / "share-view-menu-2026-11-08.html").read_text(), "html.parser")
        for decorative in menu.select('[aria-hidden="true"]'):
            decorative.decompose()
        choices = menu.select('[role="menuitemradio"]')
        view = [choice for choice in choices if VIEW_MENU_NAME.search(choice.get_text(" ", strip=True))]
        self.assertEqual(len(view), 1)
        self.assertNotIn("Invite to edit", view[0].get_text())
        for filename, text in (("share-view-prompt", "Get link to share"), ("share-view-confirmation", "Get link")):
            soup = BeautifulSoup((FIXTURES / f"{filename}-2026-11-08.html").read_text(), "html.parser")
            self.assertIn(text, soup.select_one('[role="dialog"]').get_text(" ", strip=True))
        ready = BeautifulSoup((FIXTURES / "share-view-link-2026-11-08.html").read_text(), "html.parser")
        self.assertRegex(ready.select_one('input')["value"], r"^https://maps\.app\.goo\.gl/[A-Za-z0-9]+$")

    def test_recorded_verified_identity_probes_keep_normal_matcher_vetoes(self) -> None:
        records = json.loads((FIXTURES / "verified-place-probes-2026-11-08.json").read_text())["records"]
        for record in records:
            row = copy.deepcopy(record["source_row"])
            evidence = record["verified_evidence"]
            row["Address"] = evidence.get("match_address", row["Address"])
            row["Aliases"] = evidence.get("aliases", ())
            with self.subTest(name=row["Name"]):
                result = assess_place_match(row, PlaceCandidate(**record["candidate"]))
                self.assertEqual(result.strength, record.get("observed_strength", "strong"))
                self.assertFalse(result.hard_veto)
                self.assertFalse(result.house_number_conflict)
                required = tuple(evidence.get("required_names", ()))
                self.assertTrue(maintenance._has_required_venue_name(required, PlaceCandidate(**record["candidate"])))
                if containing := record.get("rejected_containing_building_candidate"):
                    self.assertFalse(maintenance._has_required_venue_name(required, PlaceCandidate(**containing)))
                if non_food := record.get("rejected_non_food_candidate"):
                    self.assertTrue(assess_place_match(row, PlaceCandidate(**non_food)).hard_veto)


class AwardMaintenanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_uses_replacement_owned_page_after_home_recovery(self) -> None:
        record = json.loads((FIXTURES / "note-unverified-saved-panel-2026-11-08.metadata.json").read_text())["current_place"]
        driver = maintenance.AwardMapsDriver(maintenance.GoogleMapsDriverConfig(
            user_data_dir=self.state, headless=True, sync_delay_seconds=0.1,
        ))
        preceding, replacement = MagicMock(), MagicMock()
        preceding.is_closed.return_value = replacement.is_closed.return_value = False
        driver._context = MagicMock()
        driver._page = preceding

        async def submitted(**_kwargs: object) -> str:
            driver._page = replacement
            return driver._SEARCH_OUTCOME_RESULT_READY

        with (patch.object(driver, "_submit_maps_search_query", AsyncMock(side_effect=submitted)),
              patch.object(driver, "_try_open_result_by_index", AsyncMock(return_value=True)) as opened,
              patch.object(driver, "_is_add_place_input_visible", AsyncMock(return_value=False)),
              patch.object(driver, "_extract_current_place_candidate",
                           AsyncMock(return_value=PlaceCandidate(**record["candidate"]))) as extracted,
              patch.object(driver, "_cache_page_html_snapshot", AsyncMock())):
            self.assertIsNotNone(await driver.search_and_open_first_result(record["candidate"]["name"]))
        self.assertIs(opened.call_args.kwargs["page"], replacement)
        extracted.assert_awaited_once_with(replacement)

    async def test_unverified_note_cannot_reuse_preceding_confirmation(self) -> None:
        record = json.loads((FIXTURES / "note-unverified-saved-panel-2026-11-08.metadata.json").read_text())
        html = BeautifulSoup((FIXTURES / record["html_fixture"]).read_text(), "html.parser")
        current = record["current_place"]
        self.assertEqual(html.select_one("h1.DUwDvf").get_text(), current["candidate"]["name"])
        self.assertTrue(html.select('[aria-label="Show place lists details"]'))
        self.assertFalse(html.select("textarea"))
        driver = maintenance.AwardMapsDriver(maintenance.GoogleMapsDriverConfig(
            user_data_dir=self.state, headless=True, sync_delay_seconds=0.1,
        ))
        page = MagicMock(url=current["place_url"])
        for place_saved in (False, True):
            driver.last_confirmed_save = record["preceding_confirmation"]
            with (self.subTest(place_saved=place_saved),
                  patch.object(driver, "_require_page", AsyncMock(return_value=page)),
                  patch.object(driver, "_extract_current_place_candidate",
                               AsyncMock(return_value=PlaceCandidate(**current["candidate"]))),
                  patch.object(maintenance.GoogleMapsDriver, "save_current_place_to_list",
                               AsyncMock(side_effect=maintenance.GoogleMapsNoteWriteError(record["failure"], place_saved=place_saved)))):
                with self.assertRaises(maintenance.GoogleMapsTransientError):
                    await driver.save_current_place_to_list(current["list_name"], current["note"])
            self.assertIsNone(driver.last_confirmed_save)

    async def test_evidenced_restaurant_filter_rejects_containing_hotel(self) -> None:
        records = json.loads((FIXTURES / "verified-place-probes-2026-11-08.json").read_text())["records"]
        record = next(item for item in records if item.get("rejected_containing_building_candidate"))
        required = tuple(record["verified_evidence"]["required_names"])
        hotel = PlaceCandidate(**record["rejected_containing_building_candidate"])
        restaurant = PlaceCandidate(**record["candidate"])
        driver = maintenance.AwardMapsDriver(maintenance.GoogleMapsDriverConfig(
            user_data_dir=self.state, headless=True, sync_delay_seconds=0.1,
        ))
        query = build_place_query_attempts(record["source_row"])[0]
        driver.required_names_by_query = {query: required}
        accepted = []

        def normal_match(candidate: PlaceCandidate) -> bool:
            accepted.append(candidate)
            return assess_place_match(record["source_row"], candidate).strength != "weak"

        async def observed_candidates(_query: str, accept: object, **_kwargs: object) -> PlaceCandidate:
            self.assertFalse(accept(hotel))
            self.assertTrue(accept(restaurant))
            return restaurant

        with patch.object(maintenance.GoogleMapsDriver, "search_and_open_first_acceptable_result",
                          side_effect=observed_candidates):
            self.assertEqual(await driver.search_and_open_first_acceptable_result(query, normal_match), restaurant)
        self.assertEqual(accepted, [restaurant])

    async def test_stalled_result_reloads_only_the_recorded_physical_place(self) -> None:
        stalled = BeautifulSoup((FIXTURES / "stalled-place-result-2026-11-08.html").read_text(), "html.parser")
        loaded = BeautifulSoup((FIXTURES / "reloaded-place-result-2026-11-08.html").read_text(), "html.parser")
        result_url = str(stalled.select_one("a.hfpxzc")["href"])
        title = loaded.select_one("h1.DUwDvf").get_text(strip=True)
        save = loaded.select_one('button[aria-label="Save"]')
        self.assertIsNotNone(save)
        page = MagicMock()
        page.url = "https://www.google.com/maps/search/魏家婆菜/"

        async def navigate(url: str, *, wait_until: str) -> None:
            self.assertEqual(wait_until, "commit")
            page.url = url

        page.goto = AsyncMock(side_effect=navigate)
        result = MagicMock()
        result.count = AsyncMock(return_value=1)
        result.get_attribute = AsyncMock(return_value=result_url)
        driver = maintenance.AwardMapsDriver(maintenance.GoogleMapsDriverConfig(
            user_data_dir=self.state, headless=True, sync_delay_seconds=0.1,
        ))
        waits = 0

        async def wait(**kwargs: object) -> bool:
            nonlocal waits
            waits += 1
            if waits == 1:
                return False
            return await kwargs["predicate"]()

        with (patch.object(driver, "_matching_locator_at", AsyncMock(return_value=result)),
              patch.object(driver, "_supports_dom_waits", return_value=True),
              patch.object(driver, "_extract_text", AsyncMock(side_effect=["", title])),
              patch.object(driver, "_resolve_save_button", AsyncMock(return_value=save)),
              patch.object(driver, "_click_locator", AsyncMock()),
              patch.object(driver, "_wait_for_timeout", AsyncMock()),
              patch.object(driver, "_wait_for_condition", side_effect=wait)):
            self.assertTrue(await driver._try_open_result_by_index(page=page, query="魏家婆菜", result_index=0))
        page.goto.assert_awaited_once_with(result_url, wait_until="commit")

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name)
        self.rosters = tuple(_discover_award(d, source_fixtures.Taiwan500AwardsTests().fetch) for d in TAIWAN_500_AWARD_DEFINITIONS)

    def skip_policy(self) -> dict:
        return json.loads((FIXTURES / "user-authorized-skips-2026-11-08.json").read_text())

    def test_exact_user_skips_preserve_complete_official_rows_and_fingerprint(self) -> None:
        roster = self.rosters[1]
        policy = self.skip_policy()
        original = copy.deepcopy(roster.parsed.rows)
        rows = maintenance.managed_rows(roster, user_skip_policy=policy)
        self.assertEqual(len(rows), 317)
        self.assertEqual({d["source_name"] for d in maintenance.user_skip_decisions(roster, policy)}, {"COAST", "nabo ulv"})
        self.assertFalse({"COAST", "nabo ulv"} & {row["Name"] for row in rows})
        self.assertEqual(roster.parsed.rows, original)
        self.assertEqual(roster.source_point_count, 319)
        self.assertEqual(roster.source_fingerprint, policy["decisions"][0]["source_fingerprint"])
        self.assertEqual(len(maintenance.managed_rows(self.rosters[0], user_skip_policy=policy)), 472)
        self.assertEqual(len(maintenance.managed_rows(replace(roster, edition=2027), user_skip_policy=policy)), 319)
        with self.assertRaisesRegex(ValueError, "renewed approval"):
            maintenance.managed_rows(replace(roster, source_fingerprint=self.rosters[0].source_fingerprint), user_skip_policy=policy)
        policy["decisions"][0]["original_row"]["Name"] = "SAAKA"
        with self.assertRaisesRegex(ValueError, "renewed approval"):
            maintenance.managed_rows(roster, user_skip_policy=policy)

    async def test_skip_restart_reuses_all_confirmed_notes_without_reimport(self) -> None:
        roster = self.rosters[1]
        policy = self.skip_policy()
        maintenance._write_json(self.state / "user-skips.json", policy)
        rows = maintenance.managed_rows(roster, user_skip_policy=policy)
        confirmed = {maintenance.source_row_key(row): {"note": maintenance.roster_note(roster, row)} for row in rows}
        maintenance._write_json(self.state / "dishes/memberships.json", {
            "created_by_task": True, "list_name": roster.document_name, "confirmed": confirmed,
        })
        writer = MagicMock()
        writer.initialize_run = AsyncMock()
        writer.finalize_run = AsyncMock()
        writer.sync_rows_by_level = AsyncMock()
        writer.list_created_by_level = {}
        with (patch.object(maintenance, "AwardMapsDriver", return_value=MagicMock()),
              patch.object(maintenance, "GoogleMapsSyncWriter", return_value=writer)):
            ledger = await maintenance.sync_award_rows(roster=roster, endpoint=maintenance.CRON_CDP_URL, state_dir=self.state)
        writer.sync_rows_by_level.assert_not_awaited()
        self.assertEqual(ledger["confirmed"], confirmed)
        self.assertEqual(len(ledger["user_skip_policy"]["decisions"]), 2)
        # Every other official row remains required, even when the known skips are present.
        inventory = {"rows": []}
        maintenance.retain_inventory_retry_work(roster, ledger, inventory)
        self.assertEqual(set(ledger["retry_source_keys"]), set(confirmed))
        with self.assertRaisesRegex(ValueError, "missing 317 official rows"):
            maintenance.audit_inventory(roster=roster, ledger=ledger, inventory=inventory)

    async def test_verify_sharing_guard_stops_before_any_sharing_control(self) -> None:
        from michelin_scraper.adapters.award_list_inventory import ensure_viewer_link

        # Recorded real pre-sharing dialog; verification must not click its action.
        html = BeautifulSoup((FIXTURES / "share-view-prompt-2026-11-08.html").read_text(), "html.parser")
        self.assertIn("Get link to share", html.get_text(" ", strip=True))
        page = MagicMock()
        page.locator.return_value.filter.return_value.count = AsyncMock(return_value=0)
        driver = MagicMock()
        driver.open_list = AsyncMock(return_value=True)
        driver._require_page = AsyncMock(return_value=page)
        with self.assertRaisesRegex(ValueError, "refusing to enable"):
            await ensure_viewer_link(driver, self.rosters[1].document_name, self.state / "dialog.html", allow_sharing_changes=False)
        page.get_by_role.assert_not_called()

    async def test_readonly_viewer_link_resolves_to_exact_recorded_list_without_menu(self) -> None:
        from michelin_scraper.adapters import award_list_inventory as inventory

        records = json.loads((FIXTURES / "viewer-link-resolutions-2026-11-08.json").read_text())
        page = MagicMock(url=records[0]["resolved_url"])
        page.locator.return_value.filter.return_value.count = AsyncMock(return_value=1)
        driver = MagicMock()
        driver.open_list = AsyncMock(return_value=True)
        driver._require_page = AsyncMock(return_value=page)
        response = MagicMock(status=200, url=records[0]["resolved_url"])
        response.__enter__.return_value = response
        with patch.object(inventory, "urlopen", return_value=response):
            url = await inventory.ensure_viewer_link(
                driver, self.rosters[0].document_name, self.state / "dialog.html",
                allow_sharing_changes=False, existing_viewer_url=records[0]["viewer_url"],
            )
            self.assertEqual(url, records[0]["viewer_url"])
            response.url = records[1]["resolved_url"]
            with self.assertRaisesRegex(ValueError, "owned list"):
                await inventory.ensure_viewer_link(
                    driver, self.rosters[0].document_name, self.state / "dialog.html",
                    allow_sharing_changes=False, existing_viewer_url=records[0]["viewer_url"],
                )
        page.get_by_role.assert_not_called()

    def test_identity_evidence_preserves_original_source_key_and_address(self) -> None:
        roster = self.rosters[0]
        original = next(row for row in roster.parsed.rows if row["Name"] == "蒸餃阿姨")
        key = build_row_identity_key("imported", original)
        evidence = {key: {
            "original_name": original["Name"], "original_address": original["Address"],
            "match_address": "261宜蘭縣頭城鎮開蘭路57號",
            "reason": "Relocation confirmed by the award judge's published visit and identical phone.",
            "evidence_urls": ["https://www.yilan.com.tw/article/cjaycontent2/3953"],
        }}
        row = next(row for row in maintenance.managed_rows(roster, evidence) if row["Name"] == "蒸餃阿姨")
        self.assertEqual(maintenance.source_row_key(row), key)
        self.assertEqual(row["AwardOriginalAddress"], original["Address"])
        note = maintenance.roster_note(roster, row)
        self.assertIn(original["Address"], note)
        self.assertIn(evidence[key]["evidence_urls"][0], note)
        evidence[key]["original_address"] = row["Address"]
        with self.assertRaisesRegex(ValueError, "exact current official row"):
            maintenance.managed_rows(roster, evidence)

    def test_official_lane_addresses_produce_branch_specific_queries(self) -> None:
        for name, street in (("魏家婆菜", "仁愛路310巷29號"), ("太順油飯", "中央北路27巷1號")):
            row = next(row for row in self.rosters[0].parsed.rows if row["Name"] == name)
            with self.subTest(name=name):
                self.assertIn(f"{name} {street}", build_place_query_attempts(row))
        row = self.rosters[1].parsed.rows[0]
        self.assertIn(f"{row['Name']} 中山北路二段39巷3號3樓", build_place_query_attempts(row))

    async def test_query_transition_cannot_write_without_a_physical_place_url(self) -> None:
        record = json.loads((FIXTURES / "verified-place-probes-2026-11-08.json").read_text())["records"][0]
        states = json.loads((FIXTURES / "search-transition-states-2026-11-08.json").read_text())["states"]
        page = MagicMock()
        page.url = next(state["page_url"] for state in states if "/maps/search/" in state["page_url"])
        driver = maintenance.AwardMapsDriver(maintenance.GoogleMapsDriverConfig(
            user_data_dir=Path("/unused-profile"), headless=True, sync_delay_seconds=0,
        ))
        with (
            patch.object(driver, "_require_page", new=AsyncMock(return_value=page)),
            patch.object(driver, "_extract_current_place_candidate", new=AsyncMock(return_value=PlaceCandidate(**record["candidate"]))),
            patch.object(maintenance.GoogleMapsDriver, "save_current_place_to_list", new=AsyncMock()) as save,
        ):
            with self.assertRaisesRegex(maintenance.GoogleMapsTransientError, "physical place URL"):
                await driver.save_current_place_to_list(self.rosters[0].document_name)
        save.assert_not_awaited()

    async def test_owned_list_url_bypasses_recorded_search_overlay_and_checks_identity(self) -> None:
        html = BeautifulSoup((FIXTURES.parent / "google_maps/search-suggestions-2026-11-08.html").read_text(), "html.parser")
        self.assertEqual(html.select_one('input[role="combobox"]')["aria-expanded"], "true")
        metadata = json.loads((FIXTURES / "saved-list-one-row-2026-11-08.metadata.json").read_text())
        owned_url = metadata["recorded_list_url"]
        page = MagicMock()
        page.url = owned_url
        page.goto = AsyncMock()
        driver = maintenance.AwardMapsDriver(maintenance.GoogleMapsDriverConfig(
            user_data_dir=Path("/unused-profile"), headless=True, sync_delay_seconds=0,
        ))
        driver.owned_list_urls = {self.rosters[0].document_name: owned_url}
        with (
            patch.object(driver, "_require_page", new=AsyncMock(return_value=page)),
            patch.object(driver, "_wait_for_opened_list_context", new=AsyncMock()),
            patch.object(driver, "_active_list_title_matches", new=AsyncMock(return_value=True)) as title,
            patch.object(maintenance.GoogleMapsDriver, "open_list", new=AsyncMock()) as dashboard,
        ):
            self.assertTrue(await driver.open_list(self.rosters[0].document_name))
            page.goto.assert_awaited_once_with(owned_url, wait_until="commit")
            page.url = owned_url.replace(maintenance.list_id_from_url(owned_url), "<redacted-other-list-id>")
            self.assertFalse(await driver.open_list(self.rosters[0].document_name))
            page.url = owned_url
            title.return_value = False
            self.assertFalse(await driver.open_list(self.rosters[0].document_name))
        dashboard.assert_not_awaited()

    async def test_owned_removal_hovers_recorded_card_before_delete_and_rejects_unowned(self) -> None:
        html = BeautifulSoup((FIXTURES / "saved-list-one-row-2026-11-08.html").read_text(), "html.parser")
        note = html.select_one("textarea").get_text()
        award, key = MEMBERSHIP_MARKER_PATTERN.findall(note)[0]
        inventory = {"rows": [{"index": 0, "source_markers": [f"{award}/{key}"]}]}
        order = []
        card = MagicMock()
        card.hover = AsyncMock(side_effect=lambda: order.append("hover"))
        container = card.locator.return_value
        container.inner_text = AsyncMock(return_value=note)
        container.locator.return_value.input_value = AsyncMock(return_value=note)
        container.get_by_role.return_value.click = AsyncMock(side_effect=lambda: order.append("delete"))
        page = MagicMock()
        page.locator.return_value.nth.return_value = card
        page.wait_for_timeout = AsyncMock()
        driver = MagicMock()
        driver.open_list = AsyncMock(return_value=True)
        driver._require_page = AsyncMock(return_value=page)
        with patch("michelin_scraper.adapters.award_list_inventory.read_list_inventory", new=AsyncMock(return_value=inventory)):
            removed = await remove_owned_stale_rows(
                driver, list_name=self.rosters[0].document_name, award=award, current_keys=set(), owned_keys={key},
            )
            self.assertEqual(removed, [key])
            self.assertEqual(order, ["hover", "delete"])
            order.clear()
            with self.assertRaisesRegex(ValueError, "not positively owned"):
                await remove_owned_stale_rows(
                    driver, list_name=self.rosters[0].document_name, award=award, current_keys=set(), owned_keys=set(),
                )
            self.assertEqual(order, [])

    async def test_partial_sync_or_failed_readback_never_consumes_source_version(self) -> None:
        previous = {"status": "previous-confirmed-version"}
        maintenance._write_json(self.state / "applied.json", previous)
        for fail_during_sync in (True, False):
            with (
                self.subTest(fail_during_sync=fail_during_sync),
                patch.object(maintenance, "discover_taiwan_500_awards", return_value=self.rosters),
                patch.object(maintenance, "sync_award_rows", new=AsyncMock(
                    side_effect=RuntimeError("actual failed stage") if fail_during_sync else None,
                )),
                patch.object(maintenance, "verify_awards", new=AsyncMock(side_effect=RuntimeError("partial readback"))),
            ):
                with self.assertRaises(RuntimeError):
                    await maintenance.run_maintenance(state_dir=self.state, endpoint=maintenance.INTERACTIVE_CDP_URL)
                self.assertEqual(maintenance._read_json(self.state / "applied.json"), previous)
                self.assertFalse((self.state / "acceptance-manifest.json").exists())

    async def test_existing_unowned_default_named_list_is_never_adopted(self) -> None:
        driver = MagicMock()
        driver.list_exists = AsyncMock(return_value=True)
        writer = MagicMock()
        writer.initialize_run = AsyncMock()
        writer.finalize_run = AsyncMock()
        writer.dump_debug_html = AsyncMock(return_value=False)
        writer.list_created_by_level = {}
        writer.sync_rows_by_level = AsyncMock()
        with (
            patch.object(maintenance, "AwardMapsDriver", return_value=driver),
            patch.object(maintenance, "GoogleMapsSyncWriter", return_value=writer),
        ):
            with self.assertRaisesRegex(ValueError, "positive task ownership"):
                await maintenance.sync_award_rows(
                    roster=self.rosters[0], endpoint=maintenance.INTERACTIVE_CDP_URL, state_dir=self.state,
                )
        writer.sync_rows_by_level.assert_not_awaited()
        writer.finalize_run.assert_awaited_once()
        self.assertFalse(maintenance._read_json(self.state / "bowls/memberships.json").get("created_by_task"))

    async def test_removed_then_reintroduced_membership_is_saved_again(self) -> None:
        roster = self.rosters[0]
        rows = maintenance.managed_rows(roster)
        first_key = maintenance.source_row_key(rows[0])
        ledger = {"created_by_task": True, "list_name": roster.document_name, "removed_owned_keys": [first_key],
                  "confirmed": {maintenance.source_row_key(row): {"note": maintenance.roster_note(roster, row)} for row in rows}}
        maintenance._write_json(self.state / "bowls/memberships.json", ledger)
        writer = MagicMock()
        writer.initialize_run = AsyncMock()
        writer.finalize_run = AsyncMock()
        writer.sync_rows_by_level = AsyncMock()
        writer.list_created_by_level = {}
        with (
            patch.object(maintenance, "AwardMapsDriver", return_value=MagicMock()),
            patch.object(maintenance, "GoogleMapsSyncWriter", return_value=writer),
        ):
            await maintenance.sync_award_rows(
                roster=roster, endpoint=maintenance.INTERACTIVE_CDP_URL, state_dir=self.state, single_write=True,
            )
        selected = writer.sync_rows_by_level.call_args.args[0]["imported"]
        self.assertEqual([maintenance.source_row_key(row) for row in selected], [first_key])

    def test_missing_readback_retains_confirmed_ownership_and_queues_retry(self) -> None:
        roster = self.rosters[0]
        rows = maintenance.managed_rows(roster)
        ledger = {"confirmed": {maintenance.source_row_key(row): {"note": maintenance.roster_note(roster, row)} for row in rows}}
        before = copy.deepcopy(ledger["confirmed"])
        payload = json.loads((FIXTURES / "public-list-inventory-2026-11-08.json").read_text())["payload"]
        inventory = parse_public_list_inventory(payload, list_name=roster.document_name, list_id="<redacted-list-id>")
        maintenance.retain_inventory_retry_work(roster, ledger, inventory)
        present = {marker.split("/", 1)[1] for card in inventory["rows"] for marker in card["source_markers"]}
        absent = set(before) - present
        self.assertTrue(absent)
        self.assertTrue(absent <= set(ledger["retry_source_keys"]))
        self.assertEqual(ledger["confirmed"], before)

    async def test_restart_no_change_checks_auth_without_writing_or_reapplying(self) -> None:
        fingerprints = {roster.award: roster.source_fingerprint for roster in self.rosters}
        previous = {"status": "complete", "source_fingerprints": fingerprints,
                    "resolution_fingerprint": maintenance.hashlib.sha256(b"{}").hexdigest(),
                    "user_skip_fingerprint": maintenance.hashlib.sha256(b"{}").hexdigest()}
        maintenance._write_json(self.state / "applied.json", previous)
        before = (self.state / "applied.json").read_bytes()
        with (
            patch.object(maintenance, "discover_taiwan_500_awards", return_value=self.rosters),
            patch.object(maintenance, "verify_browser_auth", new=AsyncMock()) as auth,
            patch.object(maintenance, "sync_award_rows", new=AsyncMock()) as sync,
            patch.object(maintenance, "verify_awards", new=AsyncMock()) as verify,
        ):
            self.assertFalse(await maintenance.run_maintenance(state_dir=self.state, endpoint=maintenance.INTERACTIVE_CDP_URL))
        auth.assert_awaited_once_with(maintenance.INTERACTIVE_CDP_URL)
        sync.assert_not_awaited()
        verify.assert_not_awaited()
        self.assertEqual((self.state / "applied.json").read_bytes(), before)
        self.assertFalse(maintenance.source_version_is_applied(
            previous, source_fingerprints=fingerprints, resolution_fingerprint=previous["resolution_fingerprint"],
            user_skip_fingerprint="changed-policy",
        ))

    def test_cron_requires_matching_live_external_mutex_and_rejects_interactive_lane(self) -> None:
        lock_path = self.state / ".hermes/cron/locks/browser-cdp.lock"
        maintenance._write_json(lock_path, {"job_id": "7f9de040f528", "token": "fixture-lease"})
        with patch.object(Path, "home", return_value=self.state), patch.dict(os.environ, {
            "HERMES_EXECUTION_KIND": "cron", "HERMES_CRON_BROWSER_LANE_TOKEN": "fixture-lease",
        }):
            with self.assertRaisesRegex(ValueError, "must use"):
                maintenance._assert_browser_lane(maintenance.INTERACTIVE_CDP_URL)
            with self.assertRaisesRegex(ValueError, "not held"):
                maintenance._assert_browser_lane(maintenance.CRON_CDP_URL)
            with lock_path.open() as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                maintenance._assert_browser_lane(maintenance.CRON_CDP_URL, expected_job_id="7f9de040f528")
                with self.assertRaisesRegex(ValueError, "does not match"):
                    maintenance._assert_browser_lane(maintenance.CRON_CDP_URL, expected_job_id="wrong-job")
                with patch.dict(os.environ, {"HERMES_CRON_BROWSER_LANE_TOKEN": "wrong"}):
                    with self.assertRaisesRegex(ValueError, "does not match"):
                        maintenance._assert_browser_lane(maintenance.CRON_CDP_URL)


if __name__ == "__main__":
    unittest.main()
