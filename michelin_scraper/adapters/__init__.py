"""Infrastructure adapters for persistence and output files."""

from .checkpoint_store import JsonCheckpointStore, ResumeState
from .google_maps_driver import (
    GoogleMapsAuthRequiredError,
    GoogleMapsDependencyError,
    GoogleMapsError,
    GoogleMapsListAlreadyExistsError,
    GoogleMapsListMissingDuringRunError,
    GoogleMapsNoteWriteError,
    GoogleMapsPlaceAlreadySavedError,
    GoogleMapsSelectorError,
    GoogleMapsTransientError,
)
from .google_maps_sync_writer import DryRunSyncWriter, GoogleMapsSyncWriter
from .path_builder import (
    resolve_checkpoint_path,
    resolve_debug_html_path,
    resolve_error_report_path,
    resolve_state_dir,
    safe_filename,
)

__all__ = [
    "JsonCheckpointStore",
    "DryRunSyncWriter",
    "GoogleMapsAuthRequiredError",
    "GoogleMapsDependencyError",
    "GoogleMapsError",
    "GoogleMapsListAlreadyExistsError",
    "GoogleMapsListMissingDuringRunError",
    "GoogleMapsNoteWriteError",
    "GoogleMapsPlaceAlreadySavedError",
    "GoogleMapsSelectorError",
    "GoogleMapsSyncWriter",
    "GoogleMapsTransientError",
    "ResumeState",
    "resolve_checkpoint_path",
    "resolve_debug_html_path",
    "resolve_error_report_path",
    "resolve_state_dir",
    "safe_filename",
]
