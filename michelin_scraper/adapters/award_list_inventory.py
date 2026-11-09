"""Recorded-DOM Saved List inventory and viewer-link acceptance for award maintenance."""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import requests
from bs4 import BeautifulSoup

from ..application.html_redaction import find_unredacted_sensitive_markers, redact_html_text
from .google_maps_driver import GoogleMapsDriver

PLACE_CARD_SELECTOR = "button.SMP2wb"
VIEW_DIALOG_NAME = "Send link to view"
VIEW_MENU_NAME = re.compile(r"^Send link to view")
MEMBERSHIP_MARKER_PATTERN = re.compile(r"500-awards/(bowls|dishes)/([0-9a-f]{64})")
PLACE_COUNT_PATTERN = re.compile(r"(\d+)\s*(?:places?|個地點)")
PUBLIC_LIST_PATH = "/maps/preview/entitylist/getlist"
PUBLIC_LIST_QUERY_KEYS = frozenset({"authuser", "hl", "gl", "pb"})


def list_id_from_url(url: str) -> str:
    match = re.search(r"!11m\d+!2s([^!/?]+)", url)
    if match is None:
        raise ValueError("Owned Maps list URL did not expose its list identity.")
    return match[1]


def sanitize_inventory_html(html_text: str) -> str:
    """Remove account identities and script state while retaining the real Maps DOM."""

    soup = BeautifulSoup(redact_html_text(html_text), "html.parser")
    for script in soup.select("script"):
        script.decompose()
    for author in soup.select(".d4r55"):
        name = author.get_text(" ", strip=True)
        card = author.find_parent(class_="jftiEf") or author.parent or author
        author.string = "<redacted-account-name>"
        for node in card.select("[aria-label], [title], [alt]"):
            for attribute in ("aria-label", "title", "alt"):
                value = node.get(attribute)
                if name and isinstance(value, str):
                    node[attribute] = value.replace(name, "<redacted-account-name>")
        for avatar in card.select("img"):
            avatar["src"] = "data:,"
            avatar.attrs.pop("srcset", None)
    for contributor in soup.select('a[href*="/maps/contrib/"]'):
        contributor["href"] = "https://www.google.com/maps/contrib/<redacted-account-id>"
    for heading in soup.select("h2"):
        text = heading.get_text(" ", strip=True)
        if re.search(r"Shared|Private|已分享|私人", text):
            heading.string = "<redacted-account-name> · " + re.sub(
                r"^.*?(?=Shared|Private|已分享|私人)", "", text,
            )
    redacted = redact_html_text(str(soup))
    markers = find_unredacted_sensitive_markers(redacted)
    if markers:
        raise ValueError(f"Refusing to store unredacted inventory HTML: {', '.join(markers)}")
    return redacted


async def save_inventory_html(page: Any, path: Path) -> None:
    """Redact account metadata before retaining any real browser HTML."""

    redacted = sanitize_inventory_html(await page.content())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redacted, encoding="utf-8")


async def ensure_viewer_link(
    driver: GoogleMapsDriver, list_name: str, evidence_path: Path, *, allow_sharing_changes: bool = True,
    existing_viewer_url: str | None = None,
) -> str:
    """Use only the recorded 'Send link to view' flow; never enter the edit-invite flow."""

    if not await driver.open_list(list_name):
        raise ValueError(f"Cannot open the owned award list for viewer sharing: {list_name}")
    page = await driver._require_page()
    already_shared = await page.locator("h2").filter(has_text=re.compile(r"\bShared\b|已分享")).count() > 0
    if not already_shared and not allow_sharing_changes:
        raise ValueError(f"Read-only verification requires existing viewer sharing; refusing to enable it: {list_name}")
    if not allow_sharing_changes:
        if not existing_viewer_url or not re.fullmatch(r"https://maps\.app\.goo\.gl/[A-Za-z0-9]+", existing_viewer_url):
            raise ValueError("Read-only verification requires the previously recorded viewer short link.")
        await asyncio.to_thread(_verify_viewer_destination, existing_viewer_url, list_id_from_url(str(page.url)))
        return existing_viewer_url
    await page.get_by_role("button", name="Share", exact=True).click()
    await page.get_by_role("menuitemradio", name=VIEW_MENU_NAME).click()
    dialog = page.get_by_role("dialog", name=VIEW_DIALOG_NAME, exact=True)
    get_link = dialog.get_by_role("button", name="Get link to share", exact=True)
    if not already_shared:
        await get_link.wait_for(state="visible", timeout=15000)
        await get_link.click()
        await dialog.get_by_role("button", name="Get link", exact=True).click()
    link_field = dialog.locator("input").first
    await link_field.wait_for(state="visible", timeout=15000)
    viewer_url = str(await link_field.input_value()).strip()
    if not re.fullmatch(r"https://maps\.app\.goo\.gl/[A-Za-z0-9]+", viewer_url):
        raise ValueError("View-sharing dialog did not produce a Maps viewer short link.")
    await save_inventory_html(page, evidence_path)
    await dialog.get_by_role("button", name="Close", exact=True).click()
    return viewer_url


