"""Tests for Google My Maps KML/KMZ source adapter."""

import tempfile
import unittest
import zipfile
from pathlib import Path

from michelin_scraper.application.source_models import SourceRunHandlers
from michelin_scraper.application.sync_models import ScrapeSyncCommand
from michelin_scraper.sources.my_maps import MyMapsSourceAdapter, parse_my_maps_file, parse_my_maps_kml_text


class _NoOpOutput:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)


class _NoOpReporter:
    def update(self, message: str, progress: float | None = None) -> None:
        del message, progress

    def log(self, message: str) -> None:
        del message

    def finish(self, message: str | None = None) -> None:
        del message


_KML_TEXT = """\
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Taipei Food Map</name>
    <Placemark>
      <name>Alpha Cafe</name>
      <description><![CDATA[<b>Good coffee</b><br>Window seats]]></description>
      <address>1 Test Road, Taipei</address>
      <ExtendedData>
        <Data name="City"><value>Taipei</value></Data>
      </ExtendedData>
      <Point><coordinates>121.5654,25.0330,0</coordinates></Point>
    </Placemark>
    <Placemark>
      <name>Beta Noodles</name>
      <description>Soup</description>
      <ExtendedData>
        <Data name="formatted_address"><value>2 Test Road</value></Data>
        <SchemaData>
          <SimpleData name="city">New Taipei</SimpleData>
        </SchemaData>
      </ExtendedData>
      <Point><coordinates>121.5000,25.0500</coordinates></Point>
    </Placemark>
    <Placemark>
      <name>Coordinate Fallback</name>
      <Point><coordinates>121.1,25.2</coordinates></Point>
    </Placemark>
    <Placemark>
      <name>Walking Route</name>
      <LineString><coordinates>121.0,25.0 121.1,25.1</coordinates></LineString>
    </Placemark>
    <Placemark>
      <Point><coordinates>121.2,25.3</coordinates></Point>
    </Placemark>
    <Placemark>
      <name>Bad Coordinate</name>
      <Point><coordinates>bad</coordinates></Point>
    </Placemark>
  </Document>
</kml>
"""

_ADDRESS_ONLY_KML_TEXT = """\
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Address Only Map</name>
    <Placemark>
      <name>Alpha Shop</name>
      <address>Alpha Shop verbose address text</address>
      <description><![CDATA[地址: verbose<br>電話: 02 1234 5678]]></description>
      <ExtendedData>
        <Data name="地址"><value>100臺北市中正區測試路1號</value></Data>
        <Data name="地區"><value>台北</value></Data>
      </ExtendedData>
    </Placemark>
    <Placemark>
      <name>Beta Shop</name>
      <address>200基隆市測試路2號</address>
    </Placemark>
    <Placemark>
      <name>No Address</name>
    </Placemark>
    <Placemark>
      <address>300新竹市測試路3號</address>
    </Placemark>
  </Document>
</kml>
"""


