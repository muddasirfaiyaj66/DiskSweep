"""Append-only audit log of every deletion.

One JSON object per line (JSONL) in ``<data dir>/logs/audit-YYYYMMDD.jsonl``::

    {"ts": "...", "event": "delete", "target": "user_temp", "path": "...",
     "bytes": 1234, "mode": "direct"}
    {"ts": "...", "event": "run_summary", "free_before": ..., "free_after": ..., ...}
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO


def data_dir() -> Path:
    """Directory for DiskSweep's own files (overridable with ``DISKSWEEP_HOME``)."""
    override = os.environ.get("DISKSWEEP_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "DiskSweep"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "disksweep"


def log_dir() -> Path:
    """Directory holding audit logs."""
    return data_dir() / "logs"


class AuditLog:
    """Thread-safe JSONL audit writer.

    Args:
        path: log file; defaults to today's file in :func:`log_dir`.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            path = log_dir() / f"audit-{datetime.now():%Y%m%d}.jsonl"
        self.path = Path(path)
        self._lock = threading.Lock()
        self._fh: TextIO | None = None

    def _write(self, payload: dict[str, Any]) -> None:
        payload = {"ts": datetime.now(UTC).isoformat(), **payload}
        line = json.dumps(payload, default=str, ensure_ascii=False)
        with self._lock:
            if self._fh is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = self.path.open("a", encoding="utf-8")
            self._fh.write(line + "\n")

    def record_delete(self, target: str, path: str, size: int, mode: str) -> None:
        """Record one deleted path."""
        self._write(
            {"event": "delete", "target": target, "path": path, "bytes": size, "mode": mode}
        )

    def record_event(self, event: str, **fields: Any) -> None:
        """Record any other event (command run, service stop, run summary...)."""
        self._write({"event": event, **fields})

    def flush(self) -> None:
        """Flush buffered lines to disk."""
        with self._lock:
            if self._fh is not None:
                self._fh.flush()

    def close(self) -> None:
        """Close the file handle."""
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    def __enter__(self) -> AuditLog:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def tail(n: int = 50) -> list[str]:
    """Return the last ``n`` lines across the most recent audit logs."""
    files = sorted(log_dir().glob("audit-*.jsonl"))
    lines: list[str] = []
    for f in reversed(files):
        try:
            content = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        lines = content + lines
        if len(lines) >= n:
            break
    return lines[-n:]
