"""Capture GUI screenshots of the full DiskSweep workflow (Windows only).

    python tools/capture_screenshots.py [--out docs/screenshots]

The GUI runs against a throwaway **demo environment**: a temp folder laid out
like a Windows machine (temp files, browser caches, dev caches, Windows.old...),
filled with *sparse* files so sizes look realistic without using disk space.
Every environment variable the cleanup targets read points into that folder,
external commands are disabled and the Recycle Bin API is stubbed, so the
cleanup shown in the screenshots really runs, but only deletes demo files.

Windows are captured with ``PrintWindow``, so only DiskSweep's own windows are
recorded, never the rest of the desktop.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import random
import shutil
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

if sys.platform != "win32":
    sys.exit("This script captures Windows GUI screenshots and only runs on Windows.")

from PIL import Image

GIB = 1024**3
MIB = 1024**2

# --------------------------------------------------------------------------- #
# Win32 capture helpers
# --------------------------------------------------------------------------- #

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HANDLE = ctypes.c_void_p
user32.GetWindowDC.restype = HANDLE
user32.GetWindowDC.argtypes = [HANDLE]
user32.ReleaseDC.argtypes = [HANDLE, HANDLE]
user32.PrintWindow.argtypes = [HANDLE, HANDLE, wintypes.UINT]
user32.GetWindowRect.argtypes = [HANDLE, ctypes.POINTER(wintypes.RECT)]
user32.FindWindowW.restype = HANDLE
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.PostMessageW.argtypes = [HANDLE, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
gdi32.CreateCompatibleDC.restype = HANDLE
gdi32.CreateCompatibleDC.argtypes = [HANDLE]
gdi32.CreateCompatibleBitmap.restype = HANDLE
gdi32.CreateCompatibleBitmap.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = HANDLE
gdi32.SelectObject.argtypes = [HANDLE, HANDLE]
gdi32.DeleteObject.argtypes = [HANDLE]
gdi32.DeleteDC.argtypes = [HANDLE]
gdi32.GetDIBits.argtypes = [HANDLE, HANDLE, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, wintypes.UINT]  # fmt: skip
dwmapi.DwmGetWindowAttribute.argtypes = [HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

WM_COMMAND = 0x0111
IDOK, IDYES = 1, 6


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def capture_hwnd(hwnd: int) -> tuple[Image.Image, wintypes.RECT]:
    """Render one window (even if covered) and crop away the invisible DWM border."""
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    win_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(win_dc)
    bmp = gdi32.CreateCompatibleBitmap(win_dc, w, h)
    old = gdi32.SelectObject(mem_dc, bmp)
    try:
        user32.PrintWindow(hwnd, mem_dc, 2)  # PW_RENDERFULLCONTENT
        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).copy()
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, win_dc)
    ext = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(ext), ctypes.sizeof(ext)) == 0:
        img = img.crop((ext.left - rect.left, ext.top - rect.top,
                        ext.right - rect.left, ext.bottom - rect.top))  # fmt: skip
    else:
        ext = rect
    return img, ext


def composite(main: tuple[Image.Image, wintypes.RECT],
              dialog: tuple[Image.Image, wintypes.RECT]) -> Image.Image:  # fmt: skip
    """Main window, dimmed, with the dialog pasted at its real on-screen position."""
    (mimg, mr), (dimg, dr) = main, dialog
    left, top = min(mr.left, dr.left), min(mr.top, dr.top)
    right, bottom = max(mr.right, dr.right), max(mr.bottom, dr.bottom)
    canvas = Image.new("RGB", (right - left, bottom - top), (30, 30, 30))
    dim = Image.blend(mimg, Image.new("RGB", mimg.size, (0, 0, 0)), 0.35)
    canvas.paste(dim, (mr.left - left, mr.top - top))
    canvas.paste(dimg, (dr.left - left, dr.top - top))
    return canvas


user32.IsWindow.argtypes = [HANDLE]


def press(hwnd: int, button_id: int, timeout: float = 30) -> None:
    """Click a dialog button, retrying until the dialog has closed."""
    deadline = time.monotonic() + timeout
    while user32.IsWindow(hwnd):
        if time.monotonic() > deadline:
            raise TimeoutError("dialog did not close")
        user32.PostMessageW(hwnd, WM_COMMAND, button_id, 0)
        time.sleep(0.3)


def wait_for_window(title: str, timeout: float = 120) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = user32.FindWindowW("#32770", title)
        if hwnd:
            return hwnd
        time.sleep(0.1)
    raise TimeoutError(f"dialog {title!r} did not appear")


# --------------------------------------------------------------------------- #
# Demo environment
# --------------------------------------------------------------------------- #


class DemoDiskError(RuntimeError):
    """A demo file would consume real disk space."""


kernel32.DeviceIoControl.argtypes = [HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]  # fmt: skip
kernel32.SetFilePointerEx.argtypes = [HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD]
kernel32.SetEndOfFile.argtypes = [HANDLE]
kernel32.GetCompressedFileSizeW.restype = wintypes.DWORD
kernel32.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]

#: Maximum real allocation tolerated per demo file (NTFS may allocate one cluster run).
MAX_ALLOCATED = 1 * MIB


def allocated_size(path: Path) -> int:
    """Bytes actually allocated on disk (for sparse files, much less than the length)."""
    high = wintypes.DWORD()
    low = kernel32.GetCompressedFileSizeW(str(path), ctypes.byref(high))
    return (high.value << 32) | low


def _sparse_file(path: Path, size: int, old_days: float) -> None:
    """Create a file whose logical size is ``size`` but which uses ~no disk space.

    The file is marked sparse and extended with ``SetEndOfFile``, which, unlike
    the C runtime's ``truncate``, never writes zeros. The real allocation is
    verified afterwards; anything unexpected aborts the whole run.
    """
    import msvcrt

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        handle = HANDLE(msvcrt.get_osfhandle(fh.fileno()))
        returned = wintypes.DWORD()
        ok = kernel32.DeviceIoControl(
            handle, 0x000900C4, None, 0, None, 0, ctypes.byref(returned), None
        )  # FSCTL_SET_SPARSE
        if not ok:
            raise DemoDiskError(f"cannot mark {path} sparse; refusing to allocate {size} bytes")
        if not (kernel32.SetFilePointerEx(handle, size, None, 0)  # FILE_BEGIN
                and kernel32.SetEndOfFile(handle)):  # fmt: skip
            raise DemoDiskError(f"SetEndOfFile failed for {path}")
    if allocated_size(path) > MAX_ALLOCATED:
        raise DemoDiskError(f"{path} allocated real disk space; aborting")
    t = time.time() - old_days * 86400
    os.utime(path, (t, t))
    kernel32.CreateFileW.restype = HANDLE
    h = kernel32.CreateFileW(str(path), 0x100, 7, None, 3, 0x02000000, None)
    if h and h != HANDLE(-1).value:
        ticks = int((t + 11644473600) * 10_000_000)
        ft = wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)
        kernel32.SetFileTime(HANDLE(h), ctypes.byref(ft), None, None)
        kernel32.CloseHandle(HANDLE(h))


def _fill(folder: Path, total: int, count: int, pattern: str, rng: random.Random,
          old_days: float = 30) -> None:  # fmt: skip
    weights = [rng.random() + 0.1 for _ in range(count)]
    scale = total / sum(weights)
    for i, wgt in enumerate(weights):
        _sparse_file(folder / pattern.format(i=i, h=rng.getrandbits(48)), int(wgt * scale),
                     old_days)  # fmt: skip


def build_demo(root: Path) -> dict[str, str]:
    """Lay out a fake Windows machine under ``root`` and return its environment."""
    rng = random.Random(7)
    prof = root / "Users" / "demo"
    local = prof / "AppData" / "Local"
    win = root / "Windows"
    env = {
        "SystemDrive": str(root),
        "SystemRoot": str(win),
        "USERPROFILE": str(prof),
        "LOCALAPPDATA": str(local),
        "APPDATA": str(prof / "AppData" / "Roaming"),
        "TEMP": str(local / "Temp"),
        "ProgramData": str(root / "ProgramData"),
    }
    chrome = local / "Google" / "Chrome" / "User Data"
    edge = local / "Microsoft" / "Edge" / "User Data"
    layout = [
        (local / "Temp", int(1.3 * GIB), 40, "tmp{h:x}.tmp"),
        (local / "Temp" / "chocolatey", 420 * MIB, 6, "pkg{i}.nupkg"),
        (local / "CrashDumps", int(2.1 * GIB), 3, "app.exe.{i}.dmp"),
        (local / "Microsoft" / "Windows" / "Explorer", 180 * MIB, 6, "thumbcache_{i}.db"),
        (chrome / "Default" / "Cache" / "Cache_Data", int(2.4 * GIB), 60, "f_{h:06x}"),
        (chrome / "Profile 1" / "Code Cache" / "js", 610 * MIB, 25, "{h:016x}_0"),
        (edge / "Default" / "Cache" / "Cache_Data", 640 * MIB, 30, "f_{h:06x}"),
        (
            local
            / "Mozilla"
            / "Firefox"
            / "Profiles"
            / "x1.default-release"
            / "cache2"
            / "entries",
            int(1.1 * GIB),
            40,
            "{h:040X}",
        ),
        (
            local / "BraveSoftware" / "Brave-Browser" / "User Data" / "Default" / "Cache",
            380 * MIB,
            20,
            "f_{h:06x}",
        ),
        (
            local / "npm-cache" / "_cacache" / "content-v2" / "sha512",
            int(3.2 * GIB),
            80,
            "{h:012x}",
        ),
        (local / "Yarn" / "Cache", 900 * MIB, 30, "npm-pkg-{i}.zip"),
        (local / "pip" / "Cache" / "http-v2", int(1.8 * GIB), 35, "{h:x}.body"),
        (local / "NuGet" / "v3-cache", 410 * MIB, 20, "{h:x}.dat"),
        (prof / ".nuget" / "packages", int(2.3 * GIB), 50, "pkg{i}.nupkg"),
        (
            prof / ".gradle" / "caches" / "modules-2" / "files-2.1",
            int(6.5 * GIB),
            120,
            "lib-{i}.jar",
        ),
        (prof / ".m2" / "repository", int(1.2 * GIB), 60, "artifact-{i}.jar"),
        (prof / ".cargo" / "registry" / "cache", 720 * MIB, 40, "crate-{i}.crate"),
        (win / "Temp", 310 * MIB, 12, "cab_{i}.tmp"),
        (win / "SoftwareDistribution" / "Download", int(1.6 * GIB), 9, "{h:x}.cab"),
        (win / "Logs" / "CBS", 230 * MIB, 8, "CbsPersist_{i}.log"),
        (win / "Minidump", 96 * MIB, 4, "0{i}1525-01.dmp"),
        (root / "Windows.old" / "Windows" / "System32", int(14.0 * GIB), 40, "old{i}.dll"),
        (root / "Windows.old" / "Program Files", int(4.2 * GIB), 20, "app{i}.exe"),
    ]
    for folder, total, count, pattern in layout:
        _fill(folder, total, count, pattern, rng)
    _fill(local / "Temp", 64 * MIB, 5, "fresh{i}.tmp", rng, old_days=0)  # kept: < 24 h
    _sparse_file(root / "hiberfil.sys", int(6.4 * GIB), 1)
    # Personal files that must never be touched (they are protected):
    for name in ("Documents", "Desktop", "Pictures"):
        _fill(prof / name, 2 * GIB, 6, "personal-{i}.dat", rng)
    return env


# --------------------------------------------------------------------------- #
# Scripted walkthrough
# --------------------------------------------------------------------------- #


def run(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    demo_root = Path(tempfile.mkdtemp(prefix="disksweep-demo-")) / "DemoPC"
    print(f"Building demo environment in {demo_root} (sparse files) ...")
    free_before = shutil.disk_usage(demo_root.parent).free
    try:
        env = build_demo(demo_root)
    except BaseException:
        shutil.rmtree(demo_root.parent, ignore_errors=True)
        raise
    used = free_before - shutil.disk_usage(demo_root.parent).free
    print(f"Demo environment uses {used / MIB:.1f} MB of real disk space.")
    os.environ["DISKSWEEP_HOME"] = str(demo_root.parent / "disksweep-home")

    from disksweep import osutils
    from disksweep.safety import default_guard

    demo_env = osutils.EnvMap(env)
    osutils.env = lambda: demo_env  # every target path resolves into the demo folder
    osutils.which = lambda name: None  # no external tools (docker, dism, ...)
    osutils.is_admin = lambda: False
    osutils.recycle_bin_size = lambda: (int(1.2 * GIB), 214)

    def refuse(*a: object, **k: object) -> object:
        raise RuntimeError("blocked in screenshot demo")

    osutils.run_cmd = refuse  # type: ignore[assignment]
    osutils.empty_recycle_bin = refuse  # type: ignore[assignment]
    osutils.stop_service = refuse  # type: ignore[assignment]

    # The demo personal folders get the same protection as real ones.
    import disksweep.engine as engine

    real_guard = engine.default_guard
    extra = [str(Path(env["USERPROFILE"]) / n) for n in ("Documents", "Desktop", "Pictures")]
    engine.default_guard = lambda e=None, **k: real_guard(e, extra_protected=extra)
    assert default_guard is not None

    import customtkinter as ctk

    from disksweep.gui import app as gui_app
    from disksweep.gui import dashboard as dmod

    # Slow the real scan/clean slightly so the progress states can be captured.
    orig_scan, orig_clean = dmod.scan_targets, dmod.clean_targets

    def slow_scan(targets, ctx, progress=None, max_workers=4):  # type: ignore[no-untyped-def]
        def p(tid: str, d: int, t: int) -> None:
            time.sleep(0.12)
            if progress:
                progress(tid, d, t)

        return orig_scan(targets, ctx, progress=p, max_workers=1)

    def slow_clean(targets, ctx, *, apply=False, progress=None):  # type: ignore[no-untyped-def]
        assert all(t.id != "recycle_bin" for t in targets)

        def p(tid: str, d: int, t: int) -> None:
            time.sleep(0.45)
            if progress:
                progress(tid, d, t)

        return orig_clean(targets, ctx, apply=apply, progress=p)

    dmod.scan_targets, dmod.clean_targets = slow_scan, slow_clean

    ctk.set_default_color_theme("blue")
    win = gui_app.DiskSweepApp()
    win.theme_var.set("Dark")
    win.set_theme("Dark")
    saved: list[Path] = []

    def find(widget, cls):  # type: ignore[no-untyped-def]
        for child in widget.winfo_children():
            if isinstance(child, cls):
                return child
            hit = find(child, cls)
            if hit is not None:
                return hit
        return None

    dash = win.dashboard
    analyzer = win.analyzer
    scans = {"n": 0}
    orig_done = dash._scan_done

    def counted_done(results):  # type: ignore[no-untyped-def]
        scans["n"] += 1
        orig_done(results)

    dash._scan_done = counted_done

    def hwnd() -> int:
        return int(win.wm_frame(), 16)

    def save(img: Image.Image, name: str) -> None:
        path = out / f"{name}.png"
        img.save(path, optimize=True)
        saved.append(path)
        print(f"  saved {path}")

    def shot(name: str) -> None:
        win.update_idletasks()
        win.update()
        save(capture_hwnd(hwnd())[0], name)

    def dialog_open(title: str) -> bool:
        d = dash.active_dialog
        return d is not None and d.winfo_exists() and d.winfo_viewable() and d.title() == title

    def shot_dialog(name: str) -> None:
        """Main window (dimmed) with the in-app dialog at its real position."""
        win.update_idletasks()
        win.update()
        d = dash.active_dialog
        assert d is not None
        save(composite(capture_hwnd(hwnd()), capture_hwnd(int(d.wm_frame(), 16))), name)

    def close_dialog(choice: str) -> None:
        d = dash.active_dialog
        assert d is not None
        win.after(0, lambda: d.close(choice))

    def scroll(fraction: float) -> None:
        dash.list_frame._parent_canvas.yview_moveto(fraction)

    def hover(chart, index):  # type: ignore[no-untyped-def]
        """Simulate the pointer resting on a bar (index None = pointer left)."""
        if index is None:
            chart._set_hover(None)
            return
        top, bottom = chart._rows[index]
        chart._mouse = (int(chart.winfo_width() * 0.45), (top + bottom) // 2)
        chart._set_hover(index)

    steps: list[tuple[str, object]] = [
        ("sleep", 800),
        ("shot", "01-dashboard-start"),
        ("do", dash.start_scan),
        ("wait", lambda: dash.progress.get() >= 0.4),
        ("shot", "02-scanning"),
        ("wait", lambda: scans["n"] >= 1),
        ("sleep", 300),
        ("shot", "03-scan-results-safe"),
        ("do", lambda: (dash.profile_var.set("Developer"), dash.apply_profile("developer"))),
        ("do", lambda: scroll(0.42)),
        ("sleep", 300),
        ("shot", "04-developer-profile"),
        ("do", lambda: scroll(0.0)),
        ("do", lambda: win.after(0, dash.confirm_clean)),  # opens a modal; don't block
        ("wait", lambda: dialog_open("Confirm cleanup")),
        ("sleep", 600),
        ("call", lambda: shot_dialog("05-confirm-cleanup")),
        ("do", lambda: close_dialog("Clean now")),
        (
            "wait",
            lambda: dash.status.cget("text").startswith("Cleaned") and dash.progress.get() >= 0.45,
        ),
        ("shot", "06-cleaning"),
        ("wait", lambda: dialog_open("Cleanup complete")),
        ("sleep", 600),
        ("call", lambda: shot_dialog("07-cleanup-complete")),
        ("do", lambda: close_dialog("Done")),
        ("wait", lambda: scans["n"] >= 2),
        ("sleep", 300),
        ("shot", "08-after-cleanup"),
        ("do", lambda: win.show_page("Analyzer")),
        ("do", lambda: (analyzer.path_var.set(str(demo_root)), analyzer.start())),
        ("wait", lambda: analyzer.result is not None),
        ("sleep", 300),
        ("shot", "09-analyzer-charts"),
        ("do", lambda: hover(analyzer.folder_chart, 1)),
        ("sleep", 300),
        ("shot", "10-analyzer-hover"),
        ("do", lambda: hover(analyzer.folder_chart, None)),
        ("do", lambda: (analyzer.view_var.set("Tables"), analyzer.show_view("Tables"))),
        ("sleep", 300),
        ("shot", "11-analyzer-tables"),
        ("do", lambda: (analyzer.view_var.set("Charts"), analyzer.show_view("Charts"))),
        ("do", lambda: setattr(analyzer, "result", None)),
        ("do", lambda: analyzer.drill_down(analyzer.folder_chart.bars[0].key)),
        ("wait", lambda: analyzer.result is not None),
        ("sleep", 300),
        ("shot", "12-analyzer-drilldown"),
        ("do", lambda: win.show_page("Audit log")),
        ("sleep", 300),
        ("shot", "13-audit-log"),
        ("do", lambda: (win.theme_var.set("Light"), win.set_theme("Light"))),
        ("do", lambda: win.show_page("Dashboard")),
        ("do", lambda: (dash.profile_var.set("Safe"), dash.apply_profile("safe"))),
        ("do", lambda: scroll(0.0)),
        ("sleep", 600),
        ("shot", "14-dashboard-light"),
        ("do", lambda: win.show_page("Analyzer")),
        ("do", lambda: setattr(analyzer, "result", None)),
        ("do", lambda: analyzer.go_up_to(str(demo_root))),
        ("wait", lambda: analyzer.result is not None),
        ("sleep", 500),
        ("shot", "15-analyzer-light"),
        ("do", win.destroy),
    ]
    state = {"i": 0, "deadline": 0.0}

    def tick() -> None:
        while state["i"] < len(steps):
            kind, arg = steps[state["i"]]
            if kind == "wait":
                if not arg():  # type: ignore[operator]
                    if time.monotonic() > state["deadline"]:
                        raise TimeoutError(f"step {state['i']} timed out")
                    win.after(50, tick)
                    return
            elif kind == "sleep":
                state["i"] += 1
                state["deadline"] = time.monotonic() + 300
                win.after(int(arg), tick)  # type: ignore[call-overload]
                return
            elif kind == "shot":
                shot(str(arg))
            elif kind == "call":
                arg()  # type: ignore[operator]
            elif kind == "do":
                state["i"] += 1
                state["deadline"] = time.monotonic() + 300
                arg()  # type: ignore[operator]
                if state["i"] < len(steps):
                    win.after(50, tick)
                return
            state["i"] += 1
            state["deadline"] = time.monotonic() + 300

    errors: list[object] = []
    win.report_callback_exception = lambda *exc: (errors.append(exc), win.destroy())
    state["deadline"] = time.monotonic() + 300
    win.after(100, tick)
    win.mainloop()

    shutil.rmtree(demo_root.parent, ignore_errors=True)
    if errors:
        import traceback

        traceback.print_exception(*errors[0][:3])  # type: ignore[index]
        raise SystemExit(1)
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).resolve().parents[1] / "docs" / "screenshots"
    )
    files = run(parser.parse_args().out)
    print(f"{len(files)} screenshots written.")


if __name__ == "__main__":
    main()
