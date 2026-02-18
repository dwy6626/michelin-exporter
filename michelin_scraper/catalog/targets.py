"""Target catalog and target-resolution logic."""

from dataclasses import dataclass

import typer

from ..config import DEFAULT_LANGUAGE, MICHELIN_BASE_URL

COUNTRY_CODES = {
    "taiwan": "tw",
    "united-states": "us",
    "japan": "jp",
    "singapore": "sg",
    "thailand": "th",
    "france": "fr",
    "italy": "it",
    "spain": "es",
    "united-kingdom": "gb",
    "germany": "de",
    "south-korea": "kr",
    "china-mainland": "cn",
    "hong-kong": "hk",
    "macau": "mo",
    "malaysia": "my",
    "vietnam": "vn",
    "canada": "ca",
    "austria": "at",
    "ireland": "ie",
    "netherlands": "nl",
    "portugal": "pt",
    "switzerland": "ch",
    "czechia": "cz",
    "poland": "pl",
    "greece": "gr",
    "turkey": "tr",
    "argentina": "ar",
    "brazil": "br",
    "qatar": "qa",
    "united-arab-emirates": "ae",
    "saudi-arabia": "sa",
}

# Relative paths (without base URL) for predefined city listing pages.
CITY_PATHS = {
    # Asia
    "hong-kong": "en/hk/hong-kong-region/hong-kong/restaurants",
    "macau": "en/mo/macau-region/macau/restaurants",
    "singapore": "en/sg/singapore-region/singapore/restaurants",
    "bangkok": "en/th/bangkok-region/bangkok/restaurants",
    "phuket": "en/th/phuket-region/phuket/restaurants",
    "chiang-mai": "en/th/chiang-mai-region/chiang-mai/restaurants",
    "taipei": "en/tw/taipei-region/taipei/restaurants",
    "taichung": "en/tw/taichung-region/taichung/restaurants",
    "tainan": "en/tw/tainan-region/tainan/restaurants",
    "kaohsiung": "en/tw/kaohsiung-region/kaohsiung/restaurants",
    "tokyo": "en/jp/tokyo-region/tokyo/restaurants",
    "osaka": "en/jp/osaka-region/osaka/restaurants",
    "kyoto": "en/jp/kyoto-region/kyoto/restaurants",
    "seoul": "en/kr/seoul-capital-area/kr-seoul/restaurants",
    "beijing": "en/cn/beijing-municipality/beijing/restaurants",
    "shanghai": "en/cn/shanghai-municipality/shanghai/restaurants",
    "guangzhou": "en/cn/guangdong-province/guangzhou/restaurants",
    "chengdu": "en/cn/chengdu-municipality/restaurants",
    "hangzhou": "en/cn/zhe-jiang/hangzhou_1027184/restaurants",
    "kuala-lumpur": "en/my/kuala-lumpur-region/kuala-lumpur/restaurants",
    "hanoi": "en/ha-noi/restaurants",
    "ho-chi-minh-city": "en/ho-chi-minh/ho-chi-minh_2978179/restaurants",
    "da-nang": "en/da-nang-region/restaurants",
    "dubai": "en/ae/dubai-emirate/dubai/restaurants",
    "abu-dhabi": "en/ae/abu-dhabi-emirate/abu-dhabi/restaurants",
    "doha": "en/doha-region/doha_2433318/restaurants",
    "riyadh": "en/riyadh-province/riyadh_2555317/restaurants",
    "jeddah": "en/mecca-province/jeddah_2555048/restaurants",
    "hsinchu": "en/tw/northern-taiwan/hsinchu-city_2853126/restaurants",
    "istanbul": "en/istanbul-province/istanbul/restaurants",
    # Europe
    "paris": "en/fr/ile-de-france/paris/restaurants",
    "lyon": "en/fr/auvergne-rhone-alpes/lyon/restaurants",
    "london": "en/gb/greater-london/london/restaurants",
    "edinburgh": "en/gb/city-of-edinburgh/edinburgh/restaurants",
    "manchester": "en/gb/greater-manchester/manchester/restaurants",
    "madrid": "en/es/comunidad-de-madrid/madrid/restaurants",
    "barcelona": "en/es/catalunya/barcelona/restaurants",
    "rome": "en/it/lazio/roma/restaurants",
    "milan": "en/it/lombardia/milano/restaurants",
    "florence": "en/it/toscana/firenze/restaurants",
    "turin": "en/it/piemonte/torino/restaurants",
    "naples": "en/it/campania/napoli/restaurants",
    "berlin": "en/de/berlin-region/berlin/restaurants",
    "hamburg": "en/de/hamburg-region/hamburg/restaurants",
    "munich": "en/de/bayern/mnchen/restaurants",
    "amsterdam": "en/nl/noord-holland/amsterdam/restaurants",
    "lisbon": "en/pt/lisboa-region/lisboa/restaurants",
    "porto": "en/pt/porto-region/porto/restaurants",
    "zurich": "en/ch/zurich-region/zurich/restaurants",
    "geneva": "en/geneve-region/restaurants",
    "vienna": "en/at/vienna/wien/restaurants",
    "dublin": "en/dublin/restaurants",
    "prague": "en/cz/prague/prague/restaurants",
    "warsaw": "en/pl/masovia/warsaw/restaurants",
    "athens": "en/gr/attica/athens/restaurants",
    # North/South America
    "new-york": "en/us/new-york-state/new-york/restaurants",
    "chicago": "en/us/illinois/chicago/restaurants",
    "san-francisco": "en/us/california/san-francisco/restaurants",
    "washington-dc": "en/us/district-of-columbia/washington-dc/restaurants",
    "los-angeles": "en/us/california/us-los-angeles/restaurants",
    "miami": "en/us/florida/miami/restaurants",
    "orlando": "en/us/florida/orlando/restaurants",
    "tampa": "en/us/florida/tampa/restaurants",
    "austin": "en/texas/austin_2958315/restaurants",
    "houston": "en/texas/houston_2986624/restaurants",
    "dallas": "en/us/texas/dallas_2954570/restaurants",
    "toronto": "en/ca/ontario/toronto/restaurants",
    "vancouver": "en/british-columbia/ca-vancouver/restaurants",
    "buenos-aires": "en/ciudad-autonoma-de-buenos-aires/buenos-aires_777009/restaurants",
    "sao-paulo": "en/sao-paulo-region/sao-paulo/restaurants",
}

