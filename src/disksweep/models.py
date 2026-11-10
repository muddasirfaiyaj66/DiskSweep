"""Plain data structures shared by the core library, CLI and GUI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Risk(StrEnum):
    """How much care a cleanup target needs.

    * ``SAFE``: regenerable caches/temp data; worst case is a slower first launch.
    * ``MODERATE``: data that is re-downloaded or user-visible (e.g. Recycle Bin).
    * ``ADVANCED``: system-level changes that cannot be undone (Windows.old,
      DISM component cleanup, disabling hibernation, ...).
    """

    SAFE = "safe"
    MODERATE = "moderate"
    ADVANCED = "advanced"

    @property
    def rank(self) -> int:
        """Numeric ordering, lowest risk first."""
        return {"safe": 0, "moderate": 1, "advanced": 2}[self.value]


class DeleteMode(StrEnum):
    """How a path is removed."""

    DIRECT = "direct"  # permanent delete; used for caches/temp files
    RECYCLE = "recycle"  # send to Recycle Bin; used for user files


@dataclass
class SkippedItem:
    """A path that was not removed, and why."""

    path: str
    reason: str


@dataclass
class ScanResult:
    """Outcome of scanning one cleanup target."""

    target_id: str
    bytes: int = 0
    files: int = 0
    available: bool = True
    estimated: bool = False
    note: str = ""
    paths: list[str] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return asdict(self)


@dataclass
class CleanResult:
    """Outcome of cleaning one cleanup target."""

    target_id: str
    dry_run: bool = True
    bytes_freed: int = 0
    files_deleted: int = 0
    dirs_deleted: int = 0
    ran: bool = True
    note: str = ""
    skipped: list[SkippedItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return asdict(self)


@dataclass
class CleanReport:
    """Aggregate outcome of a cleanup run."""

    dry_run: bool
    results: list[CleanResult] = field(default_factory=list)
    free_before: int | None = None
    free_after: int | None = None
    drive: str = ""

    @property
    def total_bytes(self) -> int:
        """Bytes freed (or that would be freed, in a dry run)."""
        return sum(r.bytes_freed for r in self.results)

    @property
    def total_files(self) -> int:
        """Files deleted (or that would be deleted)."""
        return sum(r.files_deleted for r in self.results)

    @property
    def total_skipped(self) -> int:
        """Number of paths skipped across all targets."""
        return sum(len(r.skipped) for r in self.results)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return {
            "dry_run": self.dry_run,
            "drive": self.drive,
            "free_before": self.free_before,
            "free_after": self.free_after,
            "total_bytes": self.total_bytes,
            "total_files": self.total_files,
            "total_skipped": self.total_skipped,
            "results": [r.to_dict() for r in self.results],
        }
