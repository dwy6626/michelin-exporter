# My Maps Note Formatting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple Google My Maps note formatting from Michelin note formatting, remove `updated ...` from My Maps notes, and add user-selectable My Maps description formatting with a first built-in `500bowls` preset.

**Architecture:** Keep source parsing and destination sync separate. The My Maps source adapter should preserve KML description data as raw HTML, parsed text, and parsed key/value fields; the sync writer should delegate note construction to a source-aware formatter instead of treating every row like a Michelin row.

**Tech Stack:** Python 3.14, Typer CLI, `xml.etree.ElementTree`, existing `beautifulsoup4`, `unittest`, `uv run`, ruff, basedpyright.

---

## 規格

### 背景

目前 `sync-my-maps` 會重用 Michelin 的 Google Maps note formatter。這造成兩個問題：

- My Maps 匯入也會被加上 Michelin sync metadata，例如 `updated 2026-07-04`。
- Google My Maps KML/KMZ 的 `<description>` 目前會被無差別壓成一行；但像 500 碗地圖這類欄位型 description，需要的是經過挑選與排序的一行 note，而不是原始欄位全部攤平。

這不是 My Maps 應該有的預設行為。每份自訂地圖的 description 格式都可能不同，因此 My Maps note formatting 必須可配置；500 碗只是第一個內建格式，不應寫死成唯一規則。

### 目標

- `sync-my-maps` 預設不再輸出 `updated ...` 或任何 Michelin 專用 header。
- My Maps note formatting 從 Michelin note formatting 拆開。
- My Maps parser 保留 KML description 的原始 HTML、純文字，以及可解析的 `key: value` 欄位。
- CLI 支援使用者選擇 My Maps note format。
- 第一版支援三種 My Maps note format：
  - `raw`：預設模式，使用 KML description 解析出的欄位，依原順序用 ` | ` 分隔，不加 updated date；若沒有可解析欄位才 fallback 到純文字 description。
  - `500bowls`：針對 500 碗地圖欄位輸出單行 pipe-separated note。
  - `template`：使用者提供自訂 template，使用 `{欄位名}` 引用 description 欄位的 value。

### 非目標

- 不做自動辨識地圖類型；使用者在 `sync-my-maps` 明確指定 `--note-format 500bowls` 才使用 500 碗格式。
- 不新增新的外部依賴；HTML 解析使用專案既有的 `beautifulsoup4`。
- 不設計完整 template language；只支援 `{欄位名}` value 替換、缺值 segment 略過，以及單行空白整理。
- 不改 Michelin sync note 格式；Michelin 仍維持現有 `年份 | 獎項 | 菜系 | updated date` 行為。

### CLI 行為

`sync-my-maps` 新增兩個選項。因為 subcommand 已經提供 My Maps 語境，option 名稱不要重複 `my-maps`：

```bash
--note-format raw|500bowls|template
--note-template "<template>"
```

預設：

```bash
--note-format raw
--note-template ""
```

規則：

- `raw` 不需要 template。
- `500bowls` 不需要 template。
- `template` 必須提供非空 `--note-template`，否則 CLI 直接報錯。
- 不支援的 format 值必須報錯，錯誤訊息列出允許值。
- 同樣命名原則適用於其他 source subcommand；如果未來 Michelin command 也開放 note 格式設定，也應使用 `--note-format`，不要使用 `--michelin-note-format`。

### Description 解析規格

My Maps parser 對每個 KML `Placemark` 產生 row 時，應包含：

- `DescriptionRawHtml`：原始 `<description>` 內容。
- `Description`：去 HTML 後的純文字，供沒有可解析欄位時的 `raw` fallback 使用。
- `DescriptionFields`：從 description 解析出的欄位 dict。

欄位解析規則：

- 使用 BeautifulSoup 解析 HTML。
- 優先從 `div`, `p`, `li`, `tr` 等結構化元素取文字。
- 每一行支援半形冒號 `:` 和全形冒號 `：`。
- `key` 和 `value` 都要 trim 並壓縮空白。
- 無法解析成欄位的文字保留在 `Description`，但不放入 `DescriptionFields`。

### 500 碗格式規格

`500bowls` preset 使用 `DescriptionFields`，輸出單行 note。Google Maps saved-place note 在列表與詳情頁讀取時單行更友善，因此 My Maps note formatter 不輸出換行。

```text
{總得碗數}碗 | {得獎菜色} | {菜系} | {推薦評審} | {地址} | {電話}
```

欄位規則：

- `{總得碗數}` 是 canonical 欄位名；為了容錯，也接受常見誤字 alias `{總得晚數}`。
- `地區` 與 `營業時間` 仍會被 parser 保留在 `DescriptionFields`，但 `500bowls` preset 不輸出它們。
- 缺值欄位直接略過該 segment，並清理多餘 separator。

例如只有 `得獎菜色` 和 `電話` 時，輸出：

```text
春捲 | 08 933 2520
```

### Template 格式規格

`template` mode 使用 `--note-template`，以 `{欄位名}` 取 value。placeholder 永遠只輸出 value，不會自動輸出 key label；如果使用者想要 key label，必須在 template 字面上自己寫。

```text
{總得碗數}碗 | {得獎菜色} | 菜系: {菜系} | 電話: {電話}
```

輸出規則：

- formatter 回傳單行 note。
- `{欄位名}` 只替換成欄位 value；例如 `{得獎菜色}` 會輸出 `春捲`，不會輸出 `得獎菜色: 春捲`。
- 如果需要 key，要在 template 裡手動加，例如 `菜系: {菜系}`。
- Template literal 若需要輸出 `{` 或 `}`，使用 `{{` 和 `}}` escape；例如 `{{店家}} {得獎菜色}` 會輸出 `{店家} 春捲`。
- 缺值 placeholder 所在的 `|` segment 會略過，避免留下 `地址:` 這類空 label。
- 替換後的 note 會 trim 並壓縮連續空白。
- 不支援條件式、迴圈、預設值語法。

### 特殊符號處理規格

Description 欄位值可能包含 `|`, `{`, `}`, 引號、反斜線、換行或其他特殊符號。Formatter 與 report writer 必須分層處理：

- Google Maps note 是純文字，不做 HTML escaping。
- Formatter 使用 ` | ` 作為欄位 separator，因此欄位 key/value 裡原本的半形 `|` 要轉成全形 `｜`，避免被誤認為 separator。
- 欄位 key/value 裡的 `{` 和 `}` 保留原字元；只有 template literal 裡要用 `{{` / `}}` 表示字面大括號。
- 欄位值與 raw fallback 裡的換行、tab、連續空白都壓成單一空白，維持單行 note。
- JSONL failure report 不手寫 JSON escaping；使用 `json.dumps(..., ensure_ascii=False)` 讓引號、反斜線等 JSON 特殊字元正確 escape，同時保留中文原文。

