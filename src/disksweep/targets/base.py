"""Base classes for cleanup targets.

A *target* is one thing DiskSweep can clean (user temp, Chrome cache, npm cache,
DISM component store, ...). Each has an ``id``, ``name``, ``description``,
``risk`` level, ``requires_admin`` flag and ``scan()``/``clean()`` methods.

Two reusable kinds cover almost everything:

* :class:`DirectoryTarget` - empties one or more folders found via environment
  variables and globs, optionally filtered by age and file pattern.
* :class:`CommandTarget` - runs an external tool (``docker system prune``,
  ``dism``, ``powercfg``, ...).
"""

from __future__ import annotations

import glob
import logging
import os
import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from disksweep import osutils
from disksweep.audit import AuditLog
from disksweep.fsops import Deleter
from disksweep.models import CleanResult, DeleteMode, Risk, ScanResult, SkippedItem
from disksweep.safety import PathGuard, path_is_link
from disksweep.units import human_size

log = logging.getLogger(__name__)

#: A path template: ``("LOCALAPPDATA", "Google", "Chrome", "User Data", "*", "Cache")``.
#: The first element names an environment variable; later parts may contain globs.
PathSpec = tuple[str, ...]


@dataclass
class TargetContext:
    """Everything a target needs at run time (injected, so tests can fake it)."""

    guard: PathGuard
    environ: Mapping[str, str] = field(default_factory=lambda: osutils.EnvMap(osutils.env()))
    audit: AuditLog | None = None
    is_admin: bool = False
    cancel: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        if not isinstance(self.environ, osutils.EnvMap):
            self.environ = osutils.EnvMap(self.environ)


class CleanupTarget(ABC):
    """Abstract cleanup target."""

    id: str = ""
    name: str = ""
    description: str = ""
    risk: Risk = Risk.SAFE
    requires_admin: bool = False
    category: str = "system"
    #: Processes that must not be running while cleaning (e.g. ``chrome.exe``).
    process_names: tuple[str, ...] = ()

    @abstractmethod
    def scan(self, ctx: TargetContext) -> ScanResult:
        """Measure how much could be freed. Must not modify anything."""

    @abstractmethod
    def clean(self, ctx: TargetContext, dry_run: bool = True) -> CleanResult:
        """Free space. With ``dry_run=True`` (the default) nothing is modified."""

    # -- shared helpers ---------------------------------------------------- #

    def blocking_processes(self) -> set[str]:
        """Names of required-closed processes that are currently running."""
        if not self.process_names:
            return set()
        return osutils.running_processes(self.process_names)

    def precheck(self, ctx: TargetContext) -> str | None:
        """Return a reason this target cannot run now, or None."""
        if self.requires_admin and not ctx.is_admin:
            return "requires administrator rights"
        running = self.blocking_processes()
        if running:
            return f"skipped: {', '.join(sorted(running))} is running - close it and retry"
        return None

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.id}>"


def expand_specs(specs: Sequence[PathSpec], environ: Mapping[str, str]) -> list[str]:
    """Expand path templates to existing, de-duplicated absolute paths."""
    seen: set[str] = set()
    out: list[str] = []
    for spec in specs:
        base = environ.get(spec[0])
        if not base:
            continue
        pattern = os.path.join(base, *spec[1:])
        matches = glob.glob(pattern) if glob.has_magic(pattern) else [pattern]
        for match in sorted(matches):
            if not os.path.lexists(match):
                continue
            key = os.path.normcase(os.path.abspath(match))
            if key not in seen:
                seen.add(key)
                out.append(os.path.abspath(match))
    return out


