"""Console progress reporter implementation."""


from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)


class ProgressReporter:
    """Render progress updates to rich terminal output."""

    def __init__(self) -> None:
        self._console = Console()
        self._dynamic_enabled = self._console.is_terminal
        self._progress = Progress(
            SpinnerColumn(style="cyan"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            TextColumn("{task.description}"),
            console=self._console,
            transient=True,
            disable=not self._dynamic_enabled,
        )
        self._task_id: TaskID | None = None
        self._last_static_message: str = ""
        self._last_static_progress_bucket: int | None = None
        if self._dynamic_enabled:
            self._progress.start()
            self._task_id = self._progress.add_task("Starting scraper...", total=None)

    def _normalize(self, message: str) -> str:
        return " ".join(message.split())

    def update(self, message: str, progress: float | None = None) -> None:
        normalized = self._normalize(message)
        if self._dynamic_enabled and self._task_id is not None:
            if progress is None:
                self._progress.update(
                    self._task_id,
                    description=normalized,
                    total=None,
                )
            else:
                clamped = max(0.0, min(1.0, progress))
                self._progress.update(
                    self._task_id,
                    description=normalized,
                    total=100.0,
                    completed=clamped * 100.0,
                )
            return
        if progress is None:
            if normalized == self._last_static_message:
                return
            self._last_static_message = normalized
            print(f"[PROGRESS] {normalized}")
            return
        clamped = max(0.0, min(1.0, progress))
        progress_bucket = int(clamped * 20)
        if progress_bucket == self._last_static_progress_bucket:
            return
        self._last_static_progress_bucket = progress_bucket
        print(f"[PROGRESS] {clamped * 100:5.1f}% {normalized}")

    def log(self, message: str) -> None:
        normalized = self._normalize(message)
        if self._dynamic_enabled:
            # Keep progress bar pinned to the bottom while writing log lines above it.
            self._progress.print(normalized)
            return
        print(f"[INFO] {normalized}")

    def finish(self, message: str | None = None) -> None:
        if self._dynamic_enabled:
            self._progress.stop()
            self._task_id = None
        if message:
            self.log(message)
