"""Decision-layer tests for the general place matcher."""

import unittest

from michelin_scraper.application.place_matcher import PlaceCandidate, assess_place_match
from michelin_scraper.application.place_matcher_strategies import (
    extract_name_alternatives,
    extract_place_match_features,
)


class PlaceMatcherDecisionTests(unittest.TestCase):
    def test_parenthesized_alias_is_first_class_name_alternative(self) -> None:
        alternatives = extract_name_alternatives("菊子 Wouli 889（菊子窩裡）")

        self.assertIn("菊子 Wouli 889", alternatives)
        self.assertIn("菊子窩裡", alternatives)
        self.assertIn("菊子", alternatives)

    def test_alias_address_and_category_are_scored_together(self) -> None:
        features = extract_place_match_features(
            {
                "Name": "菊子 Wouli 889（菊子窩裡）",
                "City": "屏東",
                "Address": "900屏東縣屏東市北區市場57號",
                "Cuisine": "",
            },
            PlaceCandidate(
                name="菊子窩裡二號店（大陳手作料理）",
                address="900, Pingtung County, Pingtung City, 北區57號攤市場",
                category="餐廳",
            ),
        )

        self.assertGreaterEqual(features.alias_similarity, 85.0)
        self.assertGreaterEqual(features.address_similarity, 55.0)
        self.assertGreaterEqual(features.category_similarity, 80.0)
        self.assertGreater(features.combined_positive_evidence, features.risk_score)

    def test_address_and_house_number_are_positive_evidence_across_names(self) -> None:
        features = extract_place_match_features(
            {
                "Name": "二林竹筍粥",
                "City": "彰化",
                "Address": "526彰化縣二林鎮新生路108號",
                "Cuisine": "",
            },
            PlaceCandidate(
                name="阿才竹筍粥",
                address="No. 108號, Xinsheng Rd, Beiping Village, Erlin Township, Changhua County, 526",
                category="熟食店",
            ),
        )

        self.assertGreaterEqual(features.name_similarity, 70.0)
        self.assertGreaterEqual(features.house_number_match, 90.0)
        self.assertGreaterEqual(features.city_similarity, 70.0)
        self.assertLess(features.risk_score, features.combined_positive_evidence)

    def test_address_like_candidate_name_gets_disqualifier_score(self) -> None:
        features = extract_place_match_features(
            {
                "Name": "二林竹筍粥",
                "City": "彰化",
                "Address": "526彰化縣二林鎮新生路108號",
                "Cuisine": "",
            },
            PlaceCandidate(
                name="No. 108, Xinsheng Rd, Beiping Village, Erlin Township",
                address="No. 108, Xinsheng Rd, Beiping Village, Erlin Township, Changhua County, 526",
                category="",
            ),
        )

        self.assertGreaterEqual(features.disqualifier_score, 80.0)
        self.assertIn("address_like_candidate_name", features.risk_labels)

    def test_generic_store_category_does_not_veto_exact_food_place_match(self) -> None:
        assessment = assess_place_match(
            {
                "Name": "好口味包子(水晶餃)",
                "City": "嘉義",
                "Address": "600嘉義市東區和平路287號",
                "Cuisine": "",
            },
            PlaceCandidate(
                name="好口味包子(水晶餃)",
                address="No. 287, Heping Rd, Dongxing Village, East District, Chiayi City, 600",
                category="Store",
            ),
        )

        self.assertNotEqual(assessment.strength, "weak")
        self.assertFalse(assessment.hard_veto)
        self.assertNotIn("non_food_category", assessment.veto_reasons)

    def test_short_romanized_fuzzy_name_requires_stronger_place_evidence(self) -> None:
        assessment = assess_place_match(
            {
                "Name": "Ching Jiao",
                "City": "Taipei, Taiwan",
                "Address": "Da'an District, Taipei, Taiwan",
                "Cuisine": "",
            },
            PlaceCandidate(
                name="Chin Hua Jiao Taipei Guangfu South Branch",
                address="No. 100, Guangfu S Rd, Huasheng Village, Da'an District, Taipei City, 106",
                category="Hot pot restaurant",
                subtitle="青花驕麻辣鍋 台北光復南店",
            ),
        )

        self.assertEqual(assessment.strength, "weak")
        self.assertIn("short_latin_fuzzy_name_without_place_anchor", assessment.veto_reasons)

    def test_landmark_front_address_does_not_zero_exact_food_name_match(self) -> None:
        assessment = assess_place_match(
            {
                "Name": "麻糬寶寶",
                "City": "Taipei, 臺灣",
                "Address": "松山區饒河街111號前 (饒河街夜市), Taipei, 臺灣",
                "Cuisine": "街頭小吃",
            },
            PlaceCandidate(
                name="麻糬寶寶",
                address="No. 77號, Raohe St, Ciyou Village, Songshan District, Taipei City, 105",
                category="甜品店",
            ),
        )

        self.assertNotEqual(assessment.strength, "weak")
        self.assertNotIn("house_number_conflict", assessment.veto_reasons)

    def test_precise_different_house_number_still_rejects_exact_name_match(self) -> None:
        assessment = assess_place_match(
            {
                "Name": "麻糬寶寶",
                "City": "Taipei, 臺灣",
                "Address": "松山區饒河街111號, Taipei, 臺灣",
                "Cuisine": "街頭小吃",
            },
            PlaceCandidate(
                name="麻糬寶寶",
                address="No. 77號, Raohe St, Ciyou Village, Songshan District, Taipei City, 105",
                category="甜品店",
            ),
        )

        self.assertEqual(assessment.strength, "weak")
        self.assertIn("house_number_conflict", assessment.veto_reasons)


if __name__ == "__main__":
    unittest.main()