class DirectoryTarget(CleanupTarget):
    """Empties folders (or deletes single files) located via :data:`PathSpec`s.

    Subclasses / instances set:

    * ``specs``: folders whose *contents* are purged (or files to delete).
    * ``min_age_hours``: skip files modified more recently (protects files in use).
    * ``patterns``: only delete files matching these globs.
    * ``mode``: direct delete (caches) or Recycle Bin.
    * ``delete_root``: remove the folder itself too (e.g. ``Windows.old``).
    * ``service``: Windows service to stop during cleaning and restart afterwards.
    """

    specs: Sequence[PathSpec] = ()
    min_age_hours: float = 0
    patterns: Sequence[str] = ()
    mode: DeleteMode = DeleteMode.DIRECT
    delete_root: bool = False
    service: str | None = None

    def __init__(self, **overrides: object) -> None:
        for key, value in overrides.items():
            if not hasattr(self, key):
                raise AttributeError(f"unknown target attribute {key!r}")
            setattr(self, key, value)

    def roots(self, ctx: TargetContext) -> list[str]:
        """Existing folders/files this target operates on."""
        return expand_specs(self.specs, ctx.environ)

    def _run(self, ctx: TargetContext, dry_run: bool) -> CleanResult:
        deleter = Deleter(
            ctx.guard,
            dry_run=dry_run,
            mode=self.mode,
            audit=ctx.audit,
            target_id=self.id,
            cancel=ctx.cancel,
            min_age_seconds=self.min_age_hours * 3600 or None,
            patterns=self.patterns or None,
        )
        for root in self.roots(ctx):
            if ctx.cancel.is_set():
                break
            if path_is_link(root):
                deleter.result.skipped.append(
                    SkippedItem(root, "location is a symlink/junction; not followed")
                )
                continue
            if self.delete_root or not os.path.isdir(root):
                deleter.delete_path(root, within=root)
            else:
                deleter.purge_contents(root)
        return deleter.result

    def scan(self, ctx: TargetContext) -> ScanResult:
        """Dry-run walk: reports exactly what :meth:`clean` would delete."""
        roots = self.roots(ctx)
        if not roots:
            return ScanResult(self.id, available=False, note="not found")
        res = self._run(ctx, dry_run=True)
        return ScanResult(
            self.id,
            bytes=res.bytes_freed,
            files=res.files_deleted,
            paths=roots,
            skipped=res.skipped,
        )

    def clean(self, ctx: TargetContext, dry_run: bool = True) -> CleanResult:
        """Delete (or, in a dry run, count) this target's files."""
        # The dry run mirrors a real run: a target that would be skipped is skipped.
        reason = self.precheck(ctx)
        if reason:
            return CleanResult(self.id, dry_run=dry_run, ran=False, note=reason)
        restart = False
        if self.service and not dry_run and osutils.service_running(self.service):
            if not osutils.stop_service(self.service):
                return CleanResult(
                    self.id,
                    dry_run=dry_run,
                    ran=False,
                    note=f"could not stop service {self.service}",
                )
            restart = True
            if ctx.audit:
                ctx.audit.record_event("service_stop", service=self.service)
        try:
            return self._run(ctx, dry_run)
        finally:
            if restart and self.service:
                osutils.start_service(self.service)
                if ctx.audit:
                    ctx.audit.record_event("service_start", service=self.service)


class CommandTarget(CleanupTarget):
    """Runs an external command to free space.

    ``command`` is built lazily (``build_command``) so tools can be located at
    run time. ``measure`` optionally estimates reclaimable bytes.
    """

    executable: str = ""
    command: Sequence[str] = ()
    timeout: float = osutils.DEFAULT_TIMEOUT

    def build_command(self, ctx: TargetContext) -> list[str] | None:
        """Return the command to run, or None if the tool is unavailable."""
        if self.executable and not osutils.which(self.executable):
            return None
        return list(self.command)

    def measure(self, ctx: TargetContext) -> tuple[int, bool, str]:
        """Return ``(bytes, estimated, note)`` for the scan. Default: unknown size."""
        return 0, True, "size determined when run"

    def current_size(self, ctx: TargetContext) -> int | None:
        """Size of the data this command shrinks, if measurable.

        Used before/after running to compute bytes freed. ``None`` means "use the
        change in free space on the system drive instead".
        """
        return None

    def describe(self, ctx: TargetContext) -> str:
        """Human-readable description of what would run."""
        return " ".join(self.build_command(ctx) or [])

    def is_available(self, ctx: TargetContext) -> bool:
        """True if this target can run on this machine."""
        return osutils.IS_WINDOWS and self.build_command(ctx) is not None

    def scan(self, ctx: TargetContext) -> ScanResult:
        """Report availability and an estimate of reclaimable space."""
        if not self.is_available(ctx):
            return ScanResult(self.id, available=False, note="not available on this system")
        size, estimated, note = self.measure(ctx)
        return ScanResult(self.id, bytes=size, estimated=estimated, note=note)

    def run_steps(self, ctx: TargetContext) -> tuple[bool, str]:
        """Execute the command(s). Override for multi-step targets."""
        cmd = self.build_command(ctx)
        if cmd is None:
            return False, "tool not found"
        res = osutils.run_cmd(cmd, timeout=self.timeout)
        if ctx.audit:
            ctx.audit.record_event("command", target=self.id, cmd=cmd, returncode=res.returncode)
        out = (res.stdout or res.stderr).strip().splitlines()
        return res.ok, (out[-1] if out else f"exit code {res.returncode}")

    def _size_probe(self, ctx: TargetContext) -> int:
        size = self.current_size(ctx)
        if size is not None:
            return size
        # Fall back to free space: freed bytes = free_after - free_before.
        return -osutils.disk_free(osutils.system_drive(ctx.environ))

    def clean(self, ctx: TargetContext, dry_run: bool = True) -> CleanResult:
        """Run the command (or describe it, in a dry run)."""
        if not self.is_available(ctx):
            return CleanResult(self.id, dry_run=dry_run, ran=False, note="not available")
        reason = self.precheck(ctx)
        if reason:
            return CleanResult(self.id, dry_run=dry_run, ran=False, note=reason)
        if dry_run:
            size, estimated, note = self.measure(ctx)
            if estimated and size:
                note = f"up to {human_size(size)}; {note}"
            return CleanResult(
                self.id,
                dry_run=True,
                # Estimates are shown in the note but never added to "would free".
                bytes_freed=0 if estimated else size,
                note=f"would run: {self.describe(ctx)}" + (f" ({note})" if note else ""),
            )
        before = self._size_probe(ctx)
        ok, msg = self.run_steps(ctx)
        after = self._size_probe(ctx)
        result = CleanResult(self.id, dry_run=False, bytes_freed=max(0, before - after), note=msg)
        if not ok:
            result.errors.append(msg)
        return result
