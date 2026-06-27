"""Google My Maps KML/KMZ source adapter."""

import html
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from ..application.source_models import SourceBucket, SourcePlan, SourceRunHandlers, SourceRunResult
from ..application.sync_models import ScrapeSyncCommand
from ..application.sync_ports import SyncOutputPort

_IMPORTED_BUCKET = SourceBucket(slug="imported", label="Imported", badge="Imported")
_KML_SUFFIX = ".kml"
_KMZ_SUFFIX = ".kmz"
_ADDRESS_KEYS = frozenset({"address", "addr", "formattedaddress", "formatted_address", "地址"})
_CITY_KEYS = frozenset({"city", "town", "municipality", "locality", "地區"})
_NAME_ALIAS_SEPARATOR_PATTERN = re.compile(r"[｜|／/]+")
_NAME_PARENTHETICAL_SEGMENT_PATTERN = re.compile(r"\s*[（(][^）)]{1,24}[）)]\s*")


@dataclass(frozen=True)
class MyMapsParseResult:
    """Parsed My Maps KML payload."""

    document_name: str
    rows: tuple[dict[str, Any], ...]
    skipped_rows: int
    unsupported_rows: int


class MyMapsSourceAdapter:
    """Source adapter for exported Google My Maps KML/KMZ files."""

    def __init__(self) -> None:
        self._parse_result: MyMapsParseResult | None = None

    def prepare(self, command: ScrapeSyncCommand, output: SyncOutputPort) -> SourcePlan:
        parse_result = parse_my_maps_file(command.my_maps_file)
        self._parse_result = parse_result
        if parse_result.skipped_rows > 0 or parse_result.unsupported_rows > 0:
            output.warn(
                "My Maps import skipped rows: "
                f"invalid_or_unnamed={parse_result.skipped_rows}, "
                f"unsupported_without_address_or_point={parse_result.unsupported_rows}."
            )
        scope_name = _resolve_scope_name(
            list_name=command.my_maps_list_name,
            document_name=parse_result.document_name,
            my_maps_file=command.my_maps_file,
        )
        checkpoint_scope = f"my-maps-{_safe_scope(scope_name)}"
        return SourcePlan(
            source_id="my-maps",
            scope_name=scope_name,
            checkpoint_scope=checkpoint_scope,
            start_url=f"source://my-maps/{checkpoint_scope}",
            buckets=(_IMPORTED_BUCKET,),
        )

    def run(
        self,
        *,
        command: ScrapeSyncCommand,
        plan: SourcePlan,
        handlers: SourceRunHandlers,
    ) -> SourceRunResult:
        del command, plan
        parse_result = self._require_parse_result()
        total_rows = len(parse_result.rows)
        for row in parse_result.rows:
            handlers.on_item(1, 1, total_rows, _IMPORTED_BUCKET.slug, dict(row))
        handlers.on_page(
            1,
            handlers.start_cursor,
            [dict(row) for row in parse_result.rows],
            None,
            2,
            1,
            total_rows,
        )
        return SourceRunResult(
            total_rows=total_rows,
            processed_pages=1,
            skipped_rows=parse_result.skipped_rows,
            unsupported_rows=parse_result.unsupported_rows,
        )

    def group_local_rows_by_bucket(
        self,
        *,
        rows: list[dict[str, Any]],
        bucket_slugs: tuple[str, ...],
    ) -> dict[str, list[dict[str, Any]]]:
        grouped_rows = {bucket_slug: [] for bucket_slug in bucket_slugs}
        if _IMPORTED_BUCKET.slug in grouped_rows:
            grouped_rows[_IMPORTED_BUCKET.slug] = [dict(row) for row in rows]
        return grouped_rows

    def _require_parse_result(self) -> MyMapsParseResult:
        if self._parse_result is None:
            raise ValueError("My Maps source has not been prepared.")
        return self._parse_result