### 驗收標準

- My Maps raw note 不包含 `updated` 或 `更新`。
- My Maps raw note 若有解析欄位，使用 `key: value | key: value` 單行格式。
- My Maps 500bowls note 是單行 pipe-separated 格式。
- My Maps template note 能用 `{欄位名}` 替換 description 欄位。
- 缺少 description 欄位時不 crash；缺值 placeholder 所在 segment 會略過並整理多餘 separator。
- Description 特殊符號不會破壞 note separator、template parsing 或 JSONL failure report。
- Row sync 失敗時，failure summary 和 JSONL failure report 都包含該 row 原本要寫入的 note，方便使用者手動複製到已建立的 Google Maps 清單。
- JSONL failure report 使用 UTF-8 原文輸出 note，不把中文轉成 `\uXXXX` escape。
- Michelin note 測試維持既有 updated date 行為。
- README 和 `sync-my-maps --help` 都說明新的 note options。
- Targeted tests、full unittest、ruff、basedpyright 都通過。

## File Structure

- Modify: `michelin_scraper/application/sync_models.py`
  - Add generic note-format command fields and failure note text so CLI choices and manual-recovery data travel through the sync pipeline.
- Modify: `michelin_scraper/config/cli_options.py`
  - Add option constants for `--note-format` and `--note-template`.
- Modify: `michelin_scraper/entrypoints/cli.py`
  - Expose note-format options on `sync-my-maps`.
- Modify: `michelin_scraper/sources/my_maps.py`
  - Preserve raw description HTML and parse description fields using BeautifulSoup.
- Create: `michelin_scraper/sources/my_maps_note_formatter.py`
  - Own My Maps note formatting modes: `raw`, `500bowls`, and `template`.
- Modify: `michelin_scraper/adapters/google_maps_sync_writer.py`
  - Select Michelin note formatting only for Michelin rows and My Maps note formatting for My Maps rows.
- Modify: `michelin_scraper/application/sync_use_case.py`
  - Pass source and note-format settings into the writer and serialize failure note text into JSONL reports.
- Modify: `michelin_scraper/output/console_sync_presenter.py`
  - Show failure note text under row failures for manual copy/paste recovery.
- Modify: `README.md`
  - Document My Maps note formatting behavior and examples.
- Modify: `tests/test_my_maps_source_adapter.py`
  - Cover raw HTML preservation and field parsing.
- Create: `tests/test_my_maps_note_formatter.py`
  - Cover raw, 500bowls, template, and missing-field behavior.
- Modify: `tests/test_google_maps_sync_writer.py`
  - Cover no `updated ...` for My Maps, unchanged Michelin note behavior, and failed items carrying note text.
- Modify: `tests/test_sync_use_case.py`
  - Cover JSONL failure reports including `note_text`.
- Modify: `tests/test_console_sync_presenter.py` or existing presenter test module
  - Cover row failure display including copyable note text.
- Modify: `tests/test_cli_maps_help.py`
  - Cover new CLI options in `sync-my-maps --help`.
- Modify: `tests/test_generic_source_sync_use_case.py`
  - Cover command option propagation for My Maps note formatting.

## Task 1: Add Command Fields and CLI Constants

**Files:**
- Modify: `michelin_scraper/application/sync_models.py`
- Modify: `michelin_scraper/config/cli_options.py`

- [ ] **Step 1: Add failing command-model expectation**

Add this test to `tests/test_generic_source_sync_use_case.py` near the other My Maps command tests:

```python
def test_scrape_sync_command_defaults_note_format_to_raw(self) -> None:
    command = ScrapeSyncCommand(
        target="",
        google_user_data_dir="/tmp/profile",
        levels=("imported",),
        source="my-maps",
        my_maps_file="/tmp/map.kml",
    )

    self.assertEqual(command.note_format, "raw")
    self.assertEqual(command.note_template, "")
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
rtk uv run python -m unittest tests.test_generic_source_sync_use_case.GenericSourceSyncUseCaseTests.test_scrape_sync_command_defaults_note_format_to_raw
```

Expected: FAIL with `AttributeError` or dataclass constructor/model field mismatch for `note_format`.

- [ ] **Step 3: Add command fields**

In `michelin_scraper/application/sync_models.py`, add fields after `my_maps_list_name`:

```python
    note_format: str = "raw"
    note_template: str = ""
```

- [ ] **Step 4: Add CLI option constants**

In `michelin_scraper/config/cli_options.py`, add:

```python
NOTE_FORMAT_OPTION_FLAGS = ("--note-format",)
NOTE_TEMPLATE_OPTION_FLAGS = ("--note-template",)
```

- [ ] **Step 5: Verify Task 1**

Run:

```bash
rtk uv run python -m unittest tests.test_generic_source_sync_use_case.GenericSourceSyncUseCaseTests.test_scrape_sync_command_defaults_note_format_to_raw
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
rtk git add michelin_scraper/application/sync_models.py michelin_scraper/config/cli_options.py tests/test_generic_source_sync_use_case.py
rtk git commit -m "Add My Maps note format command fields"
```

## Task 2: Preserve and Parse My Maps Description Data

**Files:**
- Modify: `michelin_scraper/sources/my_maps.py`
- Modify: `tests/test_my_maps_source_adapter.py`

- [ ] **Step 1: Add failing parser test for description fields**

Add this test to `tests/test_my_maps_source_adapter.py`:

```python
def test_parse_kml_preserves_description_html_and_extracts_key_value_fields(self) -> None:
    kml_text = """\
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>500 Bowls</name>
    <Placemark>
      <name>Alpha Spring Rolls</name>
      <description><![CDATA[
        <div>總得碗數: 1</div>
        <div>得獎菜色: 春捲</div>
        <div>地區: 台東</div>
        <div>菜系: 台式</div>
        <div>推薦評審: 蔣勳</div>
        <div>地址: 950臺東縣台東市正氣路453-1號</div>
        <div>營業時間: 08:30–19:30</div>
        <div>電話: 08 933 2520</div>
      ]]></description>
      <address>950臺東縣台東市正氣路453-1號</address>
    </Placemark>
  </Document>
</kml>
"""

    result = parse_my_maps_kml_text(kml_text)

    row = result.rows[0]
    self.assertIn("<div>總得碗數: 1</div>", row["DescriptionRawHtml"])
    self.assertEqual(row["DescriptionFields"]["總得碗數"], "1")
    self.assertEqual(row["DescriptionFields"]["得獎菜色"], "春捲")
    self.assertEqual(row["DescriptionFields"]["電話"], "08 933 2520")
    self.assertIn("總得碗數: 1", row["Description"])
```

- [ ] **Step 2: Run the failing parser test**

Run:

```bash
rtk uv run python -m unittest tests.test_my_maps_source_adapter.MyMapsSourceAdapterTests.test_parse_kml_preserves_description_html_and_extracts_key_value_fields
```

