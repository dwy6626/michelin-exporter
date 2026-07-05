"""Google My Maps note formatting."""

import re
from collections.abc import Mapping
from typing import Any

MY_MAPS_NOTE_FORMAT_RAW = "raw"
MY_MAPS_NOTE_FORMAT_500BOWLS = "500bowls"
MY_MAPS_NOTE_FORMAT_TEMPLATE = "template"
MY_MAPS_NOTE_FORMAT_CHOICES = (
    MY_MAPS_NOTE_FORMAT_RAW,
    MY_MAPS_NOTE_FORMAT_500BOWLS,
    MY_MAPS_NOTE_FORMAT_TEMPLATE,
)

_TEMPLATE_FIELD_PATTERN = re.compile(r"\{([^{}]+)\}")
_BOWLS_500_BOWL_COUNT_KEYS = ("總得碗數", "總得晚數")
_MISSING_TEMPLATE_FIELD_MARKER = "\0missing-field\0"
_ESCAPED_OPEN_BRACE_MARKER = "\0open-brace\0"
_ESCAPED_CLOSE_BRACE_MARKER = "\0close-brace\0"


def build_my_maps_note_text(
    row: Mapping[str, Any],
    *,
    note_format: str,
    note_template: str,
) -> str:
    normalized_format = note_format.strip().lower() or MY_MAPS_NOTE_FORMAT_RAW
    if normalized_format == MY_MAPS_NOTE_FORMAT_RAW:
        fields = _description_fields(row)
        if fields:
            return _format_raw_fields_note(fields)
        return _normalize_note_text(row.get("Description", ""))
    if normalized_format == MY_MAPS_NOTE_FORMAT_500BOWLS:
        return _format_500bowls_note(_description_fields(row))
    if normalized_format == MY_MAPS_NOTE_FORMAT_TEMPLATE:
        return _format_template_note(_description_fields(row), note_template)
    allowed = ", ".join(MY_MAPS_NOTE_FORMAT_CHOICES)
    raise ValueError(f"Unsupported My Maps note format: {note_format!r}. Allowed values: {allowed}.")


def _description_fields(row: Mapping[str, Any]) -> Mapping[str, str]:
    value = row.get("DescriptionFields", {})
    if not isinstance(value, Mapping):
        return {}
    fields: dict[str, str] = {}
    for key, raw_field_value in value.items():
        normalized_key = _normalize_note_text(key)
        normalized_value = _sanitize_note_segment(raw_field_value)
        if normalized_key and normalized_value:
            fields[normalized_key] = normalized_value
    return fields


def _format_500bowls_note(fields: Mapping[str, str]) -> str:
    bowl_count = _first_field_value(fields, _BOWLS_500_BOWL_COUNT_KEYS)
    segments = [
        f"{bowl_count}碗" if bowl_count else "",
        fields.get("得獎菜色", ""),
        fields.get("菜系", ""),
        fields.get("推薦評審", ""),
        fields.get("地址", ""),
        fields.get("電話", ""),
    ]
    return _join_note_segments(segments)


def _format_raw_fields_note(fields: Mapping[str, str]) -> str:
    return _join_note_segments([f"{key}: {value}" for key, value in fields.items()])


def _format_template_note(fields: Mapping[str, str], note_template: str) -> str:
    if not note_template:
        return ""
    escaped_template = note_template.replace(
        "{{",
        _ESCAPED_OPEN_BRACE_MARKER,
    ).replace(
        "}}",
        _ESCAPED_CLOSE_BRACE_MARKER,
    )

    def replace_field(match: re.Match[str]) -> str:
        field_name = _normalize_note_text(match.group(1))
        return fields.get(field_name, _MISSING_TEMPLATE_FIELD_MARKER)

    rendered = _TEMPLATE_FIELD_PATTERN.sub(replace_field, escaped_template)
    rendered = rendered.replace(_ESCAPED_OPEN_BRACE_MARKER, "{").replace(
        _ESCAPED_CLOSE_BRACE_MARKER,
        "}",
    )
    return _cleanup_template_note(rendered)


def _first_field_value(fields: Mapping[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = fields.get(key, "")
        if value:
            return value
    return ""


def _join_note_segments(values: list[str]) -> str:
    return " | ".join(value for value in (_sanitize_note_segment(item) for item in values) if value)


def _cleanup_template_note(value: str) -> str:
    segments = [
        segment
        for segment in value.split("|")
        if _MISSING_TEMPLATE_FIELD_MARKER not in segment
    ]
    return _join_note_segments(segments)


def _normalize_note_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _sanitize_note_segment(value: Any) -> str:
    return _normalize_note_text(value).replace("|", "｜")
