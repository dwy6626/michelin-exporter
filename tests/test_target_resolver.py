"""Tests for target normalization and resolution."""

import unittest

import typer

from michelin_scraper.catalog.targets import (
    CITY_URLS,
    COUNTRY_URLS,
    normalize_target,
    resolve_language,
    resolve_target,
)


class TargetResolverTests(unittest.TestCase):
    def test_normalize_target_lowercases_and_trims(self) -> None:
        normalized = normalize_target("  USA  ")
        self.assertEqual(normalized, "usa")

    def test_resolve_target_city_precedence(self) -> None:
        resolved = resolve_target("hong-kong")
        self.assertEqual(resolved.start_url, CITY_URLS["hong-kong"])
        self.assertEqual(resolved.scope_name, "Hong Kong")

    def test_resolve_target_country_alias(self) -> None:
        resolved = resolve_target("usa")
        self.assertEqual(resolved.start_url, COUNTRY_URLS["united-states"])
        self.assertEqual(resolved.scope_name, "United States")

    def test_resolve_target_country_alias_with_custom_language(self) -> None:
        resolved = resolve_target("usa", language="ja")
        self.assertEqual(
            resolved.start_url,
            "https://guide.michelin.com/ja/us/restaurants",
        )

    def test_resolve_target_city_with_custom_language_alias(self) -> None:
        resolved = resolve_target("hong-kong", language="zh-tw")
        self.assertEqual(
            resolved.start_url,
            "https://guide.michelin.com/tw/zh_TW/hong-kong-region/hong-kong/restaurants",
        )

    def test_resolve_target_macau_city_with_zh_tw_language(self) -> None:
        resolved = resolve_target("macau", language="zh-tw")
        self.assertEqual(
            resolved.start_url,
            "https://guide.michelin.com/tw/zh_TW/macau-region/macau/restaurants",
        )

    def test_resolve_target_city_falls_back_to_zh_tw_when_no_zh_cn_override(self) -> None:
        # zh_CN has no city overrides at all, so falls back to zh_TW
        resolved = resolve_target("tokyo", language="zh-cn")
        self.assertEqual(
            resolved.start_url,
            "https://guide.michelin.com/tw/zh_TW/tokyo-region/tokyo/restaurants",
        )

    def test_resolve_target_country_falls_back_to_zh_tw_when_no_zh_cn_override(self) -> None:
        # zh_CN has no country overrides at all, so falls back to zh_TW
        resolved = resolve_target("japan", language="zh-cn")
        self.assertEqual(
            resolved.start_url,
            "https://guide.michelin.com/tw/zh_TW/selection/japan/restaurants",
        )

    def test_resolve_target_taiwan_country_with_traditional_chinese_language(self) -> None:
        resolved = resolve_target("taiwan", language="zh-tw")
        self.assertEqual(
            resolved.start_url,
            "https://guide.michelin.com/tw/zh_TW/selection/taiwan/restaurants",
        )

    def test_resolve_target_tainan_with_traditional_chinese_language(self) -> None:
        resolved = resolve_target("tainan", language="zh-tw")
        self.assertEqual(
            resolved.start_url,
            "https://guide.michelin.com/tw/zh_TW/tainan-region/restaurants",
        )
        self.assertEqual(resolved.scope_name, "\u81fa\u5357")

    def test_resolve_language_empty_raises(self) -> None:
        with self.assertRaises(typer.BadParameter):
            resolve_language("  ")

    def test_resolve_target_invalid_raises(self) -> None:
        with self.assertRaises(typer.BadParameter):
            resolve_target("not-a-valid-target")


if __name__ == "__main__":
    unittest.main()