Expected: FAIL because `DescriptionRawHtml` and `DescriptionFields` do not exist.

- [ ] **Step 3: Implement raw HTML extraction**

In `michelin_scraper/sources/my_maps.py`, import BeautifulSoup:

```python
from bs4 import BeautifulSoup
```

Add helper near `_element_text`:

```python
def _element_raw_text(element: ElementTree.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return element.text.strip()
```

In `parse_my_maps_kml_text`, before constructing `row`, assign:

```python
        description_html = _element_raw_text(_find_direct_child(placemark, "description"))
        description_text = _strip_html(description_html)
        description_fields = _extract_description_fields(description_html)
```

Then change the row fields:

```python
        row: dict[str, Any] = {
            "Name": name,
            "Description": description_text,
            "DescriptionRawHtml": description_html,
            "DescriptionFields": description_fields,
            "Address": address,
            "City": city,
        }
```

- [ ] **Step 4: Implement BeautifulSoup field extraction**

Add helpers near `_strip_html`:

```python
def _extract_description_fields(value: str) -> dict[str, str]:
    if not value:
        return {}
    soup = BeautifulSoup(value, "html.parser")
    lines: list[str] = []
    for element in soup.find_all(["div", "p", "li", "tr"]):
        text = " ".join(element.get_text(" ", strip=True).split())
        if text:
            lines.append(text)
    if not lines:
        lines = [
            " ".join(line.split())
            for line in soup.get_text("\n", strip=True).splitlines()
            if line.strip()
        ]

    fields: dict[str, str] = {}
    for line in lines:
        key, separator, raw_value = line.partition(":")
        if not separator:
            key, separator, raw_value = line.partition("：")
        normalized_key = " ".join(key.split()).strip()
        normalized_value = " ".join(raw_value.split()).strip()
        if normalized_key and normalized_value:
            fields[normalized_key] = normalized_value
    return fields
```

- [ ] **Step 5: Verify Task 2**

Run:

```bash
rtk uv run python -m unittest tests.test_my_maps_source_adapter.MyMapsSourceAdapterTests.test_parse_kml_preserves_description_html_and_extracts_key_value_fields
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
rtk git add michelin_scraper/sources/my_maps.py tests/test_my_maps_source_adapter.py
rtk git commit -m "Parse My Maps description fields"
```

## Task 3: Add My Maps Note Formatter Module

**Files:**
- Create: `michelin_scraper/sources/my_maps_note_formatter.py`
- Create: `tests/test_my_maps_note_formatter.py`

- [ ] **Step 1: Add formatter tests**

Create `tests/test_my_maps_note_formatter.py`:

```python
"""Tests for Google My Maps note formatting."""

import unittest

from michelin_scraper.sources.my_maps_note_formatter import build_my_maps_note_text


class MyMapsNoteFormatterTests(unittest.TestCase):
    def test_raw_format_uses_description_without_updated_date(self) -> None:
        note = build_my_maps_note_text(
            {
                "Description": "總得碗數: 1 得獎菜色: 春捲",
                "DescriptionFields": {
                    "總得碗數": "1",
                    "得獎菜色": "春捲",
                    "地區": "台東",
                },
            },
            note_format="raw",
            note_template="",
        )

        self.assertEqual(note, "總得碗數: 1 | 得獎菜色: 春捲 | 地區: 台東")
        self.assertNotIn("updated", note)
        self.assertNotIn("更新", note)

    def test_raw_format_falls_back_to_description_when_fields_are_missing(self) -> None:
        note = build_my_maps_note_text(
            {"Description": "Free-form note"},
            note_format="raw",
            note_template="",
        )

        self.assertEqual(note, "Free-form note")

    def test_500bowls_format_outputs_readable_single_line_note(self) -> None:
        note = build_my_maps_note_text(
            {
                "DescriptionFields": {
                    "總得碗數": "1",
                    "得獎菜色": "春捲",
                    "地區": "台東",
                    "菜系": "台式",
                    "推薦評審": "蔣勳",
                    "地址": "950臺東縣台東市正氣路453-1號",
                    "營業時間": "08:30–19:30",
                    "電話": "08 933 2520",
                }
            },
            note_format="500bowls",
            note_template="",
        )

        self.assertEqual(
            note,
            "1碗 | 春捲 | 台式 | 蔣勳 | 950臺東縣台東市正氣路453-1號 | 08 933 2520",
        )

    def test_500bowls_format_omits_missing_segments(self) -> None:
        note = build_my_maps_note_text(
            {"DescriptionFields": {"得獎菜色": "春捲", "電話": "08 933 2520"}},
            note_format="500bowls",
            note_template="",
        )

        self.assertEqual(note, "春捲 | 08 933 2520")

    def test_500bowls_format_accepts_common_bowl_count_typo_alias(self) -> None:
        note = build_my_maps_note_text(
            {"DescriptionFields": {"總得晚數": "1", "得獎菜色": "春捲"}},
            note_format="500bowls",
            note_template="",
        )

        self.assertEqual(note, "1碗 | 春捲")

    def test_template_format_replaces_fields_with_values_only(self) -> None:
        note = build_my_maps_note_text(
            {
                "DescriptionFields": {
                    "總得碗數": "1",
                    "得獎菜色": "春捲",
                    "地區": "台東",
                    "菜系": "台式",
                    "電話": "08 933 2520",
                }
            },
            note_format="template",
            note_template="{總得碗數}碗 | {得獎菜色} | 菜系: {菜系} | 地址: {地址} | 電話: {電話}",
        )

        self.assertEqual(note, "1碗 | 春捲 | 菜系: 台式 | 電話: 08 933 2520")

    def test_formatter_escapes_separator_and_preserves_literal_braces(self) -> None:
        note = build_my_maps_note_text(
            {
                "DescriptionFields": {
                    "得獎菜色": "春捲 | 韭菜盒",
                    "推薦評審": "蔣勳\n特別推薦",
                }
            },
            note_format="template",
            note_template="{{店家}} {得獎菜色} | 推薦: {推薦評審}",
        )

        self.assertEqual(note, "{店家} 春捲 ｜ 韭菜盒 | 推薦: 蔣勳 特別推薦")

    def test_unknown_format_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported My Maps note format"):
            build_my_maps_note_text({}, note_format="unknown", note_template="")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the failing formatter tests**

Run:

```bash
rtk uv run python -m unittest tests.test_my_maps_note_formatter
```

Expected: FAIL because `michelin_scraper.sources.my_maps_note_formatter` does not exist.

- [ ] **Step 3: Implement formatter module**

Create `michelin_scraper/sources/my_maps_note_formatter.py`:

```python
"""Google My Maps note formatting."""

import re
from collections.abc import Mapping
from typing import Any