def parse_my_maps_file(my_maps_file: str) -> MyMapsParseResult:
    """Parse an exported Google My Maps .kml or .kmz file."""

    source_path = Path(my_maps_file).expanduser()
    if not source_path.exists():
        raise ValueError(f"my-maps-file does not exist: {source_path}")
    if not source_path.is_file():
        raise ValueError(f"my-maps-file is not a file: {source_path}")

    suffix = source_path.suffix.lower()
    if suffix == _KML_SUFFIX:
        kml_text = source_path.read_text(encoding="utf-8")
    elif suffix == _KMZ_SUFFIX:
        kml_text = _read_kmz_kml_text(source_path)
    else:
        raise ValueError("my-maps-file must be a .kml or .kmz file.")
    return parse_my_maps_kml_text(kml_text)


def parse_my_maps_kml_text(kml_text: str) -> MyMapsParseResult:
    """Parse My Maps KML text into sync rows."""

    try:
        root = ElementTree.fromstring(kml_text)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Invalid KML XML: {exc}") from exc

    document = _find_first_descendant(root, "Document")
    document_name = _element_text(_find_direct_child(document, "name")) if document is not None else ""
    placemarks = list(_iter_descendants_by_local(root, "Placemark"))
    rows: list[dict[str, Any]] = []
    skipped_rows = 0
    unsupported_rows = 0

    for placemark in placemarks:
        name = _element_text(_find_direct_child(placemark, "name"))
        if not name:
            skipped_rows += 1
            continue
        point = _find_first_descendant(placemark, "Point")
        coordinates = _extract_point_coordinates(point)
        extended_data = _extract_extended_data(placemark)
        address = _first_extended_value(extended_data, _ADDRESS_KEYS)
        if not address:
            address = _element_text(_find_direct_child(placemark, "address"))
        city = _first_extended_value(extended_data, _CITY_KEYS)
        if coordinates is None and not address:
            unsupported_rows += 1 if point is None else 0
            skipped_rows += 1 if point is not None else 0
            continue
        row: dict[str, Any] = {
            "Name": name,
            "Description": _strip_html(_element_text(_find_direct_child(placemark, "description"))),
            "Address": address,
            "City": city,
        }
        aliases = _derive_name_aliases(name)
        if aliases:
            row["Aliases"] = aliases
        if coordinates is not None:
            longitude, latitude = coordinates
            coordinate_query = _format_coordinate_query(latitude=latitude, longitude=longitude)
            row["Address"] = address or coordinate_query
            row["Latitude"] = latitude
            row["Longitude"] = longitude
        rows.append(row)

    if not rows:
        raise ValueError(
            "my-maps-file has no importable placemarks with names and address or valid coordinates. "
            f"Found placemarks={len(placemarks)}, skipped={skipped_rows}, unsupported={unsupported_rows}."
        )
    return MyMapsParseResult(
        document_name=document_name,
        rows=tuple(rows),
        skipped_rows=skipped_rows,
        unsupported_rows=unsupported_rows,
    )


def _read_kmz_kml_text(source_path: Path) -> str:
    try:
        with zipfile.ZipFile(source_path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(_KML_SUFFIX)]
            doc_kml_candidates = [name for name in names if Path(name).name.lower() == "doc.kml"]
            if doc_kml_candidates:
                selected_name = sorted(doc_kml_candidates)[0]
            elif len(names) == 1:
                selected_name = names[0]
            elif not names:
                raise ValueError("KMZ file does not contain a KML file.")
            else:
                raise ValueError("KMZ file contains multiple KML files and no doc.kml.")
            return archive.read(selected_name).decode("utf-8")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid KMZ file: {source_path}") from exc


def _resolve_scope_name(*, list_name: str, document_name: str, my_maps_file: str) -> str:
    for candidate in (list_name, document_name, Path(my_maps_file).expanduser().stem):
        normalized = " ".join(candidate.split())
        if normalized:
            return normalized
    return "Imported My Maps"


