"""Human-friendly size parsing and formatting."""

from __future__ import annotations

import re

_UNITS: dict[str, int] = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "KIB": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "MIB": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
    "GIB": 1024**3,
    "T": 1024**4,
    "TB": 1024**4,
    "TIB": 1024**4,
}

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*$")


def parse_size(text: str | int) -> int:
    """Parse a size such as ``"10MB"``, ``"1.5 GB"`` or ``"4096"`` into bytes.

    Units are binary (1 KB = 1024 bytes), matching what Windows Explorer shows.

    Raises:
        ValueError: if the text is not a valid size.
    """
    if isinstance(text, int):
        if text < 0:
            raise ValueError("size must be non-negative")
        return text
    match = _SIZE_RE.match(text)
    if not match:
        raise ValueError(f"invalid size: {text!r}")
    number, unit = match.groups()
    unit = unit.upper()
    if unit not in _UNITS:
        raise ValueError(f"unknown size unit in {text!r}")
    return int(float(number) * _UNITS[unit])


def human_size(num_bytes: float) -> str:
    """Format a byte count as a short human-readable string (e.g. ``"1.4 GB"``)."""
    value = float(num_bytes)
    sign = "-" if value < 0 else ""
    value = abs(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{sign}{int(value)} B"
            return f"{sign}{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")  # pragma: no cover
