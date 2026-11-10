"""Scheduled cleanup via Windows Task Scheduler (``schtasks.exe``)."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence

from disksweep import osutils
from disksweep.profiles import PROFILES

TASK_NAME = r"DiskSweep\Cleanup"
FREQUENCIES = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY"}
DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def disksweep_command(profile: str, executable: str | None = None) -> list[str]:
    """Command line the scheduled task runs."""
    if getattr(sys, "frozen", False) or executable:
        base = [executable or sys.executable]
    else:
        base = [sys.executable, "-m", "disksweep"]
    return [*base, "clean", "--profile", profile, "--apply", "--yes", "--quiet"]


def build_create_command(
    profile: str = "safe",
    frequency: str = "weekly",
    *,
    at: str = "03:00",
    day: str = "SUN",
    highest: bool = False,
    executable: str | None = None,
    task_name: str = TASK_NAME,
) -> list[str]:
    """Build the ``schtasks /Create`` argument list.

    Raises:
        ValueError: for an unknown profile/frequency/day or malformed time.
    """
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}")
    if frequency not in FREQUENCIES:
        raise ValueError(f"frequency must be one of {', '.join(FREQUENCIES)}")
    if not _TIME_RE.match(at):
        raise ValueError("time must be HH:MM (24h)")
    day = day.upper()
    args = [
        "schtasks",
        "/Create",
        "/F",
        "/TN",
        task_name,
        "/TR",
        subprocess.list2cmdline(disksweep_command(profile, executable)),
        "/SC",
        FREQUENCIES[frequency],
        "/ST",
        at,
    ]
    if frequency == "weekly":
        if day not in DAYS:
            raise ValueError(f"day must be one of {', '.join(DAYS)}")
        args += ["/D", day]
    elif frequency == "monthly":
        args += ["/D", "1"]
    args += ["/RL", "HIGHEST" if highest else "LIMITED"]
    return args


def build_delete_command(task_name: str = TASK_NAME) -> list[str]:
    """``schtasks /Delete`` argument list."""
    return ["schtasks", "/Delete", "/F", "/TN", task_name]


def build_query_command(task_name: str = TASK_NAME) -> list[str]:
    """``schtasks /Query`` argument list."""
    return ["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"]


def run(args: Sequence[str]) -> osutils.CommandResult:
    """Execute a schtasks command (Windows only)."""
    if not osutils.IS_WINDOWS:
        return osutils.CommandResult(tuple(args), 1, "", "Task Scheduler is Windows-only")
    return osutils.run_cmd(args, timeout=60)
