"""Advanced, system-level space recovery. All require administrator rights."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from disksweep import osutils
from disksweep.models import Risk
from disksweep.targets.base import CleanupTarget, CommandTarget, TargetContext
from disksweep.units import human_size


class DismComponentCleanup(CommandTarget):
    id = "dism_cleanup"
    name = "Component store cleanup (DISM)"
    description = (
        "Runs `DISM /Online /Cleanup-Image /StartComponentCleanup` to remove superseded "
        "Windows component versions from WinSxS. Can take 10+ minutes."
    )
    risk = Risk.ADVANCED
    requires_admin = True
    executable = "dism"
    command = ("dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup")


def hiberfil_path(environ: Mapping[str, str]) -> str:
    """Location of ``hiberfil.sys`` on the system drive."""
    return os.path.join((environ.get("SystemDrive") or "C:") + os.sep, "hiberfil.sys")


class HibernationOff(CommandTarget):
    id = "hibernation_off"
    name = "Disable hibernation (delete hiberfil.sys)"
    description = (
        "Runs `powercfg /hibernate off`. Frees space equal to hiberfil.sys but disables "
        "hibernation and Fast Startup. Re-enable with `powercfg /hibernate on`."
    )
    risk = Risk.ADVANCED
    requires_admin = True
    executable = "powercfg"
    command = ("powercfg.exe", "/hibernate", "off")

    def current_size(self, ctx: TargetContext) -> int | None:
        try:
            return os.stat(hiberfil_path(ctx.environ)).st_size
        except OSError:
            return 0

    def measure(self, ctx: TargetContext) -> tuple[int, bool, str]:
        size = self.current_size(ctx) or 0
        return size, False, "" if size else "hibernation already off"


_SHADOW_USED = re.compile(r"Used Shadow Copy Storage space:\s*([\d.,]+)\s*(B|KB|MB|GB|TB)", re.I)
_BINARY = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}


def parse_shadow_used(output: str) -> int:
    """Sum 'Used Shadow Copy Storage space' values from ``vssadmin list shadowstorage``."""
    total = 0
    for num, unit in _SHADOW_USED.findall(output):
        total += int(float(num.replace(",", ".")) * _BINARY[unit.upper()])
    return total


class ShadowStorageResize(CommandTarget):
    id = "shadow_storage"
    name = "Limit System Restore / shadow copy storage"
    description = (
        "Runs `vssadmin resize shadowstorage /maxsize=5%` on the system drive. Windows "
        "deletes the oldest restore points to fit."
    )
    risk = Risk.ADVANCED
    requires_admin = True
    executable = "vssadmin"
    max_size = "5%"

    def build_command(self, ctx: TargetContext) -> list[str] | None:
        if not osutils.which("vssadmin"):
            return None
        drive = (ctx.environ.get("SystemDrive") or "C:").rstrip("\\")
        return [
            "vssadmin",
            "resize",
            "shadowstorage",
            f"/for={drive}",
            f"/on={drive}",
            f"/maxsize={self.max_size}",
        ]

    def current_size(self, ctx: TargetContext) -> int | None:
        res = osutils.run_cmd(["vssadmin", "list", "shadowstorage"], timeout=120)
        return parse_shadow_used(res.stdout) if res.ok else None

    def measure(self, ctx: TargetContext) -> tuple[int, bool, str]:
        if not ctx.is_admin:
            return 0, True, "run as administrator to measure"
        used = self.current_size(ctx)
        if used is None:
            return 0, True, "could not query shadow storage"
        return 0, True, f"currently using {human_size(used)}; gain depends on drive size"


TARGETS: tuple[type[CleanupTarget], ...] = (
    DismComponentCleanup,
    ShadowStorageResize,
    HibernationOff,
)
