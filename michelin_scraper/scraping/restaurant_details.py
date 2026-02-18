"""Restaurant detail page scraping utilities."""


import time
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ..config import HTTP_RETRY_BASE_DELAY_SECONDS, HTTP_RETRYABLE_STATUS_CODES
from .fetcher import fetch_page_soup
from .models import ScrapeProgressReporter
from .parsers import parse_gm_iframe_url

RestaurantPageData = dict[str, float | str]

_ADDRESS_SELECTOR = "div.data-sheet__block--text"
_DESCRIPTION_SELECTOR = "div.data-sheet__description"
_WEBSITE_EVENT_NAME = "CTA_website"
_TELEPHONE_EVENT_NAME = "CTA_tel"
_RESERVATION_BUTTON_CLASS = "js-restaurant-book-btn"
_RESTAURANT_PAGE_TYPE = "restaurant"


def build_empty_data() -> RestaurantPageData:
    """Return empty detail fields for missing or failed detail pages."""

    return {
        "Name": "",
        "NameLocal": "",
        "Address": "",
        "Description": "",
        "Restaurant Website": "",
        "Telephone Number": "",
        "Reservation Link": "",
        "Latitude": "",
        "Longitude": "",
    }


def extract_href(tag: Any) -> str:
    """Extract href attribute from a BeautifulSoup tag."""

    if not tag or not hasattr(tag, "has_attr") or not tag.has_attr("href"):
        return ""
    href_value = tag["href"]
    return href_value if isinstance(href_value, str) else ""


def build_restaurant_record(
    *,
    name: str,
    rating: str,
    city: str,
    price: str,
    cuisine: str,
    restaurant_url: str,
    page_data: RestaurantPageData,
) -> dict[str, Any]:
    """Build one final output row from listing and detail fields."""

    return {
        "Name": name,
        "NameLocal": page_data.get("NameLocal") or page_data.get("Name") or name,
        "Rating": rating,
        "City": city,
        "Price Range": price,
        "Cuisine": cuisine,
        "Description": page_data["Description"],
        "Address": page_data["Address"],
        "Latitude": page_data["Latitude"],
        "Longitude": page_data["Longitude"],
        "Michelin Website": restaurant_url,
        "Restaurant Website": page_data["Restaurant Website"],
        "Restaurant Telephone Number": page_data["Telephone Number"],
        "Reservation Link": page_data["Reservation Link"],
    }


def scrape_restaurant_page(
    *,
    session: requests.Session,
    url: str,
    headers: dict[str, str],
    tls_verify: bool | str,
    progress_reporter: ScrapeProgressReporter,
    local_language: str | None = None,
    local_country_code: str | None = None,
    requested_language: str | None = None,
) -> RestaurantPageData:
    """Fetch and parse detail fields from one restaurant page."""

    restaurant_page_url = url
    if not restaurant_page_url:
        return build_empty_data()

    restaurant_page_soup = fetch_page_soup(
        session=session,
        url=restaurant_page_url,
        headers=headers,
        tls_verify=tls_verify,
        progress_reporter=progress_reporter,
        page_type=_RESTAURANT_PAGE_TYPE,
    )
    if restaurant_page_soup.soup is None:
        return build_empty_data()

    data = _extract_restaurant_page_data(restaurant_page_soup.soup)

    # Some versions of Michelin Guide (e.g. Traditional Chinese/English for Japan)
    # strip the local script names. Google Maps sync works much better with local names.
    # If a local language is known and different from requested, we fetch the local name.
    if (
        local_language
        and requested_language
        and local_language != requested_language
        and "/" in restaurant_page_url
    ):
        local_name = _fetch_local_name(
            session=session,
            url=restaurant_page_url,
            headers=headers,
            tls_verify=tls_verify,
            local_language=local_language,
            local_country_code=local_country_code,
            requested_language=requested_language,
        )
        if local_name:
            data["NameLocal"] = local_name

    return data