MY_MAPS_NOTE_FORMAT_RAW = "raw"
MY_MAPS_NOTE_FORMAT_500BOWLS = "500bowls"
MY_MAPS_NOTE_FORMAT_TEMPLATE = "template"
MY_MAPS_NOTE_FORMAT_CHOICES = (
    MY_MAPS_NOTE_FORMAT_RAW,
    MY_MAPS_NOTE_FORMAT_500BOWLS,
    MY_MAPS_NOTE_FORMAT_TEMPLATE,
)

_TEMPLATE_FIELD_PATTERN = re.compile(r"\{([^{}]+)\}")
_BOWLS_500_BOWL_COUNT_KEYS = ("總得碗數", "總得晚數")
_MISSING_TEMPLATE_FIELD_MARKER = "\0missing-field\0"
_ESCAPED_OPEN_BRACE_MARKER = "\0open-brace\0"
_ESCAPED_CLOSE_BRACE_MARKER = "\0close-brace\0"


def build_my_maps_note_text(
    row: Mapping[str, Any],
    *,
    note_format: str,
    note_template: str,
) -> str:
    normalized_format = note_format.strip().lower() or MY_MAPS_NOTE_FORMAT_RAW
    if normalized_format == MY_MAPS_NOTE_FORMAT_RAW:
        fields = _description_fields(row)
        if fields:
            return _format_raw_fields_note(fields)
        return _normalize_note_text(row.get("Description", ""))
    if normalized_format == MY_MAPS_NOTE_FORMAT_500BOWLS:
        return _format_500bowls_note(_description_fields(row))
    if normalized_format == MY_MAPS_NOTE_FORMAT_TEMPLATE:
        return _format_template_note(_description_fields(row), note_template)
    allowed = ", ".join(MY_MAPS_NOTE_FORMAT_CHOICES)
    raise ValueError(f"Unsupported My Maps note format: {note_format!r}. Allowed values: {allowed}.")


def _description_fields(row: Mapping[str, Any]) -> Mapping[str, str]:
    value = row.get("DescriptionFields", {})
    if not isinstance(value, Mapping):
        return {}
    fields: dict[str, str] = {}
    for key, raw_field_value in value.items():
        normalized_key = _normalize_note_text(key)
        normalized_value = _sanitize_note_segment(raw_field_value)
        if normalized_key and normalized_value:
            fields[normalized_key] = normalized_value
    return fields


def _format_500bowls_note(fields: Mapping[str, str]) -> str:
    bowl_count = _first_field_value(fields, _BOWLS_500_BOWL_COUNT_KEYS)
    segments = [
        f"{bowl_count}碗" if bowl_count else "",
        fields.get("得獎菜色", ""),
        fields.get("菜系", ""),
        fields.get("推薦評審", ""),
        fields.get("地址", ""),
        fields.get("電話", ""),
    ]
    return _join_note_segments(segments)


def _format_raw_fields_note(fields: Mapping[str, str]) -> str:
    return _join_note_segments([f"{key}: {value}" for key, value in fields.items()])


def _format_template_note(fields: Mapping[str, str], note_template: str) -> str:
    if not note_template:
        return ""
    escaped_template = (
        note_template
        .replace("{{", _ESCAPED_OPEN_BRACE_MARKER)
        .replace("}}", _ESCAPED_CLOSE_BRACE_MARKER)
    )

    def replace_field(match: re.Match[str]) -> str:
        field_name = _normalize_note_text(match.group(1))
        return fields.get(field_name, _MISSING_TEMPLATE_FIELD_MARKER)

    rendered = _TEMPLATE_FIELD_PATTERN.sub(replace_field, escaped_template)
    rendered = (
        rendered
        .replace(_ESCAPED_OPEN_BRACE_MARKER, "{")
        .replace(_ESCAPED_CLOSE_BRACE_MARKER, "}")
    )
    return _cleanup_template_note(rendered)


