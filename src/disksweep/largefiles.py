"""Find large and/or old files."""

from __future__ import annotations

import heapq
import os
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from disksweep.fsops import iter_files


@dataclass
class LargeFile:
    """A file matching the large/old thresholds."""

    path: str
    bytes: int
    modified: float

    @property
    def age_days(self) -> float:
        """Days since last modification."""
        return (time.time() - self.modified) / 86400

    def to_dict(self) -> dict[str, object]:
        """JSON-serialisable dict."""
        return {**asdict(self), "age_days": round(self.age_days, 1)}


def find_large_files(
    roots: Sequence[str],
    min_size: int = 500 * 1024**2,
    *,
    older_than_days: float | None = None,
    top: int | None = 50,
    cancel: threading.Event | None = None,
    now: float | None = None,
) -> list[LargeFile]:
    """Return files at least ``min_size`` bytes (and optionally older than N days).

    Results are sorted largest first and limited to ``top`` (``None`` = unlimited).
    Symlinks and junctions are not followed.
    """
    now = time.time() if now is None else now
    cutoff = now - older_than_days * 86400 if older_than_days is not None else None
    found: list[tuple[int, str, float]] = []
    for root in roots:
        for path, st in iter_files(os.path.abspath(root), cancel):
            if st.st_size < min_size:
                continue
            if cutoff is not None and st.st_mtime > cutoff:
                continue
            item = (st.st_size, path, st.st_mtime)
            if top is None or len(found) < top:
                heapq.heappush(found, item)
            elif item > found[0]:
                heapq.heapreplace(found, item)
    return [LargeFile(p, s, m) for s, p, m in sorted(found, reverse=True)]
