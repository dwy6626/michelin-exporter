"""Tests for Google Maps place matching heuristics."""

import unittest

from michelin_scraper.application.place_matcher import (
    PlaceCandidate,
    assess_place_match,
    classify_place_match,
)


class PlaceMatcherTests(unittest.TestCase):
    def test_assess_place_match_strong_when_name_and_location_overlap(self) -> None:
        row = {
            "Name": "Alpha",
            "City": "Tokyo",
            "Address": "1-2-3 Minato, Tokyo, 106-0044, Japan",
            "Cuisine": "French",
        }
        candidate = PlaceCandidate(
            name="Alpha",
            address="1-2-3 Minato City, Tokyo 106-0044, Japan",
            category="French restaurant",
        )

        assessment = assess_place_match(row, candidate)

        self.assertEqual(assessment.strength, "strong")
        self.assertTrue(assessment.name_match)
        self.assertIn("minato", assessment.location_overlap_tokens)

    def test_assess_place_match_allows_local_script_name_with_postal_and_cuisine(self) -> None:
        row = {
            "Name": "Biriyani Osawa",
            "City": "Tokyo",
            "Address": "B1F, 1-15-12 Uchikanda, Chiyoda-ku, Tokyo, 101-0047, Japan",
            "Cuisine": "Indian",
        }
        candidate = PlaceCandidate(
            name="ビリヤニ大澤",
            address="Japan, 〒101-0047 Tokyo, Chiyoda City, Uchikanda, 1 Chome-15-12",
            category="Indian restaurant",
        )

        assessment = assess_place_match(row, candidate)

        self.assertEqual(assessment.strength, "medium")
        self.assertFalse(assessment.name_match)
        self.assertIn("1010047", assessment.postal_code_overlap_tokens)
        self.assertIn("indian", assessment.cuisine_overlap_tokens)
        self.assertTrue(assessment.city_in_candidate_address)
        self.assertEqual(classify_place_match(row, candidate), "medium")

    def test_assess_place_match_rejects_name_mismatch_without_corroboration(self) -> None:
        row = {
            "Name": "Mitsui",
            "City": "Tokyo",
            "Address": "5F, 3-10-2 Azabujuban, Minato-ku, Tokyo, 106-0045, Japan",
            "Cuisine": "Sushi",
        }
        candidate = PlaceCandidate(
            name="Mitsui Garden Hotel Jingugaien Tokyo PREMIER",
            address="11-3 Kasumigaokamachi, Shinjuku City, Tokyo 160-0013, Japan",
            category="Hotel",
        )

        assessment = assess_place_match(row, candidate)

        self.assertEqual(assessment.strength, "weak")
        self.assertFalse(assessment.name_match)
        self.assertEqual(assessment.cuisine_overlap_tokens, ())

    def test_assess_place_match_accepts_cjk_name_suffix_variant(self) -> None:
        row = {
            "Name": "蓮霧腳羊肉湯",
            "City": "Tainan, 臺灣",
            "Address": "No. 361, Zhongzheng Rd, Xinhua Dist, Tainan",
            "Cuisine": "Taiwanese",
        }
        candidate = PlaceCandidate(
            name="蓮霧腳羊肉",
            address="No. 361, Zhongzheng Rd, Xinhua District, Tainan City, 712",
            category="Taiwanese restaurant",
        )

        assessment = assess_place_match(row, candidate)

        self.assertEqual(assessment.strength, "strong")
        self.assertTrue(assessment.name_match)
        self.assertIn("361", assessment.location_overlap_tokens)

    def test_assess_place_match_rejects_cjk_substring_match_with_wrong_city(self) -> None:
        row = {
            "Name": "繡球",
            "City": "Taichung, 臺灣",
            "Address": "No. 10, Section 2, Meicun Rd, West District, Taichung",
            "Cuisine": "Taiwanese",
        }
        candidate = PlaceCandidate(
            name="高家繡球花田",
            address="No. 33, Zhuzihu Rd, Beitou District, Taipei City, 112",
            category="Tourist attraction",
        )

        assessment = assess_place_match(row, candidate)

        self.assertTrue(assessment.name_match)
        self.assertFalse(assessment.city_in_candidate_address)
        self.assertNotEqual(assessment.strength, "strong")

    def test_assess_place_match_allows_translated_name_with_location_anchor(self) -> None:
        row = {
            "Name": "大勇街無名鹹粥",
            "City": "Tainan, 臺灣",
            "Address": "台南市中西區大勇街85號",
            "Cuisine": "Congee",
        }
        candidate = PlaceCandidate(
            name="Dayong Street No Name Congee",
            address="No. 85, Dayong St, West Central District, Tainan City, 700",
            category="",
        )

        assessment = assess_place_match(row, candidate)

        self.assertEqual(assessment.strength, "medium")
        self.assertFalse(assessment.name_match)
        self.assertIn("85", assessment.location_overlap_tokens)
        self.assertIn("tainan", assessment.location_overlap_tokens)
        self.assertTrue(assessment.city_in_candidate_address)

    def test_assess_place_match_allows_translated_name_with_city_and_single_digit_house_number(self) -> None:
        row = {
            "Name": "尚好吃牛肉湯",
            "City": "Tainan, 臺灣",
            "Address": "北區北安路一段6號",
            "Cuisine": "",
        }
        candidate = PlaceCandidate(
            name="Shang Hao Chi Beef Soup",
            address="No. 6號, Section 1, Beian Rd, North District, Tainan City, 704",
            category="",
        )

        assessment = assess_place_match(row, candidate)

        self.assertEqual(assessment.strength, "medium")
        self.assertFalse(assessment.name_match)
        self.assertTrue(assessment.city_in_candidate_address)
        self.assertIn("6", assessment.location_overlap_tokens)
        self.assertIn("tainan", assessment.location_overlap_tokens)

    def test_assess_place_match_rejects_coordinate_like_candidate_name(self) -> None:
        row = {
            "Name": "Example Bistro",
            "City": "Tokyo",
            "Address": "1-2-3 Minato, Tokyo, 106-0044, Japan",
            "Cuisine": "French",
        }
        candidate = PlaceCandidate(
            name='35°41\'32.0"N 139°45\'57.9"E',
            address="1-2-3 Minato, Tokyo 106-0044, Japan",
            category="French restaurant",
        )

        assessment = assess_place_match(row, candidate)

        self.assertEqual(assessment.strength, "weak")
        self.assertTrue(assessment.coordinate_like_candidate_name)
        self.assertEqual(classify_place_match(row, candidate), "weak")

    def test_assess_place_match_rejects_nested_business_inside_same_venue(self) -> None:
        row = {
            "Name": "葉家藥燉排骨",
            "City": "New Taipei, 臺灣",
            "Address": "板橋區中山路二段441號",
            "Cuisine": "Taiwanese",
        }
        candidate = PlaceCandidate(
            name="IKKI深夜食堂-板橋店",
            address="No. 439號, Section 2, Zhongshan Rd, Banqiao District, New Taipei City, 220",
            category="Izakaya restaurant",
            located_in="Located in: 葉家藥燉排骨",
        )

        assessment = assess_place_match(row, candidate)

        self.assertEqual(assessment.strength, "weak")
        self.assertFalse(assessment.name_match)
        self.assertTrue(assessment.located_in_match)
        self.assertTrue(assessment.city_in_candidate_address)

    def test_assess_place_match_rejects_non_food_business_with_shared_address(self) -> None:
        row = {
            "Name": "陽明春天 (士林)",
            "City": "Taipei, 臺灣",
            "Address": "士林區菁山路119之1號",
            "Cuisine": "Vegetarian",
        }
        candidate = PlaceCandidate(
            name="Pure Honey Oil Workshop - Yuanpin Food Co., Ltd.",
            address="No. 119-1號, Jingshan Rd, Shilin District, Taipei City, 111",
            category="Store",
            located_in="Located in: Yangming Spring",
        )

        assessment = assess_place_match(row, candidate)

        self.assertEqual(assessment.strength, "weak")
        self.assertFalse(assessment.name_match)
        self.assertFalse(assessment.located_in_match)
        self.assertTrue(assessment.city_in_candidate_address)


if __name__ == "__main__":
    unittest.main()
