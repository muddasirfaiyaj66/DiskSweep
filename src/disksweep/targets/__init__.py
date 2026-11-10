"""Cleanup targets."""

from disksweep.targets.base import (
    CleanupTarget,
    CommandTarget,
    DirectoryTarget,
    TargetContext,
)
from disksweep.targets.registry import all_targets, get_targets, target_ids

__all__ = [
    "CleanupTarget",
    "CommandTarget",
    "DirectoryTarget",
    "TargetContext",
    "all_targets",
    "get_targets",
    "target_ids",
]