def _first_field_value(fields: Mapping[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = fields.get(key, "")
        if value:
            return value
    return ""


def _join_note_segments(values: list[str]) -> str:
    return " | ".join(value for value in (_sanitize_note_segment(item) for item in values) if value)


def _cleanup_template_note(value: str) -> str:
    segments = [
        segment
        for segment in value.split("|")
        if _MISSING_TEMPLATE_FIELD_MARKER not in segment
    ]
    return _join_note_segments(segments)


def _normalize_note_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _sanitize_note_segment(value: Any) -> str:
    return _normalize_note_text(value).replace("|", "｜")
```

- [ ] **Step 4: Verify Task 3**

Run:

```bash
rtk uv run python -m unittest tests.test_my_maps_note_formatter
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
rtk git add michelin_scraper/sources/my_maps_note_formatter.py tests/test_my_maps_note_formatter.py
rtk git commit -m "Add My Maps note formatter"
```

## Task 4: Make Sync Writer Source-Aware for Notes

**Files:**
- Modify: `michelin_scraper/adapters/google_maps_sync_writer.py`
- Modify: `michelin_scraper/application/sync_use_case.py`
- Modify: `tests/test_google_maps_sync_writer.py`
- Modify: `tests/test_generic_source_sync_use_case.py`

- [ ] **Step 1: Add failing writer test for My Maps raw note**

Add this test to `tests/test_google_maps_sync_writer.py`:

```python
async def test_sync_rows_by_level_uses_my_maps_raw_note_without_updated_date(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        fake_driver = FakeDriver()
        fake_driver.openable_lists.add("My Maps|imported")
        writer = GoogleMapsSyncWriter(
            user_data_dir=Path(temp_dir) / "profile",
            headless=True,
            sync_delay_seconds=0,
            max_save_retries=0,
            list_name_template="{scope}|{level}",
            list_name_prefix="",
            on_missing_list="stop",
            ignore_existing_lists_check=True,
            source="my-maps",
            note_format="raw",
            note_template="",
            updated_on="2026-07-04",
            driver=fake_driver,
        )
        await writer.initialize_run(scope_name="My Maps", level_slugs=("imported",))
        fake_driver.candidates_by_query = {
            "Alpha Taitung": PlaceCandidate(
                name="Alpha",
                address="Taitung",
                category="Restaurant",
            )
        }

        result = await writer.sync_rows_by_level(
            {
                "imported": [
                    {
                        "Name": "Alpha",
                        "City": "Taitung",
                        "Description": "總得碗數: 1 得獎菜色: 春捲",
                        "DescriptionFields": {
                            "總得碗數": "1",
                            "得獎菜色": "春捲",
                        },
                    }
                ]
            }
        )

        self.assertEqual(result.added_count_by_level["imported"], 1)
        self.assertEqual(fake_driver.save_calls[0][1], "總得碗數: 1 | 得獎菜色: 春捲")
        self.assertNotIn("updated 2026-07-04", fake_driver.save_calls[0][1])
```

- [ ] **Step 2: Add failing writer test for 500bowls preset**

Add this test to `tests/test_google_maps_sync_writer.py`:

```python
async def test_sync_rows_by_level_uses_my_maps_500bowls_note_format(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        fake_driver = FakeDriver()
        fake_driver.openable_lists.add("My Maps|imported")
        writer = GoogleMapsSyncWriter(
            user_data_dir=Path(temp_dir) / "profile",
            headless=True,
            sync_delay_seconds=0,
            max_save_retries=0,
            list_name_template="{scope}|{level}",
            list_name_prefix="",
            on_missing_list="stop",
            ignore_existing_lists_check=True,
            source="my-maps",
            note_format="500bowls",
            note_template="",
            updated_on="2026-07-04",
            driver=fake_driver,
        )
        await writer.initialize_run(scope_name="My Maps", level_slugs=("imported",))
        fake_driver.candidates_by_query = {
            "Alpha Taitung": PlaceCandidate(
                name="Alpha",
                address="Taitung",
                category="Restaurant",
            )
        }

        await writer.sync_rows_by_level(
            {
                "imported": [
                    {
                        "Name": "Alpha",
                        "City": "Taitung",
                        "DescriptionFields": {
                            "總得碗數": "1",
                            "得獎菜色": "春捲",
                            "電話": "08 933 2520",
                        },
                    }
                ]
            }
        )

        self.assertEqual(fake_driver.save_calls[0][1], "1碗 | 春捲 | 08 933 2520")
```

- [ ] **Step 3: Run failing writer tests**

Run:

```bash
rtk uv run python -m unittest tests.test_google_maps_sync_writer.GoogleMapsSyncWriterTests.test_sync_rows_by_level_uses_my_maps_raw_note_without_updated_date tests.test_google_maps_sync_writer.GoogleMapsSyncWriterTests.test_sync_rows_by_level_uses_my_maps_500bowls_note_format
```

Expected: FAIL because `GoogleMapsSyncWriter.__init__` does not accept `source`, `note_format`, or `note_template`.

- [ ] **Step 4: Add writer constructor fields**

In `michelin_scraper/adapters/google_maps_sync_writer.py`, import constants:

```python
from ..application.sync_models import SOURCE_MICHELIN, SOURCE_MY_MAPS
from ..sources.my_maps_note_formatter import build_my_maps_note_text
```

If `SyncBatchResult`, `SyncItemFailure`, and `SyncRowResult` are already imported from the same module, merge imports instead of duplicating import statements.

Add constructor parameters after `probe_only`:

```python
        source: str = SOURCE_MICHELIN,
        note_format: str = "raw",
        note_template: str = "",
```

Store them:

```python
        self._source = source
        self._note_format = note_format
        self._note_template = note_template
```

- [ ] **Step 5: Add source-aware note helper**

In `GoogleMapsSyncWriter`, add a private method near `_sync_one_row_without_retry`:

```python
    def _build_note_text(self, row: dict[str, Any], *, level_slug: str) -> str:
        if self._source == SOURCE_MY_MAPS:
            return build_my_maps_note_text(
                row,
                note_format=self._note_format,
                note_template=self._note_template,
            )
        return _build_place_note_text(
            row,
            level_slug=level_slug,
            language=self._language,
            updated_on=self._updated_on,
        )
```

In `_sync_one_row`, after the checkpoint skip block and before the retry loop, compute the row note once:

```python
        note_text = self._build_note_text(row, level_slug=level_slug)
```

Pass it into `_sync_one_row_without_retry(...)`:

```python
                    note_text=note_text,
```

Update `_sync_one_row_without_retry` to accept the already-built note:

```python
        note_text: str,
```

Then remove its existing local `_build_place_note_text(...)` assignment. All save calls in `_sync_one_row_without_retry` should use the `note_text` parameter, so retry-wrapper failures and helper-level failures report the exact same note text that would be written to Google Maps.

- [ ] **Step 6: Pass source settings from use case**

In `michelin_scraper/application/sync_use_case.py`, find `GoogleMapsSyncWriter(...)` construction and pass:

```python
            source=command.source,
            note_format=command.note_format,
            note_template=command.note_template,
```

- [ ] **Step 7: Update test writer helper defaults**

In `tests/test_google_maps_sync_writer.py`, update helper constructors if needed:

```python
            source=source,
            note_format=note_format,
            note_template=note_template,
```

Use defaults:

```python
        source: str = SOURCE_MICHELIN,
        note_format: str = "raw",
        note_template: str = "",
```

- [ ] **Step 8: Verify Task 4**

Run:

```bash
rtk uv run python -m unittest tests.test_google_maps_sync_writer.GoogleMapsSyncWriterTests.test_sync_rows_by_level_uses_my_maps_raw_note_without_updated_date tests.test_google_maps_sync_writer.GoogleMapsSyncWriterTests.test_sync_rows_by_level_uses_my_maps_500bowls_note_format tests.test_google_maps_sync_writer.GoogleMapsSyncWriterTests.test_build_place_note_text_uses_english_locale_format tests.test_google_maps_sync_writer.GoogleMapsSyncWriterTests.test_build_place_note_text_uses_traditional_chinese_locale_format
```

Expected: PASS. The Michelin tests should still include `updated ...`; My Maps tests should not.

- [ ] **Step 9: Commit Task 4**

```bash
rtk git add michelin_scraper/adapters/google_maps_sync_writer.py michelin_scraper/application/sync_use_case.py tests/test_google_maps_sync_writer.py tests/test_generic_source_sync_use_case.py
rtk git commit -m "Use source-aware Maps note formatting"
```

## Task 5: Include Copyable Note Text in Failure Reporting

**Files:**
- Modify: `michelin_scraper/application/sync_models.py`
- Modify: `michelin_scraper/adapters/google_maps_sync_writer.py`
- Modify: `michelin_scraper/application/sync_use_case.py`
- Modify: `michelin_scraper/output/console_sync_presenter.py`
- Modify: `tests/test_google_maps_sync_writer.py`
- Modify: `tests/test_sync_use_case.py`
- Create: `tests/test_console_sync_presenter.py`

- [ ] **Step 1: Add failing writer test for failed item note text**

Add this test to `tests/test_google_maps_sync_writer.py`:

```python
async def test_sync_rows_by_level_failed_item_includes_note_text_for_manual_recovery(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        fake_driver = FakeDriver()
        fake_driver.openable_lists.add("My Maps|imported")
        writer = GoogleMapsSyncWriter(
            user_data_dir=Path(temp_dir) / "profile",
            headless=True,
            sync_delay_seconds=0,
            max_save_retries=0,
            list_name_template="{scope}|{level}",
            list_name_prefix="",
            on_missing_list="continue",
            ignore_existing_lists_check=True,
            source="my-maps",
            note_format="500bowls",
            note_template="",
            driver=fake_driver,
        )
        await writer.initialize_run(scope_name="My Maps", level_slugs=("imported",))
        fake_driver.candidates_by_query = {}

        result = await writer.sync_rows_by_level(
            {
                "imported": [
                    {
                        "Name": "Alpha",
                        "City": "Taitung",
                        "DescriptionFields": {
                            "總得碗數": "1",
                            "得獎菜色": "春捲",
                            "菜系": "台式",
                            "推薦評審": "蔣勳",
                            "地址": "950臺東縣台東市正氣路453-1號",
                            "電話": "08 933 2520",
                        },
                    }
                ]
            }
        )

        self.assertEqual(len(result.failed_items), 1)
        self.assertEqual(
            result.failed_items[0].note_text,
            "1碗 | 春捲 | 台式 | 蔣勳 | 950臺東縣台東市正氣路453-1號 | 08 933 2520",
        )
```

- [ ] **Step 2: Run failing writer note-text test**

Run:

```bash
rtk uv run python -m unittest tests.test_google_maps_sync_writer.GoogleMapsSyncWriterTests.test_sync_rows_by_level_failed_item_includes_note_text_for_manual_recovery
```

Expected: FAIL because `SyncItemFailure` does not expose `note_text`.

- [ ] **Step 3: Add `note_text` to failure model**

In `michelin_scraper/application/sync_models.py`, add a defaulted field to `SyncItemFailure` after `rejected_candidates`:

```python
    rejected_candidates: tuple[SyncRejectedCandidate, ...] = dataclasses.field(
        default_factory=tuple
    )
    note_text: str = ""
```

Keep it defaulted and append it after the existing default field so any positional `rejected_candidates` call sites keep the same meaning.

- [ ] **Step 4: Populate `note_text` in Google Maps sync failures**

In `michelin_scraper/adapters/google_maps_sync_writer.py`, `_sync_one_row` now computes:

```python
        note_text = self._build_note_text(row, level_slug=level_slug)
```

Pass `note_text=note_text` to every `SyncItemFailure(...)` created for that row in both `_sync_one_row` and `_sync_one_row_without_retry`, including helper-level failures:

```python
failure = SyncItemFailure(
    level_slug=level_slug,
    row_key=row_key,
    restaurant_name=str(row.get("Name", "")),
    reason="PlaceNotFound",
    attempted_queries=attempted_queries,
    note_text=note_text,
    rejected_candidates=tuple(rejected_candidates),
)
```

And retry-wrapper failures:

```python
failure = SyncItemFailure(
    level_slug=level_slug,
    row_key=row_key,
    restaurant_name=str(row.get("Name", "")),
    reason=f"NoteWriteFailed: {exc}",
    attempted_queries=attempted_queries,
    note_text=note_text,
)
```

Apply the same `note_text=note_text` argument to selector runtime failures, note-write failures, transient retry-budget failures, save-action failures, ambiguous matches, place-not-found failures, and fail-fast exceptions that carry a `SyncItemFailure`.

Also add these assertions to existing writer tests so wrapper-level failures are covered:

```python
self.assertEqual(
    result.failed_items[0].note_text,
    "2025 | 1 Star | Jiangzhe | updated 2026-05-01\n"
    "A vibrant dining room with a skyline view.",
)
```

Add the assertion above to `test_sync_rows_by_level_smoke_marks_failure_when_note_write_fails`.

```python
self.assertEqual(
    result.failed_items[0].note_text,
    "1 Star | Jiangzhe | updated 2026-05-01",
)
```

Add the assertion above to `test_sync_rows_by_level_smoke_preserves_runtime_context_on_selector_failure`.

```python
self.assertEqual(
    captured.exception.failure.note_text,
    "1 Star | Jiangzhe | updated 2026-05-01",
)
```

Add the assertion above to `test_sync_rows_by_level_stop_policy_fails_fast_on_first_row_failure`.

- [ ] **Step 5: Verify writer failure carries note text**

Run:

```bash
rtk uv run python -m unittest tests.test_google_maps_sync_writer.GoogleMapsSyncWriterTests.test_sync_rows_by_level_failed_item_includes_note_text_for_manual_recovery
```

Expected: PASS.

- [ ] **Step 6: Add failing JSONL report test**

In `tests/test_sync_use_case.py`, update `test_run_scrape_sync_fail_fast_writes_failure_report_and_stops_without_summary` to assert the serialized report includes `note_text`:

```python
report_entry = json.loads(error_report_path.read_text(encoding="utf-8").splitlines()[0])
self.assertEqual(report_entry["note_text"], "1碗 | 春捲 | 台式 | 蔣勳")
self.assertIn('"note_text": "1碗 | 春捲 | 台式 | 蔣勳"', error_report_path.read_text(encoding="utf-8"))
self.assertNotIn("\\u6625\\u6372", error_report_path.read_text(encoding="utf-8"))
```

Also add a second assertion in the same test or a small adjacent test to ensure JSON escaping remains valid for quotes and backslashes:

```python
failure = SyncItemFailure(
    level_slug="imported",
    row_key="imported::quoted",
    restaurant_name="Quoted",
    reason="PlaceNotFound",
    attempted_queries=("Quoted",),
    note_text='店名 "Alpha" | 路徑 C:\\temp',
)
payload = _serialize_failed_items((failure,))[0]
encoded = json.dumps(payload, ensure_ascii=False)
self.assertEqual(json.loads(encoded)["note_text"], '店名 "Alpha" | 路徑 C:\\temp')
```

Then update `_FailFastWriterWithDebugHtml` or its `SyncItemFailure(...)` fixture in the same test file so the failure object includes:

```python
note_text="1碗 | 春捲 | 台式 | 蔣勳",
```

Run this before implementing serialization so it fails if `_serialize_failed_items` drops the field:

```bash
rtk uv run python -m unittest tests.test_sync_use_case.SyncUseCaseTests.test_run_scrape_sync_fail_fast_writes_failure_report_and_stops_without_summary
```

Expected: FAIL because the JSONL report entry does not include `note_text`.

- [ ] **Step 7: Serialize note text into failure reports**

In `michelin_scraper/application/sync_use_case.py`, update `_serialize_failed_items(...)` so each failure dict includes:

```python
            "note_text": failure.note_text,
```

The field must be present even when empty so downstream tooling can rely on a stable schema.

Also update `_write_error_report(...)` to preserve UTF-8 text for manual copy/paste:

```python
            file_handle.write(json.dumps(failure, ensure_ascii=False) + "\n")
```

- [ ] **Step 8: Verify JSONL report includes note text**

Run:

```bash
rtk uv run python -m unittest tests.test_sync_use_case.SyncUseCaseTests.test_run_scrape_sync_fail_fast_writes_failure_report_and_stops_without_summary
```

Expected: PASS.

- [ ] **Step 9: Add failing console presenter test**

Create `tests/test_console_sync_presenter.py`:

```python
"""Tests for CLI sync presenter output."""

import io
import unittest
from contextlib import redirect_stdout

from michelin_scraper.application.sync_models import (
    SyncItemFailure,
    SyncSummary,
)
from michelin_scraper.domain import ScrapeRunMetrics
from michelin_scraper.output.console_sync_presenter import ConsoleSyncPresenter


class ConsoleSyncPresenterTests(unittest.TestCase):
    def test_show_final_results_prints_failure_note_text_for_manual_recovery(self) -> None:
        presenter = ConsoleSyncPresenter()
        summary = SyncSummary(
            metrics=ScrapeRunMetrics(total_restaurants=1, processed_pages=1),
            sample_rows=(),
            scraped_count_by_level={"imported": 1},
            added_count_by_level={"imported": 0},
            skipped_count_by_level={"imported": 0},
            failed_items=[
                SyncItemFailure(
                    level_slug="imported",
                    row_key="imported::alpha",
                    restaurant_name="Alpha",
                    reason="PlaceNotFound",
                    attempted_queries=("Alpha Taitung",),
                    note_text="1碗 | 春捲 | 台式 | 蔣勳 | 950臺東縣台東市正氣路453-1號 | 08 933 2520",
                )
            ],
            missing_lists=(),
            list_names_by_level={"imported": "My Places"},
            output_targets=(("imported", "Imported"),),
            elapsed_seconds=1.0,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            presenter.show_final_results(summary)

        text = output.getvalue()
        self.assertIn("Row Failures", text)
        self.assertIn("Note: 1碗 | 春捲 | 台式 | 蔣勳 | 950臺東縣台東市正氣路453-1號 | 08 933 2520", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 10: Run failing console presenter test**

Run:

```bash
rtk uv run python -m unittest tests.test_console_sync_presenter
```

Expected: FAIL because `ConsoleSyncPresenter.show_final_results` does not print note text under row failures.

- [ ] **Step 11: Print note text under each row failure**

In `michelin_scraper/output/console_sync_presenter.py`, update the `Row Failures` block:

```python
            for failure in summary.failed_items[:20]:
                self._line(
                    f"{failure.level_slug} | {failure.restaurant_name} | "
                    f"{failure.reason} | {failure.row_key[:12]}"
                )
                if failure.note_text:
                    self._line(f"  Note: {failure.note_text}")
```

Keep the existing 20-row limit; the JSONL report contains all failures.

- [ ] **Step 12: Verify Task 5**

Run:

```bash
rtk uv run python -m unittest tests.test_google_maps_sync_writer.GoogleMapsSyncWriterTests.test_sync_rows_by_level_failed_item_includes_note_text_for_manual_recovery tests.test_sync_use_case.SyncUseCaseTests.test_run_scrape_sync_fail_fast_writes_failure_report_and_stops_without_summary tests.test_console_sync_presenter
```

Expected: PASS.

- [ ] **Step 13: Commit Task 5**

```bash
rtk git add michelin_scraper/application/sync_models.py michelin_scraper/adapters/google_maps_sync_writer.py michelin_scraper/application/sync_use_case.py michelin_scraper/output/console_sync_presenter.py tests/test_google_maps_sync_writer.py tests/test_sync_use_case.py tests/test_console_sync_presenter.py
rtk git commit -m "Include note text in Maps failure reports"
```

## Task 6: Add CLI Options for My Maps Note Formatting

**Files:**
- Modify: `michelin_scraper/entrypoints/cli.py`
- Modify: `tests/test_cli_maps_help.py`
- Modify: `tests/test_generic_source_sync_use_case.py`

- [ ] **Step 1: Add failing help test**

Add to `tests/test_cli_maps_help.py`:

```python
def test_sync_my_maps_help_includes_note_format_options(self) -> None:
    result = self.runner.invoke(app, ["sync-my-maps", "--help"])

    self.assertEqual(result.exit_code, 0)
    self.assertIn("--note-format", result.stdout)
    self.assertIn("--note-template", result.stdout)
    self.assertIn("raw", result.stdout)
    self.assertIn("500bowls", result.stdout)
    self.assertIn("template", result.stdout)
```

- [ ] **Step 2: Add failing CLI propagation test**

Add to `tests/test_generic_source_sync_use_case.py` near My Maps use-case tests:

```python
def test_sync_my_maps_command_propagates_note_format_options(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        kml_path = Path(temp_dir) / "map.kml"
        kml_path.write_text(
            """\
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Imported Map</name>
    <Placemark>
      <name>Alpha</name>
      <address>100臺北市中正區測試路1號</address>
      <description>得獎菜色: 春捲</description>
    </Placemark>
  </Document>
</kml>
""",
            encoding="utf-8",
        )
        writer = _RecordingWriter()
        output = _FakeOutput()
        command = ScrapeSyncCommand(
            target="",
            google_user_data_dir="/tmp/profile",
            levels=("imported",),
            source="my-maps",
            my_maps_file=str(kml_path),
            note_format="template",
            note_template="{得獎菜色}",
            dry_run=False,
        )

        with patch.object(sync_use_case, "GoogleMapsSyncWriter", return_value=writer) as writer_factory:
            sync_use_case.run_scrape_sync(command, output)

        self.assertEqual(writer_factory.call_args.kwargs["source"], "my-maps")
        self.assertEqual(writer_factory.call_args.kwargs["note_format"], "template")
        self.assertEqual(writer_factory.call_args.kwargs["note_template"], "{得獎菜色}")
        self.assertEqual(command.note_format, "template")
        self.assertEqual(command.note_template, "{得獎菜色}")
        self.assertEqual(writer.synced_rows[0][0], "imported")
```

- [ ] **Step 3: Run failing CLI tests**

Run:

```bash
rtk uv run python -m unittest tests.test_cli_maps_help.CliMapsHelpTests.test_sync_my_maps_help_includes_note_format_options tests.test_generic_source_sync_use_case.GenericSourceSyncUseCaseTests.test_sync_my_maps_command_propagates_note_format_options
```

Expected: help test FAIL because options are missing. Propagation test may PASS after Task 4 if command fields already pass through; if it fails, use the failure to fix missing propagation.

- [ ] **Step 4: Add CLI imports**

In `michelin_scraper/entrypoints/cli.py`, import option flags:

```python
    NOTE_FORMAT_OPTION_FLAGS,
    NOTE_TEMPLATE_OPTION_FLAGS,
```

Import formatter choices:

```python
from ..sources.my_maps_note_formatter import MY_MAPS_NOTE_FORMAT_CHOICES
```

- [ ] **Step 5: Add CLI parameters to `sync_my_maps`**

In `sync_my_maps(...)`, add parameters before `login_timeout`:

```python
    note_format: Annotated[
        str,
        typer.Option(
            *NOTE_FORMAT_OPTION_FLAGS,
            help=(
                "My Maps note formatting mode. "
                f"Allowed values: {', '.join(MY_MAPS_NOTE_FORMAT_CHOICES)}. "
                "Default: raw."
            ),
            rich_help_panel=USER_OPTIONS_HELP_PANEL,
        ),
    ] = "raw",
    note_template: Annotated[
        str,
        typer.Option(
            *NOTE_TEMPLATE_OPTION_FLAGS,
            help=(
                "Template used when --note-format=template. "
                "Use KML description field names in braces, for example: {得獎菜色} | {地區}."
            ),
            rich_help_panel=USER_OPTIONS_HELP_PANEL,
        ),
    ] = "",
```

- [ ] **Step 6: Validate CLI option values**

Before constructing `ScrapeSyncCommand`, add:

```python
    normalized_note_format = note_format.strip().lower()
    if normalized_note_format not in MY_MAPS_NOTE_FORMAT_CHOICES:
        allowed = ", ".join(MY_MAPS_NOTE_FORMAT_CHOICES)
        raise typer.BadParameter(
            f"Unsupported note format for sync-my-maps: {note_format!r}. Allowed values: {allowed}."
        )
    if normalized_note_format == "template" and not note_template.strip():
        raise typer.BadParameter("--note-template is required when --note-format=template.")
```

Pass values into `ScrapeSyncCommand`:

```python
        note_format=normalized_note_format,
        note_template=note_template,
```

- [ ] **Step 7: Add invalid-option test**

Add to `tests/test_cli_maps_help.py`:

```python
def test_sync_my_maps_rejects_template_format_without_template(self) -> None:
    result = self.runner.invoke(
        app,
        [
            "sync-my-maps",
            "--my-maps-file",
            "/tmp/missing.kml",
            "--note-format",
            "template",
        ],
    )

    self.assertNotEqual(result.exit_code, 0)
    self.assertIn("--note-template is required", result.output)
```

- [ ] **Step 8: Verify Task 6**

Run:

```bash
rtk uv run python -m unittest tests.test_cli_maps_help.CliMapsHelpTests.test_sync_my_maps_help_includes_note_format_options tests.test_cli_maps_help.CliMapsHelpTests.test_sync_my_maps_rejects_template_format_without_template tests.test_generic_source_sync_use_case.GenericSourceSyncUseCaseTests.test_sync_my_maps_command_propagates_note_format_options
```

Expected: PASS.

- [ ] **Step 9: Commit Task 6**

```bash
rtk git add michelin_scraper/entrypoints/cli.py tests/test_cli_maps_help.py tests/test_generic_source_sync_use_case.py
rtk git commit -m "Expose My Maps note formatting options"
```

## Task 7: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update My Maps section**

In `README.md`, under `## Sync Google My Maps KML/KMZ`, add:

````markdown
### My Maps note formatting

`sync-my-maps` does not add Michelin sync metadata such as `updated <date>` to
saved-place notes. By default it writes parsed KML placemark description fields
as a single `key: value | key: value` note. If a description has no parseable
fields, it falls back to the plain description text. My Maps notes are formatted
as a single line because Google Maps saved-place notes are easier to scan that
way:

```bash
rtk uv run python -m michelin_scraper sync-my-maps \
  --my-maps-file /path/to/my-map.kml \
  --note-format raw
```

For 500 Bowls-style maps whose descriptions contain fields such as `總得碗數`,
`得獎菜色`, `地區`, `菜系`, `推薦評審`, `地址`, `營業時間`, and `電話`, use:

```bash
rtk uv run python -m michelin_scraper sync-my-maps \
  --my-maps-file /path/to/500-bowls.kml \
  --note-format 500bowls
```

For custom maps, use a template. Template placeholders refer to parsed
description field names and output values only. Add labels directly in the
template when needed:

```bash
rtk uv run python -m michelin_scraper sync-my-maps \
  --my-maps-file /path/to/my-map.kml \
  --note-format template \
  --note-template "{總得碗數}碗 | {得獎菜色} | 菜系: {菜系} | 地址: {地址} | 電話: {電話}"
```

Missing values are removed and common extra separators are cleaned up.
````

- [ ] **Step 2: Verify README mentions options**

Run:

```bash
rtk rg -n "note-format|500bowls|updated <date>|single line|values only" README.md
```

Expected: output includes each searched topic in the My Maps section.

- [ ] **Step 3: Commit Task 7**

```bash
rtk git add README.md
rtk git commit -m "Document My Maps note formatting"
```

## Task 8: Run Targeted and Release-Gate Validation

**Files:**
- No source edits unless validation fails.

- [ ] **Step 1: Run targeted tests**

Run:

```bash
rtk uv run python -m unittest tests.test_my_maps_source_adapter tests.test_my_maps_note_formatter tests.test_google_maps_sync_writer tests.test_sync_use_case tests.test_console_sync_presenter tests.test_generic_source_sync_use_case tests.test_cli_maps_help
```

Expected: PASS.

- [ ] **Step 2: Run full unit suite**

Run:

```bash
rtk uv run python -m unittest discover
```

Expected: PASS.

- [ ] **Step 3: Run ruff**

Run:

```bash
rtk uv run --extra dev ruff check .
```

Expected: PASS.

- [ ] **Step 4: Run basedpyright**

Run:

```bash
rtk uv run --extra dev basedpyright
```

Expected: PASS.

- [ ] **Step 5: Fix any validation failures**

If a validation command fails, inspect the exact file/line from the output, make the smallest source or test correction needed, and rerun the failed command. Do not change unrelated files.

- [ ] **Step 6: Final status**

Record changed files and validation results in the handoff. Include whether each command passed or failed:

```text
Changed files:
- michelin_scraper/application/sync_models.py
- michelin_scraper/config/cli_options.py
- michelin_scraper/entrypoints/cli.py
- michelin_scraper/sources/my_maps.py
- michelin_scraper/sources/my_maps_note_formatter.py
- michelin_scraper/adapters/google_maps_sync_writer.py
- michelin_scraper/application/sync_use_case.py
- michelin_scraper/output/console_sync_presenter.py
- README.md
- tests/test_my_maps_source_adapter.py
- tests/test_my_maps_note_formatter.py
- tests/test_google_maps_sync_writer.py
- tests/test_sync_use_case.py
- tests/test_console_sync_presenter.py
- tests/test_cli_maps_help.py
- tests/test_generic_source_sync_use_case.py

Validation:
- rtk uv run python -m unittest tests.test_my_maps_source_adapter tests.test_my_maps_note_formatter tests.test_google_maps_sync_writer tests.test_sync_use_case tests.test_console_sync_presenter tests.test_generic_source_sync_use_case tests.test_cli_maps_help: PASS
- rtk uv run python -m unittest discover: PASS
- rtk uv run --extra dev ruff check .: PASS
- rtk uv run --extra dev basedpyright: PASS
```
