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
