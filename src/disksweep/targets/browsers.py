"""Web browser cache targets. Each is skipped while its browser is running."""

from __future__ import annotations

from disksweep.models import Risk
from disksweep.targets.base import CleanupTarget, DirectoryTarget, PathSpec

_CHROMIUM_CACHE_DIRS = (
    ("Cache",),
    ("Code Cache",),
    ("GPUCache",),
    ("Service Worker", "CacheStorage"),
    ("Service Worker", "ScriptCache"),
)


def _chromium_specs(*user_data: str) -> tuple[PathSpec, ...]:
    """Cache folders for every profile (Default, Profile 1, ...) of a Chromium browser."""
    specs: list[PathSpec] = [("LOCALAPPDATA", *user_data, "*", *d) for d in _CHROMIUM_CACHE_DIRS]
    specs += [
        ("LOCALAPPDATA", *user_data, "ShaderCache"),
        ("LOCALAPPDATA", *user_data, "GrShaderCache"),
    ]
    return tuple(specs)


class BrowserCache(DirectoryTarget):
    category = "browser"
    risk = Risk.SAFE


class ChromeCache(BrowserCache):
    id = "chrome_cache"
    name = "Google Chrome cache"
    description = "Cached web content for all Chrome profiles (not history, passwords or cookies)."
    process_names = ("chrome.exe",)
    specs = _chromium_specs("Google", "Chrome", "User Data")


class EdgeCache(BrowserCache):
    id = "edge_cache"
    name = "Microsoft Edge cache"
    description = "Cached web content for all Edge profiles (not history, passwords or cookies)."
    process_names = ("msedge.exe",)
    specs = _chromium_specs("Microsoft", "Edge", "User Data")


class BraveCache(BrowserCache):
    id = "brave_cache"
    name = "Brave cache"
    description = "Cached web content for all Brave profiles (not history, passwords or cookies)."
    process_names = ("brave.exe",)
    specs = _chromium_specs("BraveSoftware", "Brave-Browser", "User Data")


class FirefoxCache(BrowserCache):
    id = "firefox_cache"
    name = "Mozilla Firefox cache"
    description = "Cached web content for all Firefox profiles (not history, passwords or cookies)."
    process_names = ("firefox.exe",)
    specs = (
        ("LOCALAPPDATA", "Mozilla", "Firefox", "Profiles", "*", "cache2"),
        ("LOCALAPPDATA", "Mozilla", "Firefox", "Profiles", "*", "startupCache"),
        ("LOCALAPPDATA", "Mozilla", "Firefox", "Profiles", "*", "thumbnails"),
    )


TARGETS: tuple[type[CleanupTarget], ...] = (ChromeCache, EdgeCache, FirefoxCache, BraveCache)
