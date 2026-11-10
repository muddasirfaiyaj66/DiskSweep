"""Path safety: the single gate every deletion must pass.

:class:`PathGuard` decides whether a path may be deleted. It is consulted by
:mod:`disksweep.fsops`, which is the *only* module in DiskSweep that removes
files. The rules are deliberately conservative and hard-coded:

1. **Protected trees** (``C:\\Windows``, ``System32``, ``Program Files``, the
   user's Documents/Desktop/Pictures/..., boot files, ...) can never be deleted,
   nor can anything inside them, except for a short hard-coded allow-list of
   well-known cache locations inside ``C:\\Windows`` and ``C:\\ProgramData``
   (e.g. ``Windows\\Temp``).
2. **Protected anchors** (drive roots, ``C:\\Users``, the user profile,
   ``AppData``, ...) and every *ancestor* of a protected tree can never be
   deleted themselves: removing ``C:\\Users\\me`` would remove Documents.
3. **Containment**: when a cleanup target supplies its root, the path must be
   inside that root both *literally* and *after resolving* symlinks and
   junctions. A link inside a cache folder that points at ``C:\\Users\\me\\
   Documents`` can therefore never cause Documents to be touched.

The protected list can be *extended* (tests, future config) but never reduced.
"""

from __future__ import annotations

import logging
import ntpath
import os
import posixpath
import stat
import sys
from collections.abc import Callable, Iterable, Mapping
from types import ModuleType

log = logging.getLogger(__name__)

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class ProtectedPathError(PermissionError):
    """Raised when a deletion would touch a protected path."""


class PathEscapeError(ProtectedPathError):
    """Raised when a path (after resolution) lies outside its allowed root."""


# --------------------------------------------------------------------------- #
# Link / junction detection
# --------------------------------------------------------------------------- #


def is_link_or_reparse(st: os.stat_result) -> bool:
    """True if an ``lstat`` result is a symlink, junction or other reparse point."""
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def path_is_link(path: str) -> bool:
    """True if ``path`` itself is a symlink/junction/reparse point (not followed)."""
    try:
        return is_link_or_reparse(os.lstat(path))
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Default protected lists
# --------------------------------------------------------------------------- #

_USER_FOLDERS = (
    "Documents",
    "Desktop",
    "Pictures",
    "Videos",
    "Music",
    "Favorites",
    "Contacts",
    "Links",
    "Saved Games",
    "Searches",
    "OneDrive",
    "3D Objects",
    "source",  # Visual Studio projects
    "repos",
)