# Language-specific city URL path overrides.
CITY_LANGUAGE_PATH_OVERRIDES = {
    "zh_TW": {
        "hong-kong": "tw/zh_TW/hong-kong-region/hong-kong/restaurants",
        "macau": "tw/zh_TW/macau-region/macau/restaurants",
        "taipei": "tw/zh_TW/taipei-region/restaurants",
        "taichung": "tw/zh_TW/taichung-region/restaurants",
        "tainan": "tw/zh_TW/tainan-region/restaurants",
        "kaohsiung": "tw/zh_TW/kaohsiung-region/restaurants",
        "hsinchu": "tw/zh_TW/northern-taiwan/hsinchu-city_2853126/restaurants",
        "tokyo": "tw/zh_TW/tokyo-region/tokyo/restaurants",
        "osaka": "tw/zh_TW/osaka-region/osaka/restaurants",
        "kyoto": "tw/zh_TW/kyoto-region/kyoto/restaurants",
    },
    "zh_HK": {
        "hong-kong": "hk/zh_HK/hong-kong-region/hong-kong/restaurants",
        "macau": "hk/zh_HK/macau-region/macau/restaurants",
        "tokyo": "hk/zh_HK/tokyo-region/tokyo/restaurants",
        "osaka": "hk/zh_HK/osaka-region/osaka/restaurants",
        "kyoto": "hk/zh_HK/kyoto-region/kyoto/restaurants",
    },
}


COUNTRY_LANGUAGE_PATH_OVERRIDES = {
    "zh_TW": {
        "tw": "tw/zh_TW/selection/taiwan/restaurants",
        "jp": "tw/zh_TW/selection/japan/restaurants",
        "fr": "tw/zh_TW/selection/france/restaurants",
        "th": "tw/zh_TW/selection/thailand/restaurants",
        "us": "tw/zh_TW/selection/united-states/restaurants",
        "gb": "tw/zh_TW/selection/united-kingdom/restaurants",
        "sg": "tw/zh_TW/selection/singapore/restaurants",
    },
    "zh_HK": {
        "hk": "hk/zh_HK/selection/hong-kong/restaurants",
        "mo": "hk/zh_HK/selection/macao/restaurants",
        "jp": "hk/zh_HK/selection/japan/restaurants",
        "tw": "hk/zh_HK/selection/taiwan/restaurants",
    },
}


