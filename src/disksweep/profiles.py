"""Cleanup profiles: named sets of targets."""

from __future__ import annotations

from types import MappingProxyType

from disksweep.targets.registry import target_ids

SAFE: tuple[str, ...] = (
    "user_temp",
    "windows_temp",
    "crash_dumps",
    "crash_dumps_system",
    "thumbnail_cache",
    "chrome_cache",
    "edge_cache",
    "firefox_cache",
    "brave_cache",
)

DEVELOPER: tuple[str, ...] = (
    *SAFE,
    "npm_cache",
    "yarn_cache",
    "pnpm_store",
    "pip_cache",
    "nuget_cache",
    "nuget_packages",
    "gradle_cache",
    "maven_repo",
    "cargo_cache",
    "docker_prune",
)

#: Everything except ``hibernation_off``, which changes system behaviour
#: (disables hibernation and Fast Startup) and must be selected explicitly.
AGGRESSIVE: tuple[str, ...] = tuple(t for t in target_ids() if t != "hibernation_off")

PROFILES = MappingProxyType(
    {
        "safe": SAFE,
        "developer": DEVELOPER,
        "aggressive": AGGRESSIVE,
    }
)


def profile_targets(name: str) -> tuple[str, ...]:
    """Target ids for a profile name (case-insensitive).

    Raises:
        KeyError: for an unknown profile.
    """
    try:
        return PROFILES[name.lower()]
    except KeyError:
        raise KeyError(f"unknown profile {name!r}; choose from {', '.join(PROFILES)}") from None