class MyMapsSourceAdapterTests(unittest.TestCase):
    def test_parse_kml_namespaces_and_maps_placemark_fields(self) -> None:
        result = parse_my_maps_kml_text(_KML_TEXT)

        self.assertEqual(result.document_name, "Taipei Food Map")
        self.assertEqual(result.unsupported_rows, 1)
        self.assertEqual(result.skipped_rows, 2)
        self.assertEqual(len(result.rows), 3)
        first = result.rows[0]
        self.assertEqual(first["Name"], "Alpha Cafe")
        self.assertEqual(first["Description"], "Good coffee Window seats")
        self.assertEqual(first["Address"], "1 Test Road, Taipei")
        self.assertEqual(first["City"], "Taipei")
        self.assertEqual(first["Latitude"], 25.033)
        self.assertEqual(first["Longitude"], 121.5654)
        second = result.rows[1]
        self.assertEqual(second["Address"], "2 Test Road")
        self.assertEqual(second["City"], "New Taipei")
        third = result.rows[2]
        self.assertEqual(third["Address"], "25.2,121.1")

    def test_parse_address_only_placemarks_with_chinese_extended_data(self) -> None:
        result = parse_my_maps_kml_text(_ADDRESS_ONLY_KML_TEXT)

        self.assertEqual(result.document_name, "Address Only Map")
        self.assertEqual(result.unsupported_rows, 1)
        self.assertEqual(result.skipped_rows, 1)
        self.assertEqual(len(result.rows), 2)
        first = result.rows[0]
        self.assertEqual(first["Name"], "Alpha Shop")
        self.assertEqual(first["Address"], "100臺北市中正區測試路1號")
        self.assertEqual(first["City"], "台北")
        self.assertEqual(first["Description"], "地址: verbose 電話: 02 1234 5678")
        self.assertNotIn("Latitude", first)
        self.assertNotIn("Longitude", first)
        second = result.rows[1]
        self.assertEqual(second["Name"], "Beta Shop")
        self.assertEqual(second["Address"], "200基隆市測試路2號")

    def test_parse_kml_adds_conservative_name_aliases_for_source_matching(self) -> None:
        kml_text = """\
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Alias Map</name>
    <Placemark>
      <name>饅頭達人｜金獅湖肉包</name>
      <address>807高雄市三民區鼎金後路45號</address>
    </Placemark>
  </Document>
</kml>
"""

        result = parse_my_maps_kml_text(kml_text)

        self.assertEqual(result.rows[0]["Aliases"], ("饅頭達人 金獅湖肉包", "金獅湖肉包"))

    def test_parse_kmz_prefers_doc_kml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            kmz_path = Path(temp_dir) / "map.kmz"
            with zipfile.ZipFile(kmz_path, "w") as archive:
                archive.writestr("ignored.kml", _KML_TEXT.replace("Taipei Food Map", "Ignored"))
                archive.writestr("doc.kml", _KML_TEXT)

            result = parse_my_maps_file(str(kmz_path))

        self.assertEqual(result.document_name, "Taipei Food Map")
        self.assertEqual(len(result.rows), 3)

    def test_parse_kmz_accepts_single_non_doc_kml_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            kmz_path = Path(temp_dir) / "map.kmz"
            with zipfile.ZipFile(kmz_path, "w") as archive:
                archive.writestr("exports/map-data.kml", _KML_TEXT)

            result = parse_my_maps_file(str(kmz_path))

        self.assertEqual(result.document_name, "Taipei Food Map")
        self.assertEqual(len(result.rows), 3)

    def test_parse_kmz_fails_when_multiple_kml_files_have_no_doc_kml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            kmz_path = Path(temp_dir) / "map.kmz"
            with zipfile.ZipFile(kmz_path, "w") as archive:
                archive.writestr("a.kml", _KML_TEXT)
                archive.writestr("b.kml", _KML_TEXT)

            with self.assertRaisesRegex(ValueError, "multiple KML files"):
                parse_my_maps_file(str(kmz_path))

    def test_prepare_uses_list_name_override_and_path_free_checkpoint_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            kml_path = Path(temp_dir) / "private-map.kml"
            kml_path.write_text(_KML_TEXT, encoding="utf-8")
            command = ScrapeSyncCommand(
                target="",
                google_user_data_dir="/tmp/profile",
                levels=("imported",),
                source="my-maps",
                my_maps_file=str(kml_path),
                my_maps_list_name="Taipei Saved",
            )
            output = _NoOpOutput()

            plan = MyMapsSourceAdapter().prepare(command, output)

        self.assertEqual(plan.source_id, "my-maps")
        self.assertEqual(plan.scope_name, "Taipei Saved")
        self.assertEqual(plan.checkpoint_scope, "my-maps-taipei-saved")
        self.assertEqual(plan.start_url, "source://my-maps/my-maps-taipei-saved")
        self.assertNotIn(temp_dir, plan.checkpoint_scope)
        self.assertTrue(any("unsupported_without_address_or_point=1" in warning for warning in output.warnings))

    def test_run_streams_imported_bucket_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            kml_path = Path(temp_dir) / "private-map.kml"
            kml_path.write_text(_KML_TEXT, encoding="utf-8")
            command = ScrapeSyncCommand(
                target="",
                google_user_data_dir="/tmp/profile",
                levels=("imported",),
                source="my-maps",
                my_maps_file=str(kml_path),
            )
            adapter = MyMapsSourceAdapter()
            plan = adapter.prepare(command, _NoOpOutput())
            item_calls: list[tuple[str, str]] = []
            page_calls: list[tuple[int, int]] = []
            handlers = SourceRunHandlers(
                on_item=lambda _page, _total_pages, _expected, bucket, row: item_calls.append((bucket, row["Name"])),
                on_page=lambda page, _url, rows, _next, _next_page, _pages, _total: page_calls.append((page, len(rows))),
                on_interrupt=lambda *_args: None,
                progress_reporter=_NoOpReporter(),
                start_cursor=plan.start_url or "",
            )

            result = adapter.run(command=command, plan=plan, handlers=handlers)

        self.assertEqual(result.total_rows, 3)
        self.assertEqual(result.processed_pages, 1)
        self.assertEqual(result.skipped_rows, 2)
        self.assertEqual(result.unsupported_rows, 1)
        self.assertEqual(item_calls, [("imported", "Alpha Cafe"), ("imported", "Beta Noodles"), ("imported", "Coordinate Fallback")])
        self.assertEqual(page_calls, [(1, 3)])


if __name__ == "__main__":
    unittest.main()