def windows_protection(
    environ: Mapping[str, str],
    known: Mapping[str, str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Build ``(trees, anchors, allow)`` lists for Windows from environment variables.

    Hard-coded ``C:`` fallbacks are always included as well, so a stripped or
    malicious environment cannot remove protection.
    """
    j = ntpath.join
    sysdrive = (environ.get("SystemDrive") or "C:").rstrip("\\/") + "\\"
    drives = {sysdrive, "C:\\"}
    windirs = {
        environ.get("SystemRoot"),
        environ.get("windir"),
        j(sysdrive, "Windows"),
        r"C:\Windows",
    }
    windirs.discard(None)
    profiles = {environ.get("USERPROFILE")}
    profiles.discard(None)

    trees: list[str] = []
    anchors: list[str] = list(drives)
    allow: list[str] = []

    for win in windirs:
        assert win is not None
        trees += [
            win,
            j(win, "System32"),
            j(win, "SysWOW64"),
            j(win, "WinSxS"),
            j(win, "Boot"),
            j(win, "Fonts"),
            j(win, "servicing"),
            j(win, "assembly"),
            j(win, "Microsoft.NET"),
            j(win, "Installer"),
        ]
        # Well-known cache locations inside the Windows tree.
        allow += [
            j(win, "Temp"),
            j(win, "SoftwareDistribution", "Download"),
            j(win, "Minidump"),
            j(win, "MEMORY.DMP"),
            j(win, "LiveKernelReports"),
            j(win, "Logs"),
        ]

    for var in (
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramW6432",
        "CommonProgramFiles",
        "CommonProgramFiles(x86)",
        "CommonProgramW6432",
    ):
        if environ.get(var):
            trees.append(environ[var])
    for drive in drives:
        trees += [
            j(drive, "Program Files"),
            j(drive, "Program Files (x86)"),
            j(drive, "Boot"),
            j(drive, "EFI"),
            j(drive, "Recovery"),
            j(drive, "System Volume Information"),
            j(drive, "$Recycle.Bin"),  # emptied via the shell API only
            j(drive, "$WinREAgent"),
            j(drive, "bootmgr"),
            j(drive, "pagefile.sys"),
            j(drive, "swapfile.sys"),
            j(drive, "hiberfil.sys"),  # removed via ``powercfg`` only
        ]
        anchors.append(j(drive, "Users"))

    program_data = {environ.get("ProgramData"), j(sysdrive, "ProgramData")}
    program_data.discard(None)
    for pd in program_data:
        assert pd is not None
        trees.append(pd)
        allow.append(j(pd, "Microsoft", "Windows", "WER"))

    if environ.get("PUBLIC"):
        trees.append(environ["PUBLIC"])
    for drive in drives:
        trees.append(j(drive, "Users", "Public"))
        trees.append(j(drive, "Users", "Default"))

    for profile in profiles:
        assert profile is not None
        anchors += [
            profile,
            j(profile, "AppData"),
            j(profile, "AppData", "Local"),
            j(profile, "AppData", "LocalLow"),
            j(profile, "AppData", "Roaming"),
        ]
        trees += [j(profile, name) for name in _USER_FOLDERS]
        trees += [j(profile, "NTUSER.DAT"), j(profile, ".ssh"), j(profile, ".gnupg")]

    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if environ.get(var):
            trees.append(environ[var])
    for var in ("LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "HOMEDRIVE"):
        if environ.get(var):
            anchors.append(environ[var])

    trees += list((known or {}).values())
    # Allow-listed *folders* may be emptied but never removed themselves.
    anchors += [a for a in allow if not a.upper().endswith(".DMP")]
    return trees, anchors, allow


def posix_protection(environ: Mapping[str, str]) -> tuple[list[str], list[str], list[str]]:
    """Equivalent lists for POSIX systems (development / CI only)."""
    j = posixpath.join
    home = environ.get("HOME") or posixpath.expanduser("~")
    trees = [
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/lib",
        "/lib32",
        "/lib64",
        "/proc",
        "/sbin",
        "/sys",
        "/usr",
        "/var/lib",
        "/System",
        "/Library",
        "/Applications",
    ]
    trees += [
        j(home, name)
        for name in ("Documents", "Desktop", "Pictures", "Videos", "Music", ".ssh", ".gnupg")
    ]
    anchors = ["/", "/home", "/Users", "/var", "/root", home, j(home, ".cache"), j(home, ".local")]
    return trees, anchors, []


# --------------------------------------------------------------------------- #
# The guard
# --------------------------------------------------------------------------- #


class PathGuard:
    """Decides whether a path may be deleted. See module docstring for the rules.

    Args:
        trees: protected trees (nothing inside may be deleted).
        anchors: protected anchors (may not be deleted themselves; contents may).
        allow: exceptions *inside* protected trees (e.g. ``C:\\Windows\\Temp``).
        pathmod: ``ntpath``/``posixpath``; injectable so Windows rules can be
            unit-tested on Linux.
        resolve: function resolving symlinks/junctions (default ``pathmod.realpath``).
    """

    def __init__(
        self,
        trees: Iterable[str],
        anchors: Iterable[str] = (),
        allow: Iterable[str] = (),
        *,
        pathmod: ModuleType = os.path,
        resolve: Callable[[str], str] | None = None,
    ) -> None:
        self.pathmod = pathmod
        self._resolve = resolve or pathmod.realpath
        self.trees = frozenset(self._norm(p) for p in trees if p)
        self.anchors = frozenset(self._norm(p) for p in anchors if p)
        self.allow = frozenset(self._norm(p) for p in allow if p)
        # Every strict ancestor of a protected tree/anchor: deleting one of these
        # would delete protected content.
        self._protected_ancestors = frozenset(
            a for p in self.trees | self.anchors for a in self._ancestors(p)[1:]
        )
        self._real_cache: dict[str, str] = {}

    # -- helpers ----------------------------------------------------------- #

    def _norm(self, path: str) -> str:
        pm = self.pathmod
        return pm.normcase(pm.normpath(pm.abspath(os.fspath(path))))

    def _ancestors(self, p: str) -> list[str]:
        """``[p, parent, grandparent, ..., root]`` for a normalised path."""
        out = [p]
        while True:
            parent = self.pathmod.dirname(out[-1])
            if parent == out[-1]:
                return out
            out.append(parent)

    def _real(self, path: str) -> str:
        key = os.fspath(path)
        cached = self._real_cache.get(key)
        if cached is None:
            if len(self._real_cache) > 8192:
                self._real_cache.clear()
            cached = self._real_cache[key] = self._norm(self._resolve(key))
        return cached

    def is_within(self, child: str, parent: str) -> bool:
        """True if normalised ``child`` equals or is inside normalised ``parent``."""
        if child == parent:
            return True
        sep = self.pathmod.sep
        prefix = parent if parent.endswith(sep) else parent + sep
        return child.startswith(prefix)

    def _is_root(self, p: str) -> bool:
        return self.pathmod.dirname(p) == p

    def _violation(self, p: str) -> str | None:
        """Return a reason string if normalised path ``p`` is protected."""
        if self._is_root(p):
            return "drive or filesystem root"
        if p in self.anchors:
            return "protected location"
        if p in self.trees:
            return "protected path"
        if p in self._protected_ancestors:
            return "contains protected paths"
        ancestors = self._ancestors(p)
        for idx, anc in enumerate(ancestors):
            # Inside a protected tree is allowed only if an allow-listed path
            # sits between p and that tree (e.g. C:\Windows\Temp\x).
            if anc in self.trees and not any(a in self.allow for a in ancestors[:idx]):
                return f"inside protected path {anc}"
        return None

    # -- public API -------------------------------------------------------- #

    def check(self, path: str, within: str | None = None, *, link: bool = False) -> str:
        """Validate that ``path`` may be deleted, returning its normalised form.

        Args:
            path: the path to delete.
            within: if given, the path must be inside this root literally and
                after resolving links.
            link: True when the final component must not be resolved: when
                deleting a symlink/junction itself (we remove the link, never
                its target), or when ``lstat`` already showed it is not a link.
                The parent directory is always resolved.

        Raises:
            ProtectedPathError: if the path is protected.
            PathEscapeError: if the path escapes ``within``.
        """
        pm = self.pathmod
        raw = self._norm(path)
        parent_real = self._real(pm.dirname(raw))
        candidates = {raw, self._norm(pm.join(parent_real, pm.basename(raw)))}
        if not link:
            candidates.add(self._real(path))

        for cand in candidates:
            reason = self._violation(cand)
            if reason:
                log.warning("blocked protected path", extra={"path": path, "reason": reason})
                raise ProtectedPathError(f"refusing to delete {path!r}: {reason}")

        if within is not None:
            roots = {self._norm(within), self._real(within)}
            for cand in candidates:
                if not any(self.is_within(cand, r) for r in roots):
                    log.warning("blocked path escape", extra={"path": path, "within": within})
                    raise PathEscapeError(f"refusing to delete {path!r}: outside {within!r}")
        return raw

    def is_allowed(self, path: str, within: str | None = None, *, link: bool = False) -> bool:
        """Boolean form of :meth:`check`."""
        try:
            self.check(path, within, link=link)
        except ProtectedPathError:
            return False
        return True


def default_guard(
    environ: Mapping[str, str] | None = None,
    *,
    extra_protected: Iterable[str] = (),
    windows: bool | None = None,
) -> PathGuard:
    """Build the guard for the current machine.

    Args:
        environ: environment mapping (defaults to ``os.environ``).
        extra_protected: additional protected trees; can only *add* protection.
        windows: force Windows/POSIX rules (defaults to the running OS).
    """
    from disksweep import osutils

    environ = osutils.EnvMap(environ if environ is not None else os.environ)
    windows = sys.platform == "win32" if windows is None else windows
    if windows:
        known = osutils.known_folders() if sys.platform == "win32" else {}
        trees, anchors, allow = windows_protection(environ, known)
        pathmod: ModuleType = ntpath
    else:
        trees, anchors, allow = posix_protection(environ)
        pathmod = posixpath
    return PathGuard([*trees, *extra_protected], anchors, allow, pathmod=pathmod)