# When a requested language has no specific URL override for a target, try these
# fallback languages in order before falling back to the generic path template.
_LANGUAGE_URL_FALLBACKS: dict[str, tuple[str, ...]] = {
    "zh_TW": ("zh_HK",),
    "zh_HK": ("zh_TW",),
    "zh_CN": ("zh_TW", "zh_HK"),
}


def _slug_to_label(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-"))


def _join_guide_url(path: str) -> str:
    return f"{MICHELIN_BASE_URL}/{path.lstrip('/')}"


def _build_country_url(country_code: str, language: str = DEFAULT_LANGUAGE) -> str:
    language_overrides = COUNTRY_LANGUAGE_PATH_OVERRIDES.get(language, {})
    if country_code in language_overrides:
        return _join_guide_url(language_overrides[country_code])
    for fallback_language in _LANGUAGE_URL_FALLBACKS.get(language, ()):
        fallback_overrides = COUNTRY_LANGUAGE_PATH_OVERRIDES.get(fallback_language, {})
        if country_code in fallback_overrides:
            return _join_guide_url(fallback_overrides[country_code])
    return _join_guide_url(f"{language}/{country_code}/restaurants")


def _replace_path_language(path: str, language: str) -> str:
    path_segments = path.split("/", maxsplit=1)
    if len(path_segments) == 1:
        return f"{language}/{path_segments[0]}"
    return f"{language}/{path_segments[1]}"


def _build_city_url(path: str, language: str = DEFAULT_LANGUAGE) -> str:
    return _join_guide_url(_replace_path_language(path, language))


def _resolve_city_path(city_slug: str, language: str) -> str:
    language_overrides = CITY_LANGUAGE_PATH_OVERRIDES.get(language, {})
    if city_slug in language_overrides:
        return language_overrides[city_slug]
    for fallback_language in _LANGUAGE_URL_FALLBACKS.get(language, ()):
        fallback_overrides = CITY_LANGUAGE_PATH_OVERRIDES.get(fallback_language, {})
        if city_slug in fallback_overrides:
            return fallback_overrides[city_slug]
    return _replace_path_language(CITY_PATHS[city_slug], language)


def _resolve_city_label(city_slug: str, language: str) -> str:
    language_overrides = CITY_LANGUAGE_LABEL_OVERRIDES.get(language, {})
    if city_slug in language_overrides:
        return language_overrides[city_slug]
    return CITY_LABELS[city_slug]


def _resolve_country_label(country_slug: str, language: str) -> str:
    language_overrides = COUNTRY_LANGUAGE_LABEL_OVERRIDES.get(language, {})
    if country_slug in language_overrides:
        return language_overrides[country_slug]
    return COUNTRY_LABELS[country_slug]


COUNTRY_URLS = {
    country_slug: _build_country_url(country_code)
    for country_slug, country_code in COUNTRY_CODES.items()
}

CITY_URLS = {
    city_slug: _build_city_url(path)
    for city_slug, path in CITY_PATHS.items()
}

COUNTRY_LABELS = {
    country_slug: _slug_to_label(country_slug)
    for country_slug in COUNTRY_CODES
}
COUNTRY_LABELS.update(
    {
        "united-states": "United States",
        "united-kingdom": "United Kingdom",
        "south-korea": "South Korea",
        "china-mainland": "China Mainland",
        "hong-kong": "Hong Kong",
        "united-arab-emirates": "United Arab Emirates",
        "saudi-arabia": "Saudi Arabia",
    }
)

CITY_LABELS = {
    city_slug: _slug_to_label(city_slug)
    for city_slug in CITY_PATHS
}
CITY_LABELS.update(
    {
        "hong-kong": "Hong Kong",
        "kuala-lumpur": "Kuala Lumpur",
        "ho-chi-minh-city": "Ho Chi Minh City",
        "da-nang": "Da Nang",
        "abu-dhabi": "Abu Dhabi",
        "new-york": "New York",
        "san-francisco": "San Francisco",
        "washington-dc": "Washington DC",
        "los-angeles": "Los Angeles",
        "buenos-aires": "Buenos Aires",
        "sao-paulo": "Sao Paulo",
    }
)

CITY_LANGUAGE_LABEL_OVERRIDES = {
    "zh_TW": {
        "taipei": "\u81fa\u5317",
        "taichung": "\u81fa\u4e2d",
        "tainan": "\u81fa\u5357",
        "kaohsiung": "\u9ad8\u96c4",
        "hsinchu": "\u65b0\u7af9",
        "hong-kong": "\u9999\u6e2f",
        "macau": "\u6fb3\u9580",
        "tokyo": "\u6771\u4eac",
        "osaka": "\u5927\u962a",
        "kyoto": "\u4eac\u90fd",
    },
    "zh_HK": {
        "tokyo": "\u6771\u4eac",
        "osaka": "\u5927\u962a",
        "kyoto": "\u4eac\u90fd",
    },
}

COUNTRY_LANGUAGE_LABEL_OVERRIDES = {
    "zh_TW": {
        "taiwan": "\u81fa\u7063",
        "hong-kong": "\u9999\u6e2f",
        "macau": "\u6fb3\u9580",
        "japan": "\u65e5\u672c",
        "france": "\u6cd5\u570b",
        "thailand": "\u6cf0\u570b",
        "singapore": "\u65b0\u52a0\u5761",
    },
    "zh_HK": {
        "taiwan": "\u81fa\u7063",
        "hong-kong": "\u9999\u6e2f",
        "macau": "\u6fb3\u9580",
        "japan": "\u65e5\u672c",
    },
}

COUNTRY_ALIASES = {
    country_slug: country_slug
    for country_slug in COUNTRY_CODES
}
COUNTRY_ALIASES.update(
    {
        country_slug.replace("-", " "): country_slug
        for country_slug in COUNTRY_CODES
    }
)
COUNTRY_ALIASES.update(
    {
        country_code: country_slug
        for country_slug, country_code in COUNTRY_CODES.items()
    }
)
COUNTRY_ALIASES.update(
    {
        "usa": "united-states",
        "uk": "united-kingdom",
        "korea": "south-korea",
        "south korea": "south-korea",
        "china": "china-mainland",
        "mainland china": "china-mainland",
        "uae": "united-arab-emirates",
    }
)

CITY_ALIASES = {
    city_slug: city_slug
    for city_slug in CITY_PATHS
}
CITY_ALIASES.update(
    {
        city_slug.replace("-", " "): city_slug
        for city_slug in CITY_PATHS
    }
)
CITY_ALIASES.update(
    {
        "hk": "hong-kong",
        "hkg": "hong-kong",
        "nyc": "new-york",
        "sf": "san-francisco",
        "la": "los-angeles",
        "dc": "washington-dc",
        "washington dc": "washington-dc",
        "kl": "kuala-lumpur",
        "hcmc": "ho-chi-minh-city",
        "ho chi minh": "ho-chi-minh-city",
        "saigon": "ho-chi-minh-city",
        "sp": "sao-paulo",
    }
)

LANGUAGE_ALIASES = {
    "en": "en",
    "english": "en",
    "zh-cn": "zh_CN",
    "zh-tw": "zh_TW",
    "simplified-chinese": "zh_CN",
    "traditional-chinese": "zh_TW",
    "ja": "ja",
    "japanese": "ja",
    "ko": "ko",
    "korean": "ko",
    "fr": "fr",
    "french": "fr",
    "de": "de",
    "german": "de",
    "es": "es",
    "spanish": "es",
    "it": "it",
    "italian": "it",
}

SUPPORTED_LANGUAGES = ", ".join(sorted(set(LANGUAGE_ALIASES.values())))
LANGUAGE_VALUES_HELP = (
    f"Language values: {SUPPORTED_LANGUAGES}. "
    "You can also pass a Michelin Guide URL language segment directly."
)

SUPPORTED_COUNTRIES = ", ".join(sorted(COUNTRY_URLS.keys()))
SUPPORTED_CITIES = ", ".join(sorted(CITY_URLS.keys()))
SUPPORTED_COUNTRY_ALIASES = ", ".join(
    sorted(alias for alias, target in COUNTRY_ALIASES.items() if alias != target)
)
SUPPORTED_CITY_ALIASES = ", ".join(
    sorted(alias for alias, target in CITY_ALIASES.items() if alias != target)
)
COUNTRY_VALUES_HELP = (
    f"Country values: {SUPPORTED_COUNTRIES}. "
    f"Country aliases: {SUPPORTED_COUNTRY_ALIASES}."
)
CITY_VALUES_HELP = (
    f"City values: {SUPPORTED_CITIES}. "
    f"City aliases: {SUPPORTED_CITY_ALIASES}."
)
TARGET_VALUES_HELP = f"{COUNTRY_VALUES_HELP} {CITY_VALUES_HELP}"


@dataclass(frozen=True)
class ResolvedTarget:
    """Resolved target metadata used by scrape-sync workflows."""

    start_url: str
    scope_name: str
    local_language: str | None = None
    local_country_code: str | None = None


def normalize_target(value: str) -> str:
    """Normalize raw target text from CLI input."""

    return value.strip().lower()


def normalize_language(value: str) -> str:
    """Normalize raw language text from CLI input."""

    return value.strip()


def resolve_language(value: str) -> str:
    """Resolve language aliases and validate language input."""

    normalized_value = normalize_language(value)
    if not normalized_value:
        raise typer.BadParameter(f"Language cannot be empty. {LANGUAGE_VALUES_HELP}")

    alias_key = normalized_value.lower().replace("_", "-").replace(" ", "-")
    return LANGUAGE_ALIASES.get(alias_key, normalized_value)


def _get_local_language(country_code: str) -> str | None:
    """Return the primary local language code for a country."""

    # Map country codes to their primary Michelin Guide language segments.
    # This is used as a fallback for high-fidelity name lookups in Google Maps.
    local_languages = {
        "jp": "ja",
        "kr": "ko",
        "tw": "zh_TW",
        "hk": "zh_HK",
        "mo": "zh_HK",
        "cn": "zh_CN",
        "th": "th",
        "vn": "en",  # Vietnam's local guide is often en/vi, en is usually safe
        "my": "en",
        "sg": "en",
        "fr": "fr",
        "it": "it",
        "es": "es",
        "de": "de",
        "at": "de",
        "ch": "fr",  # Switzerland has multiple, fr/de/it; fr is common
        "be": "fr",
        "nl": "nl",
        "pt": "pt",
        "gr": "gr",
        "tr": "tr",
        "gb": "en",
        "ie": "en",
        "us": "en",
        "ca": "en",
        "br": "pt",
        "ar": "es",
    }
    return local_languages.get(country_code)


def resolve_target(value: str, language: str = DEFAULT_LANGUAGE) -> ResolvedTarget:
    """Resolve a normalized target value into URL and display label."""

    resolved_language = resolve_language(language)

    if value in CITY_ALIASES:
        city_slug = CITY_ALIASES[value]
        # Predefined cities are usually associated with a country vialeur initial paths.
        # For simplicity, we extract the country code from the CITY_PATHS if possible.
        city_path = CITY_PATHS[city_slug]
        # Path format: en/[country_code]/...
        path_segments = city_path.split("/")
        country_code = path_segments[1] if len(path_segments) > 1 else ""

        return ResolvedTarget(
            start_url=_join_guide_url(_resolve_city_path(city_slug, resolved_language)),
            scope_name=_resolve_city_label(city_slug, resolved_language),
            local_language=_get_local_language(country_code) if country_code else None,
            local_country_code=country_code if country_code else None,
        )

    if value in COUNTRY_ALIASES:
        country_slug = COUNTRY_ALIASES[value]
        country_code = COUNTRY_CODES[country_slug]
        return ResolvedTarget(
            start_url=_build_country_url(country_code, resolved_language),
            scope_name=_resolve_country_label(country_slug, resolved_language),
            local_language=_get_local_language(country_code),
            local_country_code=country_code,
        )

    raise typer.BadParameter(
        "Unsupported target. "
        f"{TARGET_VALUES_HELP} "
        "When the same value exists in both lists, the city target is used."
    )