def _verify_viewer_destination(viewer_url: str, expected_list_id: str) -> None:
    """Resolve a recorded public viewer link without browser cookies or authentication."""

    with urlopen(Request(viewer_url, headers={"Accept": "text/html"}), timeout=30) as response:
        resolved = urlparse(response.url)
        if (response.status != 200 or resolved.scheme != "https" or resolved.netloc != "www.google.com"
                or not resolved.path.startswith("/maps/") or list_id_from_url(response.url) != expected_list_id):
            raise ValueError("Recorded viewer short link does not resolve anonymously to the owned list.")


async def read_list_inventory(
    page: Any, list_name: str, *, expected_count: int | None = None, physical_ids: bool = False,
) -> dict[str, Any]:
    """Read every physically rendered membership independently of the write ledger."""

    title = page.get_by_role("heading", name=list_name, exact=True).or_(
        page.get_by_role("button", name=list_name, exact=True)
    )
    await title.first.wait_for(state="visible", timeout=20000)
    deadline = time.monotonic() + 90
    physical_count = None
    while time.monotonic() < deadline:
        headings = await page.locator("h2").all_text_contents()
        counts = [int(match[1]) for text in headings if (match := PLACE_COUNT_PATTERN.search(text))]
        if len(counts) == 1:
            physical_count = counts[0]
            break
        await page.wait_for_timeout(100)
    if physical_count is None:
        raise ValueError(f"Saved List physical count was not rendered: {list_name}")
    if expected_count is not None and physical_count != expected_count:
        raise ValueError(f"Saved List count mismatch for {list_name}: expected={expected_count}, actual={physical_count}")
    while time.monotonic() < deadline:
        count = await page.locator(PLACE_CARD_SELECTOR).count()
        if count >= physical_count:
            break
        await page.evaluate("""() => {
            for (const panel of document.querySelectorAll('div.XiKgde')) {
                if (panel.scrollHeight > panel.clientHeight) panel.scrollTop = panel.scrollHeight;
            }
        }""")
        await page.wait_for_timeout(200)
    rows = await page.locator(PLACE_CARD_SELECTOR).evaluate_all("""elements => elements.map((element, index) => {
        const container = element.parentElement.parentElement;
        return {index, name: element.querySelector('.fontHeadlineSmall')?.innerText || '',
            text: container.innerText, note: container.querySelector('textarea')?.value || ''};
    })""")
    if len(rows) != physical_count:
        raise ValueError(f"Incomplete Saved List inventory: rendered={len(rows)}, count={physical_count}")
    for row in rows:
        markers = MEMBERSHIP_MARKER_PATTERN.findall(row["note"] + " " + row["text"])
        row["source_markers"] = sorted(set(f"{award}/{key}" for award, key in markers))
    inventory = {
        "list_name": list_name, "physical_count": physical_count,
        "rendered_rows": len(rows), "rows": rows,
        "resolved_url": str(page.url),
    }
    if physical_ids:
        public = await read_anonymous_list_inventory(page, list_name, expected_count=physical_count)
        attach_physical_ids(inventory, public)
    return inventory


