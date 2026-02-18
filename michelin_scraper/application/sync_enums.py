"""Application-level enums for Google Maps sync workflows."""
from enum import Enum


class SyncRowStatus(Enum):
    """Status of a single row sync operation."""

    ADDED = "added"
    SKIPPED = "skipped"
    FAILED = "failed"
