"""Routing logic that maps scraped rows to output levels."""


from collections.abc import Mapping, Sequence
from typing import Any


class UnrecognizedRatingError(ValueError):
    """Raised when a restaurant has a rating not in any known level mapping."""

    def __init__(self, rating: str, restaurant_name: str) -> None:
        self.rating = rating
        self.restaurant_name = restaurant_name
        super().__init__(
            f"Unrecognized rating {rating!r} for restaurant {restaurant_name!r}. "
            "This likely indicates a Michelin website HTML change."
        )


class LevelRowRouter:
    """Group restaurant rows by output level from their rating value.

    *rating_to_level_slug* must cover **all** known Michelin ratings (not
    just the selected levels).  Rows whose rating maps to a level outside
    *level_slugs* are skipped; rows whose rating is completely unknown
    raise ``UnrecognizedRatingError``.
    """

    def __init__(
        self,
        level_slugs: Sequence[str],
        rating_to_level_slug: Mapping[str, str],
    ) -> None:
        self._level_slugs = tuple(level_slugs)
        self._selected_level_set = frozenset(level_slugs)
        self._rating_to_level_slug = {
            _normalize_rating_value(rating_value): level_slug
            for rating_value, level_slug in rating_to_level_slug.items()
            if _normalize_rating_value(rating_value)
        }

    def group_rows_by_level(
        self,
        restaurants_on_page: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {
            level_slug: [] for level_slug in self._level_slugs
        }
        for row in restaurants_on_page:
            rating_value = _normalize_rating_value(row.get("Rating", ""))
            level_slug = self._rating_to_level_slug.get(rating_value)
            if level_slug is None:
                raise UnrecognizedRatingError(
                    rating=row.get("Rating", ""),
                    restaurant_name=row.get("Name", ""),
                )
            if level_slug not in self._selected_level_set:
                continue
            grouped[level_slug].append(row)
        return grouped


def _normalize_rating_value(value: Any) -> str:
    return " ".join(str(value).split()).casefold().strip()