def attach_physical_ids(inventory: dict[str, Any], public: dict[str, Any]) -> None:
    """Join complete DOM cards to physical renderer identities by unique exact name and note."""

    identities = {}
    seen_ids = set()
    for row in public["rows"]:
        signature = (row["name"], row["note"])
        place_id = row["place_id"]
        if signature in identities or not place_id or place_id in seen_ids:
            raise ValueError("Public inventory has duplicate or ambiguous physical memberships.")
        identities[signature] = place_id
        seen_ids.add(place_id)
    if len(inventory["rows"]) != len(identities):
        raise ValueError("Signed and public inventory counts differ.")
    for row in inventory["rows"]:
        place_id = identities.pop((row["name"], row["note"]), None)
        if place_id is None:
            raise ValueError("Signed card has no unique exact public name/note association.")
        row["place_id"] = place_id


def owned_source_key(card: dict[str, Any], *, award: str, confirmed: dict[str, Any]) -> str:
    """Resolve ownership without public Dishes markers; reject conflicting or unknown identities."""

    markers = card["source_markers"]
    if award == "dishes":
        place_id = card.get("place_id")
        keys = [key for key, saved in confirmed.items() if place_id and saved.get("place_id") == place_id]
        if len(keys) != 1:
            raise ValueError("Unknown or duplicate confirmed physical place identity in dishes inventory.")
        key = keys[0]
        if markers and markers != [f"{award}/{key}"]:
            raise ValueError("Public source marker conflicts with the confirmed physical identity.")
        return key
    if len(markers) != 1:
        raise ValueError("Unidentified or duplicate source association in inventory.")
    marker_award, key = markers[0].split("/", 1)
    if marker_award != award or key not in confirmed:
        raise ValueError("Independent readback contains a membership not positively owned by this task.")
    return key


def parse_public_list_inventory(
    payload: Any, *, list_name: str, list_id: str, expected_count: int | None = None,
) -> dict[str, Any]:
    """Parse the recorded public renderer response; never retain owner/account metadata."""

    try:
        root = payload[0]
        actual_list_id = root[0][0]
        actual_list_name = root[4]
        entries = root[8]
        total = root[12]
    except (IndexError, TypeError, KeyError) as exc:
        raise ValueError("Public Saved List response has no complete membership inventory.") from exc
    if actual_list_id != list_id or actual_list_name != list_name:
        raise ValueError("Public Saved List response belongs to a different list.")
    if not isinstance(total, int) or not isinstance(entries, list) or len(entries) != total:
        raise ValueError("Public Saved List response is partial or has an invalid server count.")
    if expected_count is not None and total != expected_count:
        raise ValueError(f"Anonymous count mismatch: expected={expected_count}, actual={total}")
    rows = []
    for index, entry in enumerate(entries):
        try:
            feature_parts = entry[1][6]
            if len(feature_parts) != 2:
                raise ValueError("Public membership has no physical feature identity.")
            # The renderer serializes signed int64 halves; Maps URLs use their
            # unsigned hexadecimal representation.
            place_id = ":".join(hex(int(part) & ((1 << 64) - 1)) for part in feature_parts)
            name, note = entry[2], entry[3]
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError("Public membership has an invalid place identity or note schema.") from exc
        if not isinstance(name, str) or not isinstance(note, str):
            raise ValueError("Public membership has no readable place name and note.")
        markers = MEMBERSHIP_MARKER_PATTERN.findall(note)
        rows.append({
            "index": index, "name": name, "text": note, "note": note,
            "place_id": place_id,
            "source_markers": sorted(set(f"{award}/{key}" for award, key in markers)),
        })
    return {"list_name": list_name, "physical_count": total, "rendered_rows": len(rows),
            "rows": rows, "anonymous": True, "credentials_sent": False,
            "transport": "public Maps renderer response without authentication or cookies"}


def _get_public_list_payload(url: str) -> Any:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if parsed.scheme != "https" or parsed.netloc != "www.google.com" or parsed.path != PUBLIC_LIST_PATH:
        raise ValueError("Refusing an unexpected public Saved List renderer endpoint.")
    if not set(query) <= PUBLIC_LIST_QUERY_KEYS or "pb" not in query:
        raise ValueError("Public renderer request includes unexpected query fields.")
    # Prepare outside Session.prepare_request so netrc credentials and browser
    # cookie jars are never consulted. Preserve configured proxy/CA settings.
    request = requests.Request("GET", url, headers={"Accept": "application/json"}).prepare()
    if "Cookie" in request.headers or "Authorization" in request.headers:
        raise ValueError("Anonymous readback must not send authentication headers.")
    with requests.Session() as session:
        settings = session.merge_environment_settings(str(request.url), {}, None, None, None)
        response = session.send(request, timeout=30, allow_redirects=False, **settings)
        if response.status_code != 200:
            raise ValueError(f"Anonymous Saved List renderer returned HTTP {response.status_code}.")
        text = response.content.decode("utf-8")
        if text.startswith(")]}'"):
            text = text[4:]
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Anonymous Saved List renderer did not return valid JSON.") from exc


