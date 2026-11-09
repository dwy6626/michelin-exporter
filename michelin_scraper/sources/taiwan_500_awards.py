"""Discover and validate the current official Taiwan 500 award map exports."""

import argparse
import copy
import hashlib
import html
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from ..config.runtime import DEFAULT_AWARDS_STATE_DIR
from .my_maps import MyMapsParseResult, parse_my_maps_kml_text

FetchText = Callable[[str], str]

_USER_AGENT = "michelin-exporter/0.1 (+https://github.com/dwy6626/michelin-exporter)"
_SCRIPT_PATTERN = re.compile(r"(?:src=[\"'])([^\"']*?/js/index\.js\?[^\"']+)", re.IGNORECASE)
_STORY_PATTERN = re.compile(r"(?:\./)?api/get_story\.json\?hash=([A-Za-z0-9_-]+)")
_EDITION_PATTERN = re.compile(r"(?:500碗|500盤)\s*([12][0-9]{3})|([12][0-9]{3}).{0,12}(?:500碗|500盤)")
_KML_NAMESPACE = "http://www.opengis.net/kml/2.2"
_KML_TAG = f"{{{_KML_NAMESPACE}}}"


@dataclass(frozen=True)
class Taiwan500AwardDefinition:
    """One official award homepage and its expected authoritative folder."""

    award: str
    homepage_url: str
    map_marker: str
    winner_marker: str
    note_template: str


@dataclass(frozen=True)
class Taiwan500AwardRoster:
    """A complete, folder-scoped official roster ready for a normal My Maps sync."""

    award: str
    edition: int
    document_name: str
    folder_name: str
    source_urls: tuple[str, ...]
    map_url: str
    kml_url: str
    source_point_count: int
    total_kml_point_count: int
    excluded_folder_counts: tuple[tuple[str, int], ...]
    parsed: MyMapsParseResult
    filtered_kml: str
    source_fingerprint: str
    note_template: str


TAIWAN_500_AWARD_DEFINITIONS = (
    Taiwan500AwardDefinition(
        award="bowls",
        homepage_url="https://udn.com/500bowlsaward/",
        map_marker="完整電子地圖",
        winner_marker="",
        note_template=(
            "500碗 {edition} | 總得碗數: {總得碗數} | 得獎菜色: {得獎菜色} | "
            "地區: {地區} | 菜系: {菜系} | 推薦評審: {推薦評審} | "
            "地址: {地址} | 電話: {電話} | 官方: {source_url}"
        ),
    ),
    Taiwan500AwardDefinition(
        award="dishes",
        homepage_url="https://udn.com/500dishesaward/",
        map_marker="",
        winner_marker="得盤名單",
        note_template=(
            "500盤 {edition} | 總得盤數: {總得盤數} | 得獎菜色: {得獎菜色} | "
            "評審: {評審} | 地址: {地址} | 電話: {電話}"
        ),
    ),
)


def dishes_public_note(note: str) -> str:
    """Remove only the three user-rejected public field segments, preserving the rest verbatim."""

    return " | ".join(part for part in note.split(" | ") if not part.startswith(("區域:", "官方:", "管理:")))


def discover_taiwan_500_awards(fetch_text: FetchText | None = None) -> tuple[Taiwan500AwardRoster, ...]:
    """Return both current official rosters after strict source and folder validation."""

    fetch = fetch_text or _fetch_text
    return tuple(_discover_award(definition, fetch) for definition in TAIWAN_500_AWARD_DEFINITIONS)


