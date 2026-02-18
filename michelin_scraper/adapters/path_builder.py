"""Filesystem path builders for sync state artifacts."""

import re
import time
from pathlib import Path

DEFAULT_STATE_DIR_NAME = ".michelin-state"


def safe_filename(value: str) -> str:
    """Normalize arbitrary names into safe filename segments."""

    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower())
    cleaned = cleaned.strip("-")
    return cleaned or "michelin"


def resolve_state_dir(state_dir: str) -> Path:
    """Resolve base directory for sync state files and ensure it exists.

    When state_dir is empty, defaults to ``.michelin-state`` in the current
    working directory so that all runtime artifacts are collected in one
    git-ignored folder rather than scattered across the project root.
    """

    if state_dir:
        base_dir = Path(state_dir).expanduser()
    else:
        base_dir = Path.cwd() / DEFAULT_STATE_DIR_NAME
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def resolve_checkpoint_path(output_dir: str, scope_name: str) -> Path:
    """Resolve checkpoint JSON path and ensure directory exists."""

    safe_scope = safe_filename(scope_name)
    filename = f"{safe_scope}-michelin-checkpoint.json"
    base_dir = resolve_state_dir(output_dir)
    path = base_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def resolve_error_report_path(state_dir: str, scope_name: str) -> Path:
    """Resolve error report path for sync failures."""

    safe_scope = safe_filename(scope_name)
    base_dir = resolve_state_dir(state_dir)
    return base_dir / f"{safe_scope}-maps-sync-errors.jsonl"


def resolve_debug_html_path(
    state_dir: str,
    scope_name: str,
    *,
    context: str,
) -> Path:
    """Resolve debug HTML snapshot path for sync failures."""

    safe_scope = safe_filename(scope_name)
    safe_context = safe_filename(context)
    timestamp_ms = int(time.time() * 1000)
    base_dir = resolve_state_dir(state_dir) / "debug"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"{safe_scope}-maps-debug-{safe_context}-{timestamp_ms}.html"