async def read_anonymous_list_inventory(
    page: Any, list_name: str, *, expected_count: int | None = None,
) -> dict[str, Any]:
    """Replay only the owned list's observed public request without browser credentials."""

    list_id = list_id_from_url(str(page.url))
    urls = await page.evaluate("""() => performance.getEntriesByType('resource')
        .map(entry => entry.name).filter(url => url.includes('/maps/preview/entitylist/getlist'))""")
    matching = [url for url in urls if list_id in parse_qs(urlparse(url).query).get("pb", [""])[0]]
    if not matching:
        raise ValueError("The owned list has no observed renderer request for independent anonymous readback.")
    payload = await asyncio.to_thread(_get_public_list_payload, matching[-1])
    return parse_public_list_inventory(payload, list_name=list_name, list_id=list_id, expected_count=expected_count)


async def rename_owned_list(
    driver: GoogleMapsDriver, old_name: str, new_name: str, *, expected_list_id: str | None = None,
) -> None:
    """Rename the one task-owned list using the existing normal inline title writer."""

    if not await driver.open_list(old_name):
        if expected_list_id and await driver.open_list(new_name):
            if list_id_from_url(str((await driver._require_page()).url)) == expected_list_id:
                return
        raise ValueError(f"Cannot find the prior owned edition list: {old_name}")
    page = await driver._require_page()
    if expected_list_id and list_id_from_url(str(page.url)) != expected_list_id:
        raise ValueError("The prior edition name resolved to a different physical list.")
    owned_url = str(page.url)
    if not await driver._try_rename_inline_untitled_list(page, new_name):
        raise ValueError(f"Unable to persist the latest default document name: {new_name}")
    await page.goto(owned_url, wait_until="commit")
    await driver._wait_for_opened_list_context(page, list_name=new_name)
    if not await driver._active_list_title_matches(page, new_name):
        raise ValueError("Edition list rename did not survive a fresh list open.")


async def remove_owned_stale_rows(
    driver: GoogleMapsDriver, *, list_name: str, award: str,
    current_keys: set[str], owned_keys: set[str], confirmed: dict[str, Any] | None = None,
) -> list[str]:
    """Remove only positively owned obsolete rows after complete replacement verification."""

    removed = []
    if not await driver.open_list(list_name):
        raise ValueError(f"Cannot open owned list for stale membership reconciliation: {list_name}")
    page = await driver._require_page()
    inventory = await read_list_inventory(page, list_name, physical_ids=award == "dishes")
    ownership = confirmed if confirmed is not None else dict.fromkeys(owned_keys, {})
    keys = [owned_source_key(row, award=award, confirmed=ownership) for row in inventory["rows"]]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate physical memberships prevent stale-row reconciliation.")
    for row in reversed(inventory["rows"]):
        key = owned_source_key(row, award=award, confirmed=ownership)
        if key not in owned_keys:
            raise ValueError("A membership is not positively owned by this award task.")
        if key in current_keys:
            continue
        card = page.locator(PLACE_CARD_SELECTOR).nth(row["index"])
        container = card.locator("../..")
        actual_note = str(await container.locator("textarea").input_value())
        if award == "dishes":
            unchanged = (actual_note == row["note"]
                         and await card.locator(".fontHeadlineSmall").inner_text() == row["name"])
        else:
            unchanged = f"{award}/{key}" in ((await container.inner_text()) + " " + actual_note)
        if not unchanged:
            raise ValueError("Stale membership identity changed before removal.")
        await card.hover()
        await container.get_by_role("button", name="Delete", exact=True).click()
        await page.wait_for_timeout(150)
        removed.append(key)
    return removed
