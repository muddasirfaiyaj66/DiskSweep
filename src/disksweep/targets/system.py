"""Windows system cleanup targets."""

from __future__ import annotations

from disksweep import osutils
from disksweep.models import CleanResult, Risk, ScanResult
from disksweep.targets.base import CleanupTarget, DirectoryTarget, TargetContext


class UserTemp(DirectoryTarget):
    id = "user_temp"
    name = "User temporary files"
    description = "Files in your %TEMP% folder older than 24 hours."
    risk = Risk.SAFE
    specs = (("TEMP",), ("TMP",), ("LOCALAPPDATA", "Temp"))
    min_age_hours = 24


class WindowsTemp(DirectoryTarget):
    id = "windows_temp"
    name = "Windows temporary files"
    description = r"Files in C:\Windows\Temp older than 24 hours."
    risk = Risk.SAFE
    requires_admin = True
    specs = (("SystemRoot", "Temp"),)
    min_age_hours = 24


class WindowsUpdateCache(DirectoryTarget):
    id = "windows_update"
    name = "Windows Update cache"
    description = (
        "Downloaded update packages in SoftwareDistribution\\Download. The Windows "
        "Update service is stopped while cleaning and restarted afterwards."
    )
    risk = Risk.MODERATE
    requires_admin = True
    specs = (("SystemRoot", "SoftwareDistribution", "Download"),)
    service = "wuauserv"


class WindowsOld(DirectoryTarget):
    id = "windows_old"
    name = "Previous Windows installation (Windows.old)"
    description = (
        "Leftovers of a previous Windows version. Removing it makes rolling back "
        "to that version impossible."
    )
    risk = Risk.ADVANCED
    requires_admin = True
    specs = (("SystemDrive", "Windows.old"),)
    delete_root = True


class CrashDumpsUser(DirectoryTarget):
    id = "crash_dumps"
    name = "Crash dumps and error reports (user)"
    description = "Application crash dumps and Windows Error Reporting files for your account."
    risk = Risk.SAFE
    category = "system"
    specs = (
        ("LOCALAPPDATA", "CrashDumps"),
        ("LOCALAPPDATA", "Microsoft", "Windows", "WER", "ReportArchive"),
        ("LOCALAPPDATA", "Microsoft", "Windows", "WER", "ReportQueue"),
    )


class CrashDumpsSystem(DirectoryTarget):
    id = "crash_dumps_system"
    name = "Crash dumps and error reports (system)"
    description = "System memory dumps (MEMORY.DMP, Minidump) and system-wide error reports."
    risk = Risk.SAFE
    requires_admin = True
    specs = (
        ("SystemRoot", "Minidump"),
        ("SystemRoot", "MEMORY.DMP"),
        ("SystemRoot", "LiveKernelReports"),
        ("ProgramData", "Microsoft", "Windows", "WER", "ReportArchive"),
        ("ProgramData", "Microsoft", "Windows", "WER", "ReportQueue"),
    )


class WindowsLogs(DirectoryTarget):
    id = "windows_logs"
    name = "Old Windows log files"
    description = r"*.log/*.etl/*.cab files in C:\Windows\Logs older than 7 days."
    risk = Risk.SAFE
    requires_admin = True
    specs = (("SystemRoot", "Logs"),)
    patterns = ("*.log", "*.etl", "*.cab", "*.old")
    min_age_hours = 24 * 7


class ThumbnailCache(DirectoryTarget):
    id = "thumbnail_cache"
    name = "Thumbnail and icon cache"
    description = "Explorer thumbnail databases. Rebuilt automatically; files in use are skipped."
    risk = Risk.SAFE
    specs = (("LOCALAPPDATA", "Microsoft", "Windows", "Explorer"),)
    patterns = ("thumbcache_*.db", "iconcache_*.db")


class RecycleBin(CleanupTarget):
    id = "recycle_bin"
    name = "Recycle Bin"
    description = "Permanently delete everything in the Recycle Bin on all drives."
    risk = Risk.MODERATE
    category = "system"

    def scan(self, ctx: TargetContext) -> ScanResult:
        if not osutils.IS_WINDOWS:
            return ScanResult(self.id, available=False, note="Windows only")
        size, items = osutils.recycle_bin_size()
        return ScanResult(self.id, bytes=size, files=items)

    def clean(self, ctx: TargetContext, dry_run: bool = True) -> CleanResult:
        size, items = osutils.recycle_bin_size()
        if dry_run or not osutils.IS_WINDOWS:
            return CleanResult(
                self.id,
                dry_run=dry_run,
                bytes_freed=size,
                files_deleted=items,
                ran=osutils.IS_WINDOWS,
            )
        ok = osutils.empty_recycle_bin()
        if ctx.audit:
            ctx.audit.record_event("empty_recycle_bin", bytes=size, items=items, ok=ok)
        result = CleanResult(
            self.id, dry_run=False, bytes_freed=size if ok else 0, files_deleted=items if ok else 0
        )
        if not ok:
            result.errors.append("SHEmptyRecycleBin failed")
        return result


TARGETS: tuple[type[CleanupTarget], ...] = (
    UserTemp,
    WindowsTemp,
    WindowsUpdateCache,
    CrashDumpsUser,
    CrashDumpsSystem,
    WindowsLogs,
    ThumbnailCache,
    RecycleBin,
    WindowsOld,
)
