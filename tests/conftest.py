"""Shared fixtures. All tests use temporary directories and mock Windows-only calls."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from disksweep import osutils
from disksweep.safety import PathGuard, default_guard
from disksweep.targets.base import TargetContext


def make_tree(root: Path, files: dict[str, bytes | str]) -> None:
    """Create files (and parent folders) under ``root``."""
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            content = content.encode()
        p.write_bytes(content)


def make_old(path: Path, days: float = 30) -> None:
    """Back-date a file's mtime/atime (and creation time on Windows).

    DiskSweep's age filter uses the newer of modification and creation time, so
    on Windows the creation time must be back-dated too.
    """
    import time

    t = time.time() - days * 86400
    os.utime(path, (t, t))
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        handle = kernel32.CreateFileW(
            str(path), 0x100, 0x7, None, 3, 0x02000000, None
        )  # FILE_WRITE_ATTRIBUTES, share all, OPEN_EXISTING, BACKUP_SEMANTICS
        if handle in (None, wintypes.HANDLE(-1).value):
            raise OSError(ctypes.get_last_error(), "CreateFileW failed")
        try:
            ticks = int((t + 11644473600) * 10_000_000)
            ft = wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)
            if not kernel32.SetFileTime(wintypes.HANDLE(handle), ctypes.byref(ft), None, None):
                raise OSError(ctypes.get_last_error(), "SetFileTime failed")
        finally:
            kernel32.CloseHandle(wintypes.HANDLE(handle))


def try_symlink(target: Path, link: Path, *, directory: bool) -> None:
    """Create a symlink or (on Windows) a junction; skip the test if impossible."""
    try:
        os.symlink(target, link, target_is_directory=directory)
        return
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32" and directory:
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
            return
        except (OSError, AttributeError):
            pass
    pytest.skip("cannot create symlinks/junctions on this system")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep audit logs inside tmp and stub every Windows/system call."""
    monkeypatch.setenv("DISKSWEEP_HOME", str(tmp_path / "_disksweep_home"))
    monkeypatch.setattr(osutils, "running_processes", lambda names: set())
    monkeypatch.setattr(osutils, "service_running", lambda name: False)
    monkeypatch.setattr(osutils, "stop_service", lambda name: True)
    monkeypatch.setattr(osutils, "start_service", lambda name: True)
    monkeypatch.setattr(osutils, "is_admin", lambda: False)

    def no_commands(*args: object, **kwargs: object) -> osutils.CommandResult:
        raise AssertionError(f"unexpected external command in tests: {args}")

    monkeypatch.setattr(osutils, "run_cmd", no_commands)


@pytest.fixture
def guard(tmp_path: Path) -> PathGuard:
    """The real default guard for this OS, plus a protected folder inside tmp."""
    protected = tmp_path / "protected"
    protected.mkdir()
    return default_guard(extra_protected=[str(protected)])


@pytest.fixture
def ctx(guard: PathGuard, tmp_path: Path) -> TargetContext:
    """Target context whose environment points into tmp."""
    env = {
        "TEMP": str(tmp_path / "env" / "Temp"),
        "LOCALAPPDATA": str(tmp_path / "env" / "Local"),
        "APPDATA": str(tmp_path / "env" / "Roaming"),
        "USERPROFILE": str(tmp_path / "env" / "Profile"),
        "SystemRoot": str(tmp_path / "env" / "Windows"),
        "SystemDrive": str(tmp_path / "env" / "Drive"),
        "ProgramData": str(tmp_path / "env" / "ProgramData"),
    }
    return TargetContext(guard=guard, environ=env, is_admin=True)
