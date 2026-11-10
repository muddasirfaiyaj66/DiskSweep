"""Protected paths and symlink escapes must always be blocked."""

from __future__ import annotations

import ntpath
import os
from pathlib import Path

import pytest

from conftest import try_symlink
from disksweep.safety import (
    PathEscapeError,
    PathGuard,
    ProtectedPathError,
    default_guard,
    windows_protection,
)

WIN_ENV = {
    "SystemDrive": "C:",
    "SystemRoot": r"C:\Windows",
    "windir": r"C:\Windows",
    "ProgramFiles": r"C:\Program Files",
    "ProgramFiles(x86)": r"C:\Program Files (x86)",
    "ProgramData": r"C:\ProgramData",
    "USERPROFILE": r"C:\Users\alice",
    "LOCALAPPDATA": r"C:\Users\alice\AppData\Local",
    "APPDATA": r"C:\Users\alice\AppData\Roaming",
    "TEMP": r"C:\Users\alice\AppData\Local\Temp",
    "PUBLIC": r"C:\Users\Public",
    "OneDrive": r"C:\Users\alice\OneDrive",
}


@pytest.fixture
def win_guard() -> PathGuard:
    """Windows rules evaluated with ntpath, so they are testable on any OS."""
    known = {"Documents": r"D:\Redirected\Docs"}
    trees, anchors, allow = windows_protection(WIN_ENV, known)
    return PathGuard(trees, anchors, allow, pathmod=ntpath, resolve=ntpath.abspath)


# --------------------------------------------------------------------------- #
# Windows rules (pure path logic)
# --------------------------------------------------------------------------- #

BLOCKED = [
    r"C:\\",
    r"C:\Windows",
    r"C:\Windows\System32",
    r"C:\Windows\System32\drivers\etc\hosts",
    r"C:\Windows\SysWOW64\kernel32.dll",
    r"C:\Windows\WinSxS\x86_foo",
    r"C:\Windows\explorer.exe",
    r"C:\Program Files",
    r"C:\Program Files\App\app.exe",
    r"C:\Program Files (x86)\Steam\steam.exe",
    r"C:\ProgramData",
    r"C:\ProgramData\Microsoft\Crypto\key",
    r"C:\Users",
    r"C:\Users\alice",
    r"C:\Users\alice\AppData",
    r"C:\Users\alice\AppData\Local",
    r"C:\Users\alice\AppData\Local\Temp",  # contents yes, folder itself no
    r"C:\Users\alice\Documents",
    r"C:\Users\alice\Documents\thesis.docx",
    r"C:\Users\alice\Desktop\notes.txt",
    r"C:\Users\alice\Pictures\2023\img.jpg",
    r"C:\Users\alice\Videos\clip.mp4",
    r"C:\Users\alice\Music\song.mp3",
    r"C:\Users\alice\OneDrive\work.xlsx",
    r"C:\Users\alice\.ssh\id_ed25519",
    r"C:\Users\Public\Documents\x",
    r"C:\pagefile.sys",
    r"C:\hiberfil.sys",
    r"C:\$Recycle.Bin\S-1-5\file",
    r"C:\System Volume Information\x",
    r"D:\Redirected\Docs\report.pdf",  # known-folder redirect
    r"C:\Windows\SoftwareDistribution\Download",  # allow-listed folder itself
]

ALLOWED = [
    r"C:\Users\alice\AppData\Local\Temp\tmp1234.tmp",
    r"C:\Users\alice\AppData\Local\Temp\sub\dir\file",
    r"C:\Windows\Temp\cab_123.tmp",
    r"C:\Windows\SoftwareDistribution\Download\abc\update.cab",
    r"C:\Windows\Minidump\012345.dmp",
    r"C:\Windows\MEMORY.DMP",
    r"C:\Windows\Logs\CBS\old.log",
    r"C:\ProgramData\Microsoft\Windows\WER\ReportArchive\x",
    r"C:\Users\alice\AppData\Local\Google\Chrome\User Data\Default\Cache\f_0001",
    r"C:\Users\alice\.gradle\caches\modules-2\x.jar",
    r"C:\Windows.old\Windows\x",
    r"C:\Windows.old",
    r"D:\Downloads\dup.iso",
]


@pytest.mark.parametrize("path", BLOCKED)
def test_windows_protected_paths_blocked(win_guard: PathGuard, path: str) -> None:
    with pytest.raises(ProtectedPathError):
        win_guard.check(path)


@pytest.mark.parametrize("path", ALLOWED)
def test_windows_cache_paths_allowed(win_guard: PathGuard, path: str) -> None:
    win_guard.check(path)


@pytest.mark.parametrize(
    "path",
    [
        r"c:\WINDOWS\system32\config\SAM",  # case
        r"C:\Users\alice\AppData\Local\Temp\..\..\..\Documents\a.txt",  # traversal
        r"C:\Windows\Temp\..\System32\ntoskrnl.exe",
        r"C:/Program Files/App/x.dll",  # forward slashes
        r"C:\Windows\System32\.\drivers",
    ],
)
def test_windows_tricks_blocked(win_guard: PathGuard, path: str) -> None:
    with pytest.raises(ProtectedPathError):
        win_guard.check(path)


