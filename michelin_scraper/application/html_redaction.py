"""Shared HTML de-identification helpers for debug artifacts and fixtures."""

from __future__ import annotations

import re

_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
        "<redacted-email>",
    ),
    (
        re.compile(r"(?i)([?&](?:access_token|id_token|token|session|sid|oauth|code)=)[^&#\"'\s]+"),
        r"\1<redacted>",
    ),
    (
        re.compile(
            r"(?i)(\b(?:sid|hsid|ssid|apisid|sapisid|sidcc|__Secure-1PSID|__Secure-3PSID)\s*=\s*)[^;\"'\s]+"
        ),
        r"\1<redacted>",
    ),
    (
        re.compile(
            r'(?i)("(?:(?:sid|hsid|ssid|apisid|sapisid|sidcc|__Secure-1PSID|__Secure-3PSID|email|gaia_id))"\s*:\s*")[^"]*"'
        ),
        r'\1<redacted>"',
    ),
    (
        re.compile(r"(?i)/Users/[^/\"'<>\\\s]+"),
        "/Users/<redacted-user>",
    ),
)

_UNREDACTED_MARKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "email",
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    ),
    (
        "query-token",
        re.compile(r"(?i)[?&](?:access_token|id_token|token|session|sid|oauth|code)=(?!<redacted>)[^&#\"'\s]+"),
    ),
    (
        "cookie-token",
        re.compile(
            r"(?i)\b(?:sid|hsid|ssid|apisid|sapisid|sidcc|__Secure-1PSID|__Secure-3PSID)\s*=\s*(?!<redacted>)[^;\"'\s]+"
        ),
    ),
    (
        "json-token",
        re.compile(
            r'(?i)"(?:sid|hsid|ssid|apisid|sapisid|sidcc|__Secure-1PSID|__Secure-3PSID|email|gaia_id)"\s*:\s*"(?!<redacted>)[^"]+"'
        ),
    ),
    (
        "user-path",
        re.compile(r"(?i)/Users/(?!<redacted-user>)[^/\"'<>\\\s]+"),
    ),
)


def redact_html_text(html_text: str) -> str:
    """Redact sensitive tokens from raw HTML text."""

    redacted_text = str(html_text)
    for pattern, replacement in _REDACTION_PATTERNS:
        redacted_text = pattern.sub(replacement, redacted_text)
    return redacted_text


def find_unredacted_sensitive_markers(html_text: str) -> tuple[str, ...]:
    """Return marker labels that still look sensitive after redaction."""

    markers: list[str] = []
    for label, pattern in _UNREDACTED_MARKER_PATTERNS:
        if pattern.search(html_text):
            markers.append(label)
    return tuple(markers)
