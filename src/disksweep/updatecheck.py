"""Optional update check. **Off by default**; only runs on explicit request.

This is the only code in DiskSweep that performs a network request. It sends a
single anonymous GET to the GitHub releases API: no identifiers, no telemetry.
"""

from __future__ import annotations

import json
import urllib.request

from disksweep.version import __version__

RELEASES_URL = "https://api.github.com/repos/muddasirfaiyaj66/DiskSweep/releases/latest"


def _parse(version: str) -> tuple[int, ...]:
    parts = []
    for piece in version.lstrip("vV").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def is_newer(latest: str, current: str = __version__) -> bool:
    """True if ``latest`` is a higher version than ``current``."""
    return _parse(latest) > _parse(current)


def check_for_update(timeout: float = 5.0, url: str = RELEASES_URL) -> str | None:
    """Return the newer version tag if one exists, else None. Network errors -> None."""
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "DiskSweep"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except (OSError, ValueError):
        return None
    tag = str(data.get("tag_name") or "")
    return tag if tag and is_newer(tag) else None