def test_windows_old_is_not_confused_with_windows(win_guard: PathGuard) -> None:
    # "C:\Windows.old" starts with "C:\Windows" as a string but is a sibling.
    assert win_guard.is_allowed(r"C:\Windows.old\x")
    assert not win_guard.is_allowed(r"C:\Windows\x")


def test_within_containment(win_guard: PathGuard) -> None:
    root = r"C:\Users\alice\AppData\Local\Temp"
    win_guard.check(root + r"\a\b.txt", within=root)
    with pytest.raises(PathEscapeError):
        win_guard.check(r"C:\Users\alice\AppData\Local\Other\x", within=root)
    with pytest.raises(PathEscapeError):
        win_guard.check(root + r"\..\Other\x", within=root)
    with pytest.raises(PathEscapeError):  # prefix trick: Temp2 is not inside Temp
        win_guard.check(root + r"2\x", within=root)


def test_resolved_link_target_is_checked() -> None:
    """A path whose *resolution* lands in a protected folder is blocked."""
    trees, anchors, allow = windows_protection(WIN_ENV)
    link = r"C:\Users\alice\AppData\Local\Temp\evil"
    mapping = {link: r"C:\Users\alice\Documents"}

    def fake_resolve(p: str) -> str:
        for src, dst in mapping.items():
            if ntpath.normcase(p).startswith(ntpath.normcase(src)):
                return dst + p[len(src) :]
        return ntpath.abspath(p)

    g = PathGuard(trees, anchors, allow, pathmod=ntpath, resolve=fake_resolve)
    root = r"C:\Users\alice\AppData\Local\Temp"
    with pytest.raises(ProtectedPathError):
        g.check(link + r"\secret.docx", within=root)
    # Removing the link itself (not following it) is fine.
    g.check(link, within=root, link=True)


def test_environment_cannot_remove_protection() -> None:
    """Even with an empty environment, hard-coded C:\\ fallbacks stay protected."""
    trees, anchors, allow = windows_protection({})
    g = PathGuard(trees, anchors, allow, pathmod=ntpath, resolve=ntpath.abspath)
    for p in (r"C:\Windows\System32\x", r"C:\Program Files\x", r"C:\\", r"C:\Users"):
        assert not g.is_allowed(p)


def test_extra_protected_only_adds(tmp_path: Path) -> None:
    g = default_guard(extra_protected=[str(tmp_path / "mine")])
    assert not g.is_allowed(str(tmp_path / "mine" / "file"))
    assert g.is_allowed(str(tmp_path / "other" / "file"))


# --------------------------------------------------------------------------- #
# Real filesystem (this OS)
# --------------------------------------------------------------------------- #


def test_real_symlink_escape_blocked(guard: PathGuard, tmp_path: Path) -> None:
    root = tmp_path / "cache"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "precious.txt").write_text("keep me")
    try_symlink(outside, root / "link", directory=True)

    with pytest.raises(PathEscapeError):
        guard.check(str(root / "link" / "precious.txt"), within=str(root))
    # The link entry itself may be removed, never followed.
    guard.check(str(root / "link"), within=str(root), link=True)


def test_real_symlink_into_protected_blocked(guard: PathGuard, tmp_path: Path) -> None:
    root = tmp_path / "cache"
    root.mkdir()
    protected = tmp_path / "protected"
    (protected / "doc.txt").write_text("secret")
    try_symlink(protected, root / "docs", directory=True)
    with pytest.raises(ProtectedPathError):
        guard.check(str(root / "docs" / "doc.txt"), within=str(root))


def test_default_guard_protects_os_locations() -> None:
    g = default_guard()
    if os.name == "nt":
        windir = os.environ.get("SystemRoot", r"C:\Windows")
        assert not g.is_allowed(os.path.join(windir, "System32", "kernel32.dll"))
        assert not g.is_allowed(os.path.join(os.environ["USERPROFILE"], "Documents", "x.txt"))
        assert not g.is_allowed(os.environ.get("ProgramFiles", r"C:\Program Files"))
    else:
        assert not g.is_allowed("/usr/bin/python3")
        assert not g.is_allowed("/etc/passwd")
        assert not g.is_allowed(os.path.expanduser("~/Documents/x.txt"))
    assert not g.is_allowed(os.path.abspath(os.sep))
    assert not g.is_allowed(os.path.expanduser("~"))


def test_guard_works_with_uppercased_environment() -> None:
    """dict(os.environ) upper-cases keys on Windows; protection must not weaken."""
    env = {"SYSTEMDRIVE": "E:", "SYSTEMROOT": r"E:\Windows", "USERPROFILE": r"E:\Users\bob"}
    g = default_guard(env, windows=True)
    assert not g.is_allowed(r"E:\Windows\System32\x.dll")
    assert not g.is_allowed(r"E:\Program Files\x")
    assert not g.is_allowed(r"E:\Users\bob\Documents\x")
    assert g.is_allowed(r"E:\Windows\Temp\x.tmp")


def test_envmap_case_insensitive() -> None:
    from disksweep.osutils import EnvMap

    m = EnvMap({"SystemRoot": r"C:\Windows"})
    assert m["SYSTEMROOT"] == m.get("systemroot") == r"C:\Windows"
    assert "SystemRoot" in m
