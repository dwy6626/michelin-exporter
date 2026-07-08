"""Shared HTML de-identification helpers for debug artifacts and fixtures."""

from __future__ import annotations

import re

_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?is)(Google Account:\s*)"
            r"(?!<redacted-account-name>|<redacted-email>)[^\"<\\]*?"
            r"(\\u0026#10;)[^\"<\\]*?@[A-Z0-9.-]+\.[A-Z]{2,}"
        ),
        r"\1<redacted-account-name>\2<redacted-email>",
    ),
    (
        re.compile(
            r"(?is)[^\\\"<>]{1,120}?"
            r"(\\u003c/div\\u003e\\u003cdiv\\u003e)"
            r"[^\"<\\]*?@[A-Z0-9.-]+\.[A-Z]{2,}"
        ),
        r"<redacted-account-name>\1<redacted-email>",
    ),
    (
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
        "<redacted-email>",
    ),
    (
        re.compile(r"AIza(?!SyFAKE_KEY_FOR_TESTING_ONLY_00000000)[0-9A-Za-z_-]{20,}"),
        "AIza<redacted>",
    ),
    (
        re.compile(
            r"(?i)([?&](?:amp;)?key=)"
            r"(?!AIzaSyFAKE_KEY_FOR_TESTING_ONLY_00000000(?:[&#\"'\s]|$))"
            r"(?:AIza[0-9A-Za-z_-]{20,}|[^&#\"'\s]+)"
        ),
        r"\1<redacted>",
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
    (
        re.compile(
            r"(?i)((?:https?:)?//lh3\.googleusercontent\.com/ogw/)"
            r"(?!<redacted-account-avatar>)[^\"'&<>\s)]+"
        ),
        r"\1<redacted-account-avatar>",
    ),
    (
        re.compile(
            r"(?i)(\blh3\.googleusercontent\.com/ogw/)"
            r"(?!<redacted-account-avatar>)[^\"'&<>\s)]+"
        ),
        r"\1<redacted-account-avatar>",
    ),
    (
        re.compile(r'(?s)(<div class="gb_g">)[^<]*(</div>)'),
        r"\1<redacted-account-name>\2",
    ),
    (
        re.compile(
            r'(?is)(aria-label="Google Account:)\s*'
            r'(?!<redacted-account-name>|<redacted-email>)([^"<]*?)(\s*<redacted-email>)(")'
        ),
        r"\1 <redacted-account-name>\3\4",
    ),
    (
        re.compile(
            r'(?is)(aria-label="Google Account:)\s*'
            r'(?!<redacted-account-name>|<redacted-email>)[^"]*(")'
        ),
        r"\1 <redacted-account-name>\2",
    ),
    (
        re.compile(
            r"(?is)(Google Account:\s*)"
            r"(?!<redacted-account-name>|<redacted-email>)[^\"<\\]*?"
            r"(\\u0026#10;\s*<redacted-email>)"
        ),
        r"\1<redacted-account-name>\2",
    ),
    (
        re.compile(
            r"(?is)[^\\\"<>]{1,120}?"
            r"(\\u003c/div\\u003e\\u003cdiv\\u003e<redacted-email>)"
        ),
        r"<redacted-account-name>\1",
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
        "google-api-key",
        re.compile(r"AIza(?!<redacted>|SyFAKE_KEY_FOR_TESTING_ONLY_00000000)[0-9A-Za-z_-]{20,}"),
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
    (
        "google-account-avatar-url",
        re.compile(
            r"(?i)(?:(?:https?:)?//)?lh3\.googleusercontent\.com/ogw/"
            r"(?!<redacted-account-avatar>)[^\"'&<>\s)]+"
        ),
    ),
    (
        "google-account-name",
        re.compile(r'(?s)<div class="gb_g">(?!<redacted-account-name>)[^<]+</div>'),
    ),
    (
        "google-account-label",
        re.compile(
            r'(?is)aria-label="Google Account:(?!\s*(?:<redacted-account-name>|<redacted-email>))[^"]+?"'
        ),
    ),
    (
        "google-account-escaped-label",
        re.compile(
            r"(?is)Google Account:\s*"
            r"(?!<redacted-account-name>|<redacted-email>)[^\"<\\]*?"
            r"\\u0026#10;\s*(?:<redacted-email>|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})"
        ),
    ),
    (
        "google-account-escaped-name",
        re.compile(
            r"(?is)[^\\\"<>]{1,120}?"
            r"\\u003c/div\\u003e\\u003cdiv\\u003e"
            r"(?:<redacted-email>|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})"
        ),
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
