"""Shared test doubles for Google Maps adapter and sync-writer tests."""

from pathlib import Path

from michelin_scraper.adapters.google_maps_driver import GoogleMapsDriverPort
from michelin_scraper.application.place_matcher import PlaceCandidate


class FakeDriver:
    """Protocol-compatible fake Maps driver with call recording."""

    def __init__(self) -> None:
        self.existing_lists: set[str] = set()
        self.openable_lists: set[str] = set()
        self.created_lists: list[str] = []
        self.candidates_by_query: dict[str, PlaceCandidate | None] = {}
        self.queries: list[str] = []
        self.save_calls: list[tuple[str, str]] = []
        self.save_error: Exception | None = None
        self.auth_checks = 0

    async def start(self) -> None:
        return

    async def open_maps_home(self) -> None:
        return

    async def is_authenticated(self, *, refresh: bool = True) -> bool:
        del refresh
        self.auth_checks += 1
        return True

    async def list_exists(self, list_name: str) -> bool:
        return list_name in self.existing_lists

    async def create_list(self, list_name: str) -> None:
        self.created_lists.append(list_name)
        self.openable_lists.add(list_name)

    async def open_list(self, list_name: str) -> bool:
        return list_name in self.openable_lists

    async def search_and_open_first_result(self, query: str) -> PlaceCandidate | None:
        self.queries.append(query)
        return self.candidates_by_query.get(query)

    async def save_current_place_to_list(self, list_name: str, note_text: str = "") -> bool:
        self.save_calls.append((list_name, note_text))
        if self.save_error is not None:
            raise self.save_error
        return list_name in self.openable_lists

    async def close(self, *, force: bool = False) -> None:
        del force
        return

    async def dump_page_html(self, path: Path) -> bool:
        del path
        return False


def _assert_driver_port(fake_driver: GoogleMapsDriverPort) -> None:
    """Type-check helper to guarantee Protocol compatibility."""

    del fake_driver


_assert_driver_port(FakeDriver())


class FakePage:
    def __init__(self, *, closed: bool = False, content: str = "") -> None:
        self._closed = closed
        self._content = content

    def is_closed(self) -> bool:
        return self._closed

    async def content(self) -> str:
        return self._content


class FakeContext:
    def __init__(self, *, pages: list[FakePage], cookies_payload: list[dict[str, str]]) -> None:
        self.pages = pages
        self._cookies_payload = cookies_payload
        self.created_page_count = 0

    async def new_page(self) -> FakePage:
        self.created_page_count += 1
        page = FakePage(closed=False)
        self.pages.append(page)
        return page

    async def cookies(self, urls: list[str]) -> list[dict[str, str]]:
        del urls
        return self._cookies_payload
