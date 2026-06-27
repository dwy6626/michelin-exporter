"""Tests for Google Maps place query cascade behavior."""

import unittest

from michelin_scraper.application.place_query_builder import build_place_query_attempts


class PlaceQueryBuilderTests(unittest.TestCase):
    def test_build_place_query_attempts_uses_expected_priority(self) -> None:
        row = {
            "Name": "Alpha",
            "City": "Taipei",
            "Address": "No. 1 Example Street",
            "Cuisine": "Taiwanese",
            "Latitude": "25.0330",
            "Longitude": "121.5654",
        }

        attempts = build_place_query_attempts(row)

        self.assertEqual(
            attempts,
            (
                "Alpha Taipei",
                "Alpha",
                "25.033,121.5654",
                "No. 1 Example Street",
                "Alpha Taiwanese Taipei",
            ),
        )

    def test_build_place_query_attempts_prioritizes_taiwan_district_hint(self) -> None:
        row = {
            "Name": "首烏",
            "City": "New Taipei, 臺灣",
            "Address": "板橋區民族路27號",
            "Cuisine": "客家菜",
        }

        attempts = build_place_query_attempts(row)

        self.assertEqual(
            attempts,
            (
                "首烏 板橋區",
                "首烏 民族路27號",
                "首烏 New Taipei, 臺灣",
                "首烏",
                "板橋區民族路27號",
                "首烏 客家菜 板橋區",
                "首烏 客家菜 New Taipei, 臺灣",
            ),
        )

    def test_build_place_query_attempts_prioritizes_local_name_with_taiwan_district_hint(self) -> None:
        row = {
            "Name": "Shou Wu",
            "NameLocal": "首烏",
            "City": "New Taipei, 臺灣",
            "Address": "板橋區民族路27號",
            "Cuisine": "Hakkanese",
        }

        attempts = build_place_query_attempts(row)

        self.assertEqual(attempts[0], "首烏 板橋區")
        self.assertEqual(attempts[1], "首烏 民族路27號")
        self.assertLess(
            attempts.index("Shou Wu 板橋區"),
            attempts.index("Shou Wu New Taipei, 臺灣"),
        )

    def test_build_place_query_attempts_extracts_city_level_hint_after_county(self) -> None:
        row = {
            "Name": "Example",
            "City": "Hsinchu County, 臺灣",
            "Address": "302新竹縣竹北市成功一街20號",
            "Cuisine": "",
        }

        attempts = build_place_query_attempts(row)

        self.assertEqual(attempts[0], "Example 新竹縣竹北市")
        self.assertEqual(attempts[1], "Example 成功一街20號")

    def test_build_place_query_attempts_keeps_full_taiwan_city_district_hint_after_postal_code(self) -> None:
        cases = (
            ("許仔豬腳麵線", "103臺北市大同區保安街49巷17號", "許仔豬腳麵線 臺北市大同區"),
            ("廣東汕頭牛肉店", "203基隆市中山區復旦路17之6號", "廣東汕頭牛肉店 基隆市中山區"),
            ("正宗鴨肉飯", "813高雄市左營區裕誠路245號", "正宗鴨肉飯 高雄市左營區"),
            ("蒸餃阿姨", "261宜蘭縣頭城鎮南門路12號", "蒸餃阿姨 宜蘭縣頭城鎮"),
            ("福德小館", "209連江縣南竿鄉介壽村107-3號", "福德小館 連江縣南竿鄉"),
        )

        for name, address, expected_first_query in cases:
            with self.subTest(address=address):
                attempts = build_place_query_attempts(
                    {
                        "Name": name,
                        "City": "",
                        "Address": address,
                        "Cuisine": "",
                    }
                )

                self.assertEqual(attempts[0], expected_first_query)

    def test_build_place_query_attempts_uses_source_aliases_before_address_fallback(self) -> None:
        row = {
            "Name": "饅頭達人｜金獅湖肉包",
            "Aliases": ("饅頭達人 金獅湖肉包", "金獅湖肉包"),
            "City": "高雄",
            "Address": "807高雄市三民區鼎金後路45號",
            "Cuisine": "",
        }

        attempts = build_place_query_attempts(row)

        self.assertEqual(attempts[0], "饅頭達人｜金獅湖肉包 高雄市三民區")
        self.assertLess(
            attempts.index("金獅湖肉包 高雄市三民區"),
            attempts.index("807高雄市三民區鼎金後路45號"),
        )
        self.assertIn("金獅湖肉包 鼎金後路45號", attempts)

    def test_build_place_query_attempts_uses_street_house_before_city_for_branch_chains(self) -> None:
        row = {
            "Name": "鼎泰豐 (信義路)",
            "City": "Taipei, 臺灣",
            "Address": "大安區信義路二段277號, Taipei, 110, 臺灣",
            "Cuisine": "滬菜",
        }

        attempts = build_place_query_attempts(row)

        self.assertEqual(
            attempts[:3],
            (
                "鼎泰豐 (信義路) 大安區",
                "鼎泰豐 (信義路) 信義路二段277號",
                "鼎泰豐 (信義路) Taipei, 臺灣",
            ),
        )

    def test_build_place_query_attempts_removes_empty_and_duplicates(self) -> None:
        row = {
            "Name": "Alpha",
            "City": "",
            "Address": "Alpha",
            "Cuisine": "",
            "Latitude": "",
            "Longitude": "",
        }

        attempts = build_place_query_attempts(row)

        self.assertEqual(attempts, ("Alpha",))

    def test_build_place_query_attempts_ignores_invalid_coordinates(self) -> None:
        row = {
            "Name": "Alpha",
            "City": "Taipei",
            "Address": "No. 1 Example Street",
            "Cuisine": "",
            "Latitude": "999",
            "Longitude": "121.5654",
        }

        attempts = build_place_query_attempts(row)

        self.assertNotIn("999,121.5654", attempts)
        self.assertEqual(
            attempts,
            (
                "Alpha Taipei",
                "Alpha",
                "No. 1 Example Street",
            ),
        )


if __name__ == "__main__":
    unittest.main()