def write_roster_kml(roster: Taiwan500AwardRoster, destination: Path) -> None:
    """Atomically retain the exact validated source subset used for a sync attempt."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    temporary.write_text(roster.filtered_kml, encoding="utf-8")
    temporary.replace(destination)


def roster_manifest_entry(roster: Taiwan500AwardRoster) -> dict[str, object]:
    """Build durable, non-secret provenance for an accepted roster."""

    return {
        "award": roster.award,
        "edition": roster.edition,
        "document_name": roster.document_name,
        "folder_name": roster.folder_name,
        "source_urls": list(roster.source_urls),
        "map_url": roster.map_url,
        "kml_url": roster.kml_url,
        "source_point_count": roster.source_point_count,
        "total_kml_point_count": roster.total_kml_point_count,
        "excluded_folder_counts": dict(roster.excluded_folder_counts),
        "unique_physical_rows": len(roster.parsed.rows),
        "source_fingerprint": roster.source_fingerprint,
    }


def _discover_award(definition: Taiwan500AwardDefinition, fetch: FetchText) -> Taiwan500AwardRoster:
    homepage_html = fetch(definition.homepage_url)
    edition = _extract_edition(homepage_html, definition.award)
    script_url = _extract_script_url(homepage_html, definition.homepage_url)
    script_text = fetch(script_url)
    story_url = _extract_story_url(script_text, definition.homepage_url)
    story_payload = _parse_story_payload(fetch(story_url), story_url)
    article_url, map_candidate = _find_official_map_candidate(
        definition=definition,
        story_payload=story_payload,
        edition=edition,
    )
    article_html = ""
    if not map_candidate:
        if not article_url:
            raise ValueError(f"Official {definition.award} discovery did not provide a winner article URL.")
        article_html = fetch(article_url)
        if str(edition) not in _visible_text(article_html):
            raise ValueError(
                f"Official {definition.award} winner article does not identify edition {edition}: {article_url}"
            )
        map_candidate = _extract_google_map_url(article_html)
    map_url = _resolve_map_url(map_candidate, fetch)
    map_id = _extract_map_id(map_url)
    kml_url = f"https://www.google.com/maps/d/kml?mid={map_id}&forcekml=1"
    kml_text = fetch(kml_url)
    document_name, folder_name, filtered_kml, total_count, excluded_counts = _filter_award_folder(
        kml_text=kml_text,
        award=definition.award,
        edition=edition,
    )
    parsed = parse_my_maps_kml_text(filtered_kml)
    selected_point_count = total_count - sum(count for _, count in excluded_counts)
    if parsed.skipped_rows or parsed.unsupported_rows or len(parsed.rows) != selected_point_count:
        raise ValueError(f"Official {definition.award} folder has unimportable rows; refusing a partial roster.")
    for row in parsed.rows:
        fields = row.get("DescriptionFields", {})
        if not isinstance(fields, dict) or not fields.get("得獎菜色"):
            raise ValueError(f"Official {definition.award} row has no award-dish evidence: {row['Name']!r}")
    deduplicated_rows = _deduplicate_exact_physical_rows(parsed.rows)
    if len(deduplicated_rows) != len(parsed.rows):
        filtered_kml = _filtered_kml_from_rows(
            document_name=document_name,
            folder_name=folder_name,
            rows=deduplicated_rows,
        )
        parsed = parse_my_maps_kml_text(filtered_kml)
    if not parsed.rows:
        raise ValueError(f"Official {definition.award} folder {folder_name!r} is empty.")
    source_urls = tuple(
        url for url in (definition.homepage_url, story_url, article_url, map_url, kml_url) if url
    )
    fingerprint = _fingerprint_roster(
        award=definition.award,
        edition=edition,
        document_name=document_name,
        folder_name=folder_name,
        rows=parsed.rows,
    )
    note_template = definition.note_template.replace("{edition}", str(edition)).replace(
        "{source_url}", article_url or definition.homepage_url
    )
    return Taiwan500AwardRoster(
        award=definition.award,
        edition=edition,
        document_name=document_name,
        folder_name=folder_name,
        source_urls=source_urls,
        map_url=map_url,
        kml_url=kml_url,
        source_point_count=selected_point_count,
        total_kml_point_count=total_count,
        excluded_folder_counts=excluded_counts,
        parsed=parsed,
        filtered_kml=filtered_kml,
        source_fingerprint=fingerprint,
        note_template=note_template,
    )


def _fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/json,application/vnd.google-earth.kml+xml"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed official URLs are validated below.
        return response.read().decode("utf-8")


def _extract_edition(homepage_html: str, award: str) -> int:
    matches = []
    award_marker = "500碗" if award == "bowls" else "500盤"
    for match in _EDITION_PATTERN.finditer(_visible_text(homepage_html)):
        year = match.group(1) or match.group(2)
        if year and award_marker in match.group(0):
            matches.append(int(year))
    if not matches:
        raise ValueError(f"Official {award} homepage has no current edition marker.")
    return max(matches)


def _extract_script_url(homepage_html: str, homepage_url: str) -> str:
    match = _SCRIPT_PATTERN.search(homepage_html)
    if match is None:
        raise ValueError(f"Official homepage is missing its current index.js source: {homepage_url}")
    return urljoin(homepage_url, html.unescape(match.group(1)))


def _extract_story_url(script_text: str, homepage_url: str) -> str:
    match = _STORY_PATTERN.search(script_text)
    if match is None:
        raise ValueError(f"Official frontend script is missing get_story.json: {homepage_url}")
    return urljoin(homepage_url, f"api/get_story.json?hash={match.group(1)}")


def _parse_story_payload(raw_payload: str, story_url: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Official story API returned invalid JSON: {story_url}") from exc
    if not isinstance(parsed, dict) or parsed.get("status") is not True:
        raise ValueError(f"Official story API did not report success: {story_url}")
    return parsed


def _find_official_map_candidate(
    *,
    definition: Taiwan500AwardDefinition,
    story_payload: dict[str, object],
    edition: int,
) -> tuple[str, str]:
    layouts = story_payload.get("layout")
    if not isinstance(layouts, list):
        raise ValueError(f"Official {definition.award} story API has no layout list.")
    if definition.award == "bowls":
        for layout in layouts:
            if not isinstance(layout, dict):
                continue
            for item in _layout_items(layout):
                headline = _as_text(item.get("headline"))
                link = _as_link(item.get("link"))
                if definition.map_marker in headline and str(edition) in headline and link:
                    return "", link
        raise ValueError(f"Official bowls story has no current-edition {definition.map_marker!r} link.")

    for layout in layouts:
        if not isinstance(layout, dict):
            continue
        title = _as_text(layout.get("title"))
        if definition.winner_marker not in title:
            continue
        items = tuple(_layout_items(layout))
        if len(items) != 1:
            raise ValueError("Official dishes winner layout must have exactly one current winner article.")
        link = _as_link(items[0].get("link"))
        if link:
            return link, ""
    raise ValueError(f"Official dishes story has no current-edition {definition.winner_marker!r} layout.")


def _layout_items(layout: dict[str, object]) -> Iterable[dict[str, object]]:
    items = layout.get("list")
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, dict))


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):
        return " ".join(_as_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_as_text(item) for item in value.values())
    return ""


def _as_link(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _visible_text(raw_html: str) -> str:
    return BeautifulSoup(raw_html, "html.parser").get_text(" ", strip=True)


def _extract_google_map_url(article_html: str) -> str:
    match = re.search(r"https?://www\.google\.com/maps/d/(?:u/\d+/)?edit\?[^\"'<\s]+", article_html)
    if match is None:
        raise ValueError("Official winner article has no public Google My Maps edit URL.")
    return html.unescape(match.group(0))


def _resolve_map_url(candidate_url: str, fetch: FetchText) -> str:
    parsed = urlparse(candidate_url)
    if parsed.netloc == "reurl.cc":
        soup = BeautifulSoup(fetch(candidate_url), "html.parser")
        target = soup.select_one("input#target")
        resolved = str(target.get("value", "")).strip() if target is not None else ""
        if not resolved:
            raise ValueError(f"Official short link did not expose its public destination: {candidate_url}")
        return html.unescape(resolved)
    return candidate_url


def _extract_map_id(map_url: str) -> str:
    parsed = urlparse(map_url)
    if parsed.scheme != "https" or parsed.netloc != "www.google.com" or not parsed.path.startswith("/maps/d/"):
        raise ValueError(f"Official map destination is not a Google My Maps URL: {map_url}")
    map_ids = parse_qs(parsed.query).get("mid", [])
    if len(map_ids) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{8,}", map_ids[0]):
        raise ValueError(f"Official Google My Maps URL has no valid map ID: {map_url}")
    return map_ids[0]


def _filter_award_folder(
    *,
    kml_text: str,
    award: str,
    edition: int,
) -> tuple[str, str, str, int, tuple[tuple[str, int], ...]]:
    try:
        root = ElementTree.fromstring(kml_text)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Official {award} KML is invalid XML: {exc}") from exc
    document = root.find(f".//{_KML_TAG}Document")
    if document is None:
        raise ValueError(f"Official {award} KML has no Document.")
    document_name = _child_text(document, "name")
    expected_marker = "500碗" if award == "bowls" else "500盤"
    if expected_marker not in document_name or str(edition) not in document_name:
        raise ValueError(
            f"Official {award} KML document does not match current edition {edition}: {document_name!r}"
        )
    expected_folder = f"{expected_marker}{edition}"
    folders = [child for child in document if _local_name(child) == "Folder"]
    selected = [folder for folder in folders if _child_text(folder, "name") == expected_folder]
    if len(selected) != 1:
        names = ", ".join(repr(_child_text(folder, "name")) for folder in folders)
        raise ValueError(
            f"Official {award} KML requires exactly one {expected_folder!r} folder; found: {names or '<none>'}"
        )
    selected_folder = selected[0]
    total_count = len([element for element in root.iter() if _local_name(element) == "Placemark"])
    excluded_counts = tuple(
        (name, len([element for element in folder.iter() if _local_name(element) == "Placemark"]))
        for folder in folders
        if folder is not selected_folder
        if (name := _child_text(folder, "name"))
    )
    filtered_kml = _serialize_filtered_kml(document_name=document_name, selected_folder=selected_folder)
    return document_name, expected_folder, filtered_kml, total_count, excluded_counts


def _serialize_filtered_kml(*, document_name: str, selected_folder: ElementTree.Element) -> str:
    root = ElementTree.Element(f"{_KML_TAG}kml")
    document = ElementTree.SubElement(root, f"{_KML_TAG}Document")
    ElementTree.SubElement(document, f"{_KML_TAG}name").text = document_name
    document.append(copy.deepcopy(selected_folder))
    ElementTree.register_namespace("", _KML_NAMESPACE)
    return ElementTree.tostring(root, encoding="unicode", xml_declaration=True)


def _filtered_kml_from_rows(*, document_name: str, folder_name: str, rows: tuple[dict[str, object], ...]) -> str:
    root = ElementTree.Element(f"{_KML_TAG}kml")
    document = ElementTree.SubElement(root, f"{_KML_TAG}Document")
    ElementTree.SubElement(document, f"{_KML_TAG}name").text = document_name
    folder = ElementTree.SubElement(document, f"{_KML_TAG}Folder")
    ElementTree.SubElement(folder, f"{_KML_TAG}name").text = folder_name
    for row in rows:
        placemark = ElementTree.SubElement(folder, f"{_KML_TAG}Placemark")
        ElementTree.SubElement(placemark, f"{_KML_TAG}name").text = str(row["Name"])
        ElementTree.SubElement(placemark, f"{_KML_TAG}address").text = str(row["Address"])
        fields = row.get("DescriptionFields", {})
        if isinstance(fields, dict):
            extended = ElementTree.SubElement(placemark, f"{_KML_TAG}ExtendedData")
            for key, value in fields.items():
                data = ElementTree.SubElement(extended, f"{_KML_TAG}Data", {"name": str(key)})
                ElementTree.SubElement(data, f"{_KML_TAG}value").text = str(value)
    ElementTree.register_namespace("", _KML_NAMESPACE)
    return ElementTree.tostring(root, encoding="unicode", xml_declaration=True)


def _deduplicate_exact_physical_rows(rows: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
    unique: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (_normalized_identity(row.get("Name", "")), _normalized_identity(row.get("Address", "")))
        if not all(key):
            raise ValueError("Official roster has a selected row without a stable name and address.")
        if key not in seen:
            unique.append(row)
            seen.add(key)
        else:
            previous = next(item for item in unique if (
                _normalized_identity(item.get("Name", "")),
                _normalized_identity(item.get("Address", "")),
            ) == key)
            if previous != row:
                raise ValueError(f"Official roster has conflicting duplicate location data: {row['Name']!r}")
    return tuple(unique)


def _normalized_identity(value: object) -> str:
    return " ".join(str(value).split()).casefold()


def _fingerprint_roster(
    *,
    award: str,
    edition: int,
    document_name: str,
    folder_name: str,
    rows: tuple[dict[str, object], ...],
) -> str:
    canonical_rows = [
        {
            "name": _normalized_identity(row.get("Name", "")),
            "address": _normalized_identity(row.get("Address", "")),
            "latitude": row.get("Latitude"),
            "longitude": row.get("Longitude"),
            "fields": dict(sorted(_string_fields(row.get("DescriptionFields", {})).items())),
        }
        for row in rows
    ]
    canonical = {
        "award": award,
        "edition": edition,
        "document_name": document_name,
        "folder_name": folder_name,
        "rows": sorted(canonical_rows, key=lambda row: (row["name"], row["address"])),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _string_fields(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(field_value) for key, field_value in value.items()}


def _local_name(element: ElementTree.Element) -> str:
    return str(element.tag).rsplit("}", 1)[-1]


def _child_text(element: ElementTree.Element, local_name: str) -> str:
    for child in element:
        if _local_name(child) == local_name:
            return " ".join((child.text or "").split())
    return ""


def main() -> int:
    """Run the read-only official-source probe without a browser or Saved List writes."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-dir", default=DEFAULT_AWARDS_STATE_DIR,
        help=f"Save validated KML subsets and source-probe.json here (default: {DEFAULT_AWARDS_STATE_DIR}).",
    )
    arguments = parser.parse_args()
    destination = Path(arguments.state_dir).expanduser()
    try:
        rosters = discover_taiwan_500_awards()
        for roster in rosters:
            write_roster_kml(roster, destination / "sources" / f"{roster.award}-{roster.edition}.kml")
        report = json.dumps([roster_manifest_entry(roster) for roster in rosters], ensure_ascii=False, indent=2) + "\n"
        destination.mkdir(parents=True, exist_ok=True)
        temporary = destination / "source-probe.json.tmp"
        temporary.write_text(report, encoding="utf-8")
        temporary.replace(destination / "source-probe.json")
        print(report, end="")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Official-source probe failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