def _safe_scope(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "imported"


def _derive_name_aliases(name: str) -> tuple[str, ...]:
    normalized_name = " ".join(name.split()).strip()
    if not normalized_name:
        return ()

    candidates: list[str] = []
    without_parenthetical = " ".join(
        _NAME_PARENTHETICAL_SEGMENT_PATTERN.sub(" ", normalized_name).split()
    )
    _append_unique_alias(candidates, without_parenthetical, normalized_name)

    separator_parts = [
        part.strip()
        for part in _NAME_ALIAS_SEPARATOR_PATTERN.split(without_parenthetical)
        if part.strip()
    ]
    if len(separator_parts) >= 2:
        _append_unique_alias(candidates, " ".join(separator_parts), normalized_name)
        last_part = separator_parts[-1]
        if len(last_part) >= 4:
            _append_unique_alias(candidates, last_part, normalized_name)

    return tuple(candidates)


def _append_unique_alias(candidates: list[str], alias: str, original_name: str) -> None:
    normalized_alias = " ".join(alias.split()).strip()
    if (
        not normalized_alias
        or normalized_alias == original_name
        or len(normalized_alias) < 3
        or normalized_alias in candidates
    ):
        return
    candidates.append(normalized_alias)


def _local_name(element: ElementTree.Element) -> str:
    tag = str(element.tag)
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _iter_descendants_by_local(
    element: ElementTree.Element,
    local_name: str,
) -> list[ElementTree.Element]:
    return [candidate for candidate in element.iter() if _local_name(candidate) == local_name]


def _find_first_descendant(
    element: ElementTree.Element,
    local_name: str,
) -> ElementTree.Element | None:
    for candidate in element.iter():
        if candidate is not element and _local_name(candidate) == local_name:
            return candidate
    return None


def _find_direct_child(
    element: ElementTree.Element | None,
    local_name: str,
) -> ElementTree.Element | None:
    if element is None:
        return None
    for child in list(element):
        if _local_name(child) == local_name:
            return child
    return None


def _element_text(element: ElementTree.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return " ".join(element.text.split()).strip()


def _parse_point_coordinates(coordinates_text: str) -> tuple[float, float] | None:
    first_tuple = coordinates_text.strip().split()[0] if coordinates_text.strip() else ""
    coordinate_parts = first_tuple.split(",")
    if len(coordinate_parts) < 2:
        return None
    try:
        longitude = float(coordinate_parts[0])
        latitude = float(coordinate_parts[1])
    except ValueError:
        return None
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        return None
    return longitude, latitude


def _extract_point_coordinates(point: ElementTree.Element | None) -> tuple[float, float] | None:
    if point is None:
        return None
    coordinates_text = _element_text(_find_first_descendant(point, "coordinates"))
    return _parse_point_coordinates(coordinates_text)


def _extract_extended_data(placemark: ElementTree.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    extended_data = _find_first_descendant(placemark, "ExtendedData")
    if extended_data is None:
        return values

    for data in _iter_descendants_by_local(extended_data, "Data"):
        key = _normalize_extended_key(str(data.attrib.get("name", "")))
        value = _element_text(_find_direct_child(data, "value"))
        if key and value:
            values[key] = value
    for simple_data in _iter_descendants_by_local(extended_data, "SimpleData"):
        key = _normalize_extended_key(str(simple_data.attrib.get("name", "")))
        value = _element_text(simple_data)
        if key and value:
            values[key] = value
    return values


def _normalize_extended_key(value: str) -> str:
    return "".join(character for character in value.strip().casefold() if character.isalnum())


def _first_extended_value(values: dict[str, str], keys: frozenset[str]) -> str:
    normalized_keys = {_normalize_extended_key(key) for key in keys}
    for key, value in values.items():
        if key in normalized_keys:
            return value
    return ""


def _strip_html(value: str) -> str:
    unescaped = html.unescape(value)
    without_tags = re.sub(r"<[^>]+>", " ", unescaped)
    return " ".join(without_tags.split()).strip()


def _format_coordinate_query(*, latitude: float, longitude: float) -> str:
    return f"{_format_coordinate_part(latitude)},{_format_coordinate_part(longitude)}"


def _format_coordinate_part(value: float) -> str:
    return f"{value:.7f}".rstrip("0").rstrip(".")
