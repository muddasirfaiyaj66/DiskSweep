"""Thin wrappers around operating-system calls.

Every Windows-only call (ctypes, ``schtasks``, ``net``, ``dism``, process
inspection, ...) goes through this module so that the rest of the code base can
be tested on any OS by monkeypatching these functions.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import psutil

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

#: Default timeout for external commands (seconds). DISM can take a long time.
DEFAULT_TIMEOUT = 60 * 60


@dataclass(frozen=True)
class CommandResult:
    """Result of an external command."""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True if the command exited with status 0."""
        return self.returncode == 0


class EnvMap(dict[str, str]):
    """Case-insensitive environment mapping (Windows variable names ignore case).

    ``dict(os.environ)`` upper-cases keys on Windows, so ``env["SystemRoot"]``
    would fail on a plain copy. Keys are stored upper-cased and looked up the same way.
    """

    def __init__(self, data: Mapping[str, str] | None = None) -> None:
        super().__init__()
        for key, value in (data or {}).items():
            self[key] = value

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(key.upper(), value)

    def __getitem__(self, key: str) -> str:
        return super().__getitem__(key.upper())

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and super().__contains__(key.upper())

    def get(self, key: str, default: str | None = None) -> str | None:  # type: ignore[override]
        return super().get(key.upper(), default)


def env() -> Mapping[str, str]:
    """Return the process environment (indirection point for tests)."""
    return os.environ


def is_admin() -> bool:
    """Return True if the current process has administrator rights."""
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except OSError:  # pragma: no cover - defensive
            return False
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def relaunch_elevated(argv: Sequence[str]) -> bool:
    """Re-launch the current program with a UAC elevation prompt.

    Returns True if the elevated process was started; the caller should then exit.
    """
    if not IS_WINDOWS:
        raise OSError("elevation is only supported on Windows")
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, list(argv)
    else:
        exe, params = sys.executable, ["-m", "disksweep", *argv]
    rc = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
        None, "runas", exe, subprocess.list2cmdline(params), None, 1
    )
    return int(rc) > 32


def which(name: str) -> str | None:
    """Locate an executable on PATH."""
    return shutil.which(name)


def run_cmd(
    args: Sequence[str],
    timeout: float = DEFAULT_TIMEOUT,
    input_text: str | None = None,
) -> CommandResult:
    """Run an external command without a shell and capture its output.

    Never raises for a non-zero exit status; missing executables and timeouts are
    reported as a failed :class:`CommandResult`.
    """
    kwargs: dict[str, object] = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    log.info("run command", extra={"cmd": list(args)})
    try:
        proc = subprocess.run(
            list(args),
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **kwargs,  # type: ignore[arg-type]
        )
    except FileNotFoundError as exc:
        return CommandResult(tuple(args), 127, "", str(exc))
    except subprocess.TimeoutExpired:
        return CommandResult(tuple(args), 124, "", f"timed out after {timeout}s")
    return CommandResult(tuple(args), proc.returncode, proc.stdout or "", proc.stderr or "")


def running_processes(names: Iterable[str]) -> set[str]:
    """Return the subset of ``names`` (e.g. ``"chrome.exe"``) that are running."""
    wanted = {n.lower() for n in names}
    found: set[str] = set()
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name in wanted:
            found.add(name)
    return found


def service_running(name: str) -> bool:
    """Return True if the Windows service ``name`` is running."""
    if not IS_WINDOWS:
        return False
    try:
        return psutil.win_service_get(name).status() == "running"  # type: ignore[attr-defined]
    except Exception:
        return False


def stop_service(name: str) -> bool:
    """Stop a Windows service and wait for it. Returns True on success."""
    return run_cmd(["net", "stop", name, "/y"], timeout=300).ok


def start_service(name: str) -> bool:
    """Start a Windows service. Returns True on success."""
    return run_cmd(["net", "start", name], timeout=300).ok


def disk_free(path: str) -> int:
    """Free bytes on the volume containing ``path``."""
    return psutil.disk_usage(path).free


def disk_usage(path: str) -> tuple[int, int, int]:
    """``(total, used, free)`` bytes for the volume containing ``path``."""
    usage = psutil.disk_usage(path)
    return usage.total, usage.used, usage.free


def list_drives() -> list[str]:
    """Mount points of local fixed drives."""
    drives: list[str] = []
    for part in psutil.disk_partitions(all=False):
        if IS_WINDOWS and ("cdrom" in part.opts or part.fstype == ""):
            continue
        drives.append(part.mountpoint)
    return drives


def system_drive(environ: Mapping[str, str] | None = None) -> str:
    """Root of the system drive, e.g. ``C:\\`` (``/`` on POSIX)."""
    environ = environ if environ is not None else env()
    if IS_WINDOWS:
        return environ.get("SystemDrive", "C:") + "\\"
    return "/"


# --------------------------------------------------------------------------- #
# Recycle Bin (shell32)
# --------------------------------------------------------------------------- #


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


def recycle_bin_size() -> tuple[int, int]:
    """``(bytes, items)`` currently in the Recycle Bin on all drives."""
    if not IS_WINDOWS:
        return 0, 0
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    hr = shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    if hr != 0:
        return 0, 0
    return int(info.i64Size), int(info.i64NumItems)


def empty_recycle_bin() -> bool:
    """Empty the Recycle Bin on all drives without UI. Returns True on success."""
    if not IS_WINDOWS:
        return False
    flags = 0x1 | 0x2 | 0x4  # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    hr = shell32.SHEmptyRecycleBinW(None, None, flags)
    # S_OK, or E_UNEXPECTED (0x8000FFFF) when the bin is already empty.
    return hr in (0, -2147418113)


# --------------------------------------------------------------------------- #
# Known folders (handles Documents/Desktop redirected to OneDrive or D:)
# --------------------------------------------------------------------------- #

_KNOWN_FOLDERS = {
    "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
    "Music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "Favorites": "1777F761-68AD-4D8A-87BD-30B759FA33DD",
    "SavedGames": "4C5C32FF-BB9D-43B0-B5B4-2D72E54EAAA4",
}


def known_folders() -> dict[str, str]:
    """Actual locations of the user's personal folders (Windows only)."""
    if not IS_WINDOWS:
        return {}
    result: dict[str, str] = {}
    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    for name, guid in _KNOWN_FOLDERS.items():
        buf = ctypes.create_string_buffer(uuid.UUID(guid).bytes_le, 16)
        out = ctypes.c_void_p()
        try:
            hr = shell32.SHGetKnownFolderPath(buf, 0, None, ctypes.byref(out))
            if hr == 0 and out.value:
                path = ctypes.wstring_at(out.value)
                if path:
                    result[name] = path
        except OSError:  # pragma: no cover - defensive
            continue
        finally:
            if out.value:
                ole32.CoTaskMemFree(out)
    return result
