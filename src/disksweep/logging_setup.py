"""Structured logging configuration.

Log calls attach structured fields with ``extra=``::

    log.info("deleted", extra={"path": p, "bytes": n})

The text formatter renders them as ``key=value`` pairs; the JSON formatter emits
one JSON object per line.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None)).keys()
) | {"message", "asctime", "taskName"}


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}


class KeyValueFormatter(logging.Formatter):
    """``2024-01-01T00:00:00 INFO disksweep.fsops deleted path=... bytes=...``."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).isoformat(timespec="seconds")
        base = f"{ts} {record.levelname:<7} {record.name} {record.getMessage()}"
        fields = " ".join(f"{k}={v!r}" for k, v in _extra_fields(record).items())
        line = f"{base} {fields}" if fields else base
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class JsonFormatter(logging.Formatter):
    """One JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(verbosity: int = 0, fmt: str = "text") -> None:
    """Configure the ``disksweep`` logger hierarchy.

    Args:
        verbosity: 0 = warnings, 1 = info, 2+ = debug.
        fmt: ``"text"`` or ``"json"``.
    """
    level = logging.WARNING if verbosity <= 0 else logging.INFO if verbosity == 1 else logging.DEBUG
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else KeyValueFormatter())
    logger = logging.getLogger("disksweep")
    logger.handlers[:] = [handler]
    logger.setLevel(level)
    logger.propagate = False