def _fetch_local_name(
    *,
    session: requests.Session,
    url: str,
    headers: dict[str, str],
    tls_verify: bool | str,
    local_language: str,
    local_country_code: str | None,
    requested_language: str,
) -> str:
    """Fetch restaurant name from the local-language version of the page."""

    # Parse URL to correctly swap country and language segments.
    # Michelin URLs follow: guide.michelin.com/[country]/[lang]/...
    parsed_url = urlparse(url)
    path_segments = parsed_url.path.split("/")
    if len(path_segments) < 3:
        return ""

    # path_segments[0] is empty
    # path_segments[1] is country code (e.g. 'tw')
    # path_segments[2] is language code (e.g. 'zh_TW')

    original_country_code = path_segments[1]

    # Change to local segments
    path_segments[1] = local_country_code or original_country_code
    path_segments[2] = local_language
    
    local_path = "/".join(path_segments)
    local_url = parsed_url._replace(path=local_path).geturl()

    if local_url == url:
        return ""

    response = None
    for attempt in range(3):
        if attempt > 0:
            time.sleep(HTTP_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
        try:
            response = session.get(local_url, headers=headers, verify=tls_verify, timeout=10)
            if response.status_code not in HTTP_RETRYABLE_STATUS_CODES:
                break
        except requests.exceptions.RequestException:
            continue
    if response is None or response.status_code != 200:
        return ""

    soup = BeautifulSoup(response.text, "html.parser")
    title_tag = soup.find("h1")
    if title_tag:
        name_text = title_tag.get_text(strip=True)
        # If the name is in format "LocalName / RomanName" or "LocalName / RomanName",
        # we often just want the part before the separator.
        for separator in ("/", "／", "|"):
            if separator in name_text:
                return name_text.split(separator)[0].strip()
        return name_text

    return ""


def extract_telephone(href: str) -> str:
    """Normalize telephone href value into plain number."""

    if not href:
        return ""
    return href.split(":", 1)[1] if ":" in href else href


def _extract_restaurant_page_data(restaurant_page_soup: BeautifulSoup) -> RestaurantPageData:
    """Extract all supported detail fields from a restaurant page soup."""

    address = _extract_text_by_selector(restaurant_page_soup, _ADDRESS_SELECTOR)
    description = _extract_text_by_selector(restaurant_page_soup, _DESCRIPTION_SELECTOR)

    website_link = _extract_link_by_event_name(
        restaurant_page_soup, _WEBSITE_EVENT_NAME
    )
    telephone_link = _extract_link_by_event_name(
        restaurant_page_soup, _TELEPHONE_EVENT_NAME
    )
    reservation_link = _extract_reservation_link(restaurant_page_soup)

    latitude, longitude = _extract_coordinates(restaurant_page_soup)

    return {
        "Address": address,
        "Description": description,
        "Restaurant Website": website_link,
        "Telephone Number": extract_telephone(telephone_link),
        "Reservation Link": reservation_link,
        "Latitude": latitude,
        "Longitude": longitude,
    }


def _extract_text_by_selector(restaurant_page_soup: BeautifulSoup, selector: str) -> str:
    """Extract stripped text for the first matching selector."""

    selected_tag = restaurant_page_soup.select_one(selector)
    return selected_tag.get_text(strip=True) if selected_tag else ""


def _extract_link_by_event_name(
    restaurant_page_soup: BeautifulSoup, event_name: str
) -> str:
    """Extract href from the first link matching a data-event value."""

    event_link_tag = restaurant_page_soup.find("a", {"data-event": event_name})
    return extract_href(event_link_tag)


def _extract_reservation_link(restaurant_page_soup: BeautifulSoup) -> str:
    """Extract restaurant reservation link if present."""

    reservation_link_tag = restaurant_page_soup.find(
        "a", class_=_RESERVATION_BUTTON_CLASS
    )
    return extract_href(reservation_link_tag)


def _extract_coordinates(restaurant_page_soup: BeautifulSoup) -> tuple[float | str, float | str]:
    """Extract latitude and longitude from the best available iframe source."""

    preferred_iframe_url = extract_preferred_iframe_url(restaurant_page_soup)
    if not preferred_iframe_url:
        return "", ""
    return parse_gm_iframe_url(preferred_iframe_url)


def extract_preferred_iframe_url(soup: BeautifulSoup) -> str:
    """Extract the best iframe source candidate for map coordinates."""

    iframe_tags = soup.select("iframe")
    preferred_order = [1, 0]
    for index in preferred_order:
        if len(iframe_tags) <= index:
            continue
        iframe_src = iframe_tags[index].get("src")
        if isinstance(iframe_src, str):
            return iframe_src
    return ""
