"""Registry of all built-in cleanup targets."""

from __future__ import annotations

from disksweep.targets import advanced, browsers, dev, system
from disksweep.targets.base import CleanupTarget

_TARGET_CLASSES: tuple[type[CleanupTarget], ...] = (
    *system.TARGETS,
    *browsers.TARGETS,
    *dev.TARGETS,
    *advanced.TARGETS,
)


def all_targets() -> list[CleanupTarget]:
    """Fresh instances of every built-in target, in display order."""
    return [cls() for cls in _TARGET_CLASSES]


def target_ids() -> list[str]:
    """Ids of every built-in target."""
    return [cls.id for cls in _TARGET_CLASSES]


def get_targets(ids: list[str] | tuple[str, ...] | set[str]) -> list[CleanupTarget]:
    """Instances for the given ids, in registry order.

    Raises:
        KeyError: if an id is unknown.
    """
    known = set(target_ids())
    unknown = set(ids) - known
    if unknown:
        raise KeyError(f"unknown target(s): {', '.join(sorted(unknown))}")
    return [t for t in all_targets() if t.id in ids]
