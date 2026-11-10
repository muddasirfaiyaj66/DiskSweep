"""Orchestrates scanning and cleaning of targets. Shared by the CLI and GUI."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from disksweep import osutils
from disksweep.audit import AuditLog
from disksweep.models import CleanReport, CleanResult, ScanResult
from disksweep.safety import PathGuard, default_guard
from disksweep.targets.base import CleanupTarget, TargetContext

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int], None]
"""``callback(target_id, done, total)``."""


def make_context(
    *,
    guard: PathGuard | None = None,
    environ: Mapping[str, str] | None = None,
    audit: AuditLog | None = None,
    is_admin: bool | None = None,
    cancel: threading.Event | None = None,
) -> TargetContext:
    """Build a :class:`TargetContext` with sensible defaults for this machine."""
    env = osutils.EnvMap(environ if environ is not None else osutils.env())
    return TargetContext(
        guard=guard or default_guard(env),
        environ=env,
        audit=audit,
        is_admin=osutils.is_admin() if is_admin is None else is_admin,
        cancel=cancel or threading.Event(),
    )


def scan_targets(
    targets: Sequence[CleanupTarget],
    ctx: TargetContext,
    progress: ProgressCallback | None = None,
    max_workers: int = 4,
) -> dict[str, ScanResult]:
    """Scan targets in parallel. Never modifies anything.

    A target that raises is reported as unavailable instead of aborting the scan.
    """
    results: dict[str, ScanResult] = {}
    total = len(targets)

    def _scan(t: CleanupTarget) -> ScanResult:
        try:
            return t.scan(ctx)
        except Exception as exc:
            log.exception("scan failed", extra={"target": t.id})
            return ScanResult(t.id, available=False, note=f"scan error: {exc}")

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="scan") as pool:
        futures = {pool.submit(_scan, t): t for t in targets}
        for done, fut in enumerate(as_completed(futures), start=1):
            target = futures[fut]
            results[target.id] = fut.result()
            if progress:
                progress(target.id, done, total)
    # preserve input order
    return {t.id: results[t.id] for t in targets}


def clean_targets(
    targets: Sequence[CleanupTarget],
    ctx: TargetContext,
    *,
    apply: bool = False,
    progress: ProgressCallback | None = None,
) -> CleanReport:
    """Clean targets sequentially.

    Args:
        targets: targets to clean.
        ctx: run context.
        apply: **must be True to delete anything**; otherwise this is a dry run.
        progress: optional progress callback.

    Returns:
        A :class:`CleanReport` including free space before/after (real runs).
    """
    dry_run = not apply
    drive = osutils.system_drive(ctx.environ)
    report = CleanReport(dry_run=dry_run, drive=drive)
    try:
        report.free_before = osutils.disk_free(drive)
    except OSError:
        report.free_before = None

    if ctx.audit and not dry_run:
        ctx.audit.record_event(
            "run_start", targets=[t.id for t in targets], free_before=report.free_before
        )

    total = len(targets)
    for done, target in enumerate(targets, start=1):
        if ctx.cancel.is_set():
            report.results.append(
                CleanResult(target.id, dry_run=dry_run, ran=False, note="cancelled")
            )
            continue
        log.info("cleaning", extra={"target": target.id, "dry_run": dry_run})
        try:
            result = target.clean(ctx, dry_run=dry_run)
        except Exception as exc:
            log.exception("clean failed", extra={"target": target.id})
            result = CleanResult(target.id, dry_run=dry_run, ran=False, errors=[str(exc)])
        report.results.append(result)
        if progress:
            progress(target.id, done, total)

    try:
        report.free_after = osutils.disk_free(drive)
    except OSError:
        report.free_after = None

    if ctx.audit and not dry_run:
        ctx.audit.record_event(
            "run_summary",
            free_before=report.free_before,
            free_after=report.free_after,
            bytes_freed=report.total_bytes,
            files_deleted=report.total_files,
            skipped=report.total_skipped,
        )
        ctx.audit.flush()
    return report
