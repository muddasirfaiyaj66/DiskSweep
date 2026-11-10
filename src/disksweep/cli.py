"""Command-line interface.

Examples::

    disksweep scan
    disksweep clean --profile safe --apply
    disksweep analyze C:\\ --top 30
    disksweep duplicates D:\\Downloads --min-size 10MB
    disksweep schedule --weekly --profile safe

Nothing is ever deleted unless ``--apply`` is passed.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

from disksweep import osutils
from disksweep.analyzer import analyze, export_csv, export_json
from disksweep.audit import AuditLog, log_dir, tail
from disksweep.duplicates import find_duplicates, remove_duplicates
from disksweep.engine import clean_targets, make_context, scan_targets
from disksweep.largefiles import find_large_files
from disksweep.logging_setup import configure_logging
from disksweep.models import CleanReport, Risk
from disksweep.profiles import PROFILES, profile_targets
from disksweep.scheduler import (
    DAYS,
    build_create_command,
    build_delete_command,
    build_query_command,
)
from disksweep.scheduler import run as run_schtasks
from disksweep.targets.base import CleanupTarget
from disksweep.targets.registry import all_targets, get_targets, target_ids
from disksweep.units import human_size, parse_size
from disksweep.version import __version__

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _size_arg(text: str) -> int:
    try:
        return parse_size(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _print(args: argparse.Namespace, *parts: object) -> None:
    if not getattr(args, "quiet", False):
        print(*parts)


def _table(rows: Sequence[Sequence[str]], headers: Sequence[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(c)) for w, c in zip(widths, row, strict=True)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    lines += [fmt.format(*row) for row in rows]
    return "\n".join(lines)


def _select_targets(args: argparse.Namespace, default_profile: str | None) -> list[CleanupTarget]:
    if getattr(args, "target", None):
        return get_targets(args.target)
    profile = getattr(args, "profile", None) or default_profile
    if profile is None:
        return all_targets()
    return get_targets(profile_targets(profile))


def _dump_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def cmd_targets(args: argparse.Namespace) -> int:
    """List all cleanup targets."""
    targets = all_targets()
    if args.json:
        _dump_json(
            [
                {
                    "id": t.id,
                    "name": t.name,
                    "description": t.description,
                    "risk": t.risk.value,
                    "requires_admin": t.requires_admin,
                    "category": t.category,
                }
                for t in targets
            ]
        )
        return EXIT_OK
    rows = [
        [t.id, t.risk.value, "yes" if t.requires_admin else "", t.category, t.name] for t in targets
    ]
    print(_table(rows, ["ID", "RISK", "ADMIN", "CATEGORY", "NAME"]))
    print("\nProfiles: " + ", ".join(f"{k} ({len(v)} targets)" for k, v in PROFILES.items()))
    return EXIT_OK


def cmd_scan(args: argparse.Namespace) -> int:
    """Measure reclaimable space. Read-only."""
    targets = _select_targets(args, None)
    ctx = make_context()
    if not args.json:
        _print(args, f"Scanning {len(targets)} targets...")
    results = scan_targets(targets, ctx)
    if args.json:
        _dump_json({tid: r.to_dict() for tid, r in results.items()})
        return EXIT_OK
    rows = []
    total = 0
    for t in targets:
        r = results[t.id]
        if not r.available:
            continue
        size = ("~" if r.estimated else "") + human_size(r.bytes)
        admin = "admin" if t.requires_admin and not ctx.is_admin else ""
        rows.append([t.id, t.risk.value, admin, size, str(r.files or ""), r.note])
        if not r.estimated:
            total += r.bytes
    print(_table(rows, ["TARGET", "RISK", "", "SIZE", "FILES", "NOTE"]))
    print(f"\nReclaimable (measured): {human_size(total)}")
    if not ctx.is_admin and any(t.requires_admin for t in targets):
        print("Note: targets marked 'admin' need an elevated prompt to clean (use --elevate).")
    print("Nothing was deleted. Run `disksweep clean --profile safe` to preview a cleanup.")
    return EXIT_OK


def _confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def _print_report(
    args: argparse.Namespace, report: CleanReport, targets: Sequence[CleanupTarget]
) -> None:
    names = {t.id: t.name for t in targets}
    rows = []
    for r in report.results:
        status = "skipped" if not r.ran else ("error" if r.errors else "ok")
        rows.append(
            [
                r.target_id,
                status,
                human_size(r.bytes_freed),
                str(r.files_deleted),
                str(len(r.skipped) or ""),
                r.note or names.get(r.target_id, ""),
            ]
        )
    verb = "WOULD FREE" if report.dry_run else "FREED"
    _print(args, _table(rows, ["TARGET", "STATUS", verb, "FILES", "SKIPPED", "NOTE"]))
    _print(args, "")
    _print(
        args,
        f"Total {verb.lower()}: {human_size(report.total_bytes)} in "
        f"{report.total_files} files ({report.total_skipped} items skipped)",
    )
    if report.free_before is not None:
        line = f"Free space on {report.drive}: {human_size(report.free_before)}"
        if not report.dry_run and report.free_after is not None:
            delta = report.free_after - report.free_before
            line += (
                f" -> {human_size(report.free_after)} ({'+' if delta >= 0 else ''}"
                f"{human_size(delta)})"
            )
        _print(args, line)
    if args.verbose and report.total_skipped:
        _print(args, "\nSkipped items:")
        for r in report.results:
            for s in r.skipped[:50]:
                _print(args, f"  {s.path}: {s.reason}")
    if report.dry_run:
        _print(args, "\nDRY RUN - nothing was deleted. Re-run with --apply to clean.")


def cmd_clean(args: argparse.Namespace) -> int:
    """Clean targets (dry run unless --apply)."""
    targets = _select_targets(args, "safe")
    is_admin = osutils.is_admin()
    needs_admin = [t for t in targets if t.requires_admin]

    if args.apply and needs_admin and not is_admin and args.elevate:
        argv = [a for a in (args.raw_argv or []) if a != "--elevate"]
        if osutils.relaunch_elevated(argv):
            _print(args, "Relaunched with administrator rights in a new window.")
            return EXIT_OK
        print("Elevation was cancelled or failed.", file=sys.stderr)
        return EXIT_ERROR

    risky = [t for t in targets if t.risk is not Risk.SAFE]
    if args.apply and risky and not args.yes:
        listing = ", ".join(f"{t.id} ({t.risk.value})" for t in risky)
        if not sys.stdin.isatty():
            print(f"Refusing to clean non-safe targets without --yes: {listing}", file=sys.stderr)
            return EXIT_USAGE
        print(f"The following targets are not 'safe': {listing}")
        if not _confirm("Proceed with cleanup?"):
            print("Aborted. Nothing was deleted.")
            return EXIT_OK

    audit = AuditLog() if args.apply else None
    ctx = make_context(audit=audit, is_admin=is_admin)
    if needs_admin and not is_admin:
        _print(
            args,
            "Not elevated: "
            + ", ".join(t.id for t in needs_admin)
            + " will be skipped (use --elevate).",
        )
    try:
        report = clean_targets(targets, ctx, apply=args.apply)
    finally:
        if audit:
            audit.close()
    if args.json:
        _dump_json(report.to_dict())
    else:
        _print_report(args, report, targets)
        if audit:
            _print(args, f"Audit log: {audit.path}")
    return EXIT_OK


def cmd_analyze(args: argparse.Namespace) -> int:
    """Show the largest folders and files under a path."""
    res = analyze(args.path, top=args.top, workers=args.workers, max_depth=args.max_depth)
    if args.json:
        export_json(res, args.json)
    if args.csv:
        export_csv(res, args.csv)
    print(
        f"{res.root}: {human_size(res.total_bytes)} in {res.file_count:,} files, "
        f"{res.dir_count:,} folders ({res.elapsed:.1f}s)"
    )
    print("\nLargest folders:")
    print(_table([[human_size(e.bytes), e.path] for e in res.top_dirs], ["SIZE", "PATH"]))
    print("\nLargest files:")
    print(_table([[human_size(e.bytes), e.path] for e in res.top_files], ["SIZE", "PATH"]))
    if res.by_type:
        print("\nSpace by file type:")
        total = res.total_bytes or 1
        rows = [[human_size(t.bytes), f"{t.bytes / total:.0%}", f"{t.files:,}", t.ext]
                for t in res.by_type]  # fmt: skip
        print(_table(rows, ["SIZE", "SHARE", "FILES", "TYPE"]))
    if res.errors:
        print(f"\n{len(res.errors)} folders could not be read (access denied / in use).")
    for path, kind in ((args.json, "JSON"), (args.csv, "CSV")):
        if path:
            print(f"{kind} written to {path}")
    return EXIT_OK


def cmd_duplicates(args: argparse.Namespace) -> int:
    """Find (and optionally recycle) duplicate files."""
    groups = find_duplicates(args.paths, min_size=args.min_size)
    wasted = sum(g.wasted for g in groups)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump([g.to_dict() for g in groups], fh, indent=2)
    print(f"{len(groups)} duplicate groups, {human_size(wasted)} reclaimable")
    for g in groups[: args.top]:
        print(f"\n{human_size(g.size)} x {len(g.files)}  (wasted {human_size(g.wasted)})")
        for f in g.files:
            print(f"   {f}")
    if args.remove:
        if (
            args.apply
            and not args.yes
            and not _confirm(
                f"Move {sum(len(g.files) - 1 for g in groups)} duplicate files to the Recycle Bin?"
            )
        ):
            print("Aborted.")
            return EXIT_OK
        ctx = make_context()
        audit = AuditLog() if args.apply else None
        try:
            res = remove_duplicates(
                groups, ctx.guard, keep=args.keep, dry_run=not args.apply, audit=audit
            )
        finally:
            if audit:
                audit.close()
        verb = "Recycled" if args.apply else "Would recycle"
        print(
            f"\n{verb} {res.files_deleted} files ({human_size(res.bytes_freed)}); "
            f"{len(res.skipped)} skipped"
        )
        for s in res.skipped[:20]:
            print(f"   skipped {s.path}: {s.reason}")
        if not args.apply:
            print("DRY RUN - add --apply to move duplicates to the Recycle Bin.")
    return EXIT_OK


def cmd_large(args: argparse.Namespace) -> int:
    """List large / old files."""
    files = find_large_files(
        args.paths, args.min_size, older_than_days=args.older_than, top=args.top
    )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump([f.to_dict() for f in files], fh, indent=2)
    rows = [[human_size(f.bytes), f"{f.age_days:.0f}d", f.path] for f in files]
    print(_table(rows, ["SIZE", "AGE", "PATH"]))
    print(f"\n{len(files)} files, {human_size(sum(f.bytes for f in files))} total")
    return EXIT_OK


def cmd_schedule(args: argparse.Namespace) -> int:
    """Create, show or remove the scheduled cleanup task."""
    if args.remove:
        cmd = build_delete_command()
    elif args.show:
        cmd = build_query_command()
    else:
        frequency = "daily" if args.daily else "monthly" if args.monthly else "weekly"
        try:
            cmd = build_create_command(
                args.profile, frequency, at=args.time, day=args.day, highest=args.highest
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
    if args.dry_run:
        print("Would run: " + subprocess.list2cmdline(cmd))
        return EXIT_OK
    res = run_schtasks(cmd)
    print((res.stdout or res.stderr).strip())
    return EXIT_OK if res.ok else EXIT_ERROR


def cmd_log(args: argparse.Namespace) -> int:
    """Show the audit log."""
    if args.path:
        print(log_dir())
        return EXIT_OK
    lines = tail(args.tail)
    if not lines:
        print(f"No audit entries yet (logs are written to {log_dir()}).")
    for line in lines:
        print(line)
    return EXIT_OK


def cmd_update_check(args: argparse.Namespace) -> int:
    """Explicit, opt-in update check (the only network access in DiskSweep)."""
    from disksweep.updatecheck import check_for_update

    newer = check_for_update()
    print(
        f"A newer version is available: {newer}"
        if newer
        else f"DiskSweep {__version__} is up to date (or the check failed)."
    )
    return EXIT_OK


def cmd_gui(args: argparse.Namespace) -> int:
    """Launch the graphical interface."""
    from disksweep.gui.app import main as gui_main

    gui_main()
    return EXIT_OK


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v", "--verbose", action="count", default=0, help="more output (-vv for debug logging)"
    )
    common.add_argument(
        "--log-format",
        choices=["text", "json"],
        default="text",
        help="format of diagnostic logs on stderr",
    )

    p = argparse.ArgumentParser(
        prog="disksweep",
        description="Safe disk cleanup for Windows. Dry run by default: nothing is deleted "
        "without --apply.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    def add(name: str, func: Any, help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_, description=help_, parents=[common])
        sp.set_defaults(func=func)
        return sp

    def target_opts(sp: argparse.ArgumentParser, default_profile: str | None) -> None:
        sp.add_argument(
            "--profile",
            choices=list(PROFILES),
            default=None,
            help=f"target set (default: {default_profile or 'all targets'})",
        )
        sp.add_argument(
            "--target",
            action="append",
            choices=target_ids(),
            metavar="ID",
            help="specific target id (repeatable; see `disksweep targets`)",
        )

    sp = add("scan", cmd_scan, "Measure reclaimable space (read-only)")
    target_opts(sp, None)
    sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = add("clean", cmd_clean, "Clean targets (dry run unless --apply)")
    target_opts(sp, "safe")
    sp.add_argument("--apply", action="store_true", help="actually delete files")
    sp.add_argument(
        "-y", "--yes", action="store_true", help="do not ask for confirmation of non-safe targets"
    )
    sp.add_argument(
        "--elevate",
        action="store_true",
        help="relaunch with administrator rights if a target needs them",
    )
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.add_argument("-q", "--quiet", action="store_true", help="no output (for scheduled runs)")

    sp = add("analyze", cmd_analyze, "Show the largest folders and files")
    sp.add_argument("path", help=r"folder or drive, e.g. C:\ ")
    sp.add_argument("--top", type=int, default=20)
    sp.add_argument(
        "--max-depth", type=int, default=None, help="only list folders up to N levels below PATH"
    )
    sp.add_argument("--workers", type=int, default=None, help="scanner threads")
    sp.add_argument("--json", metavar="FILE", help="export results to JSON")
    sp.add_argument("--csv", metavar="FILE", help="export results to CSV")

    sp = add("duplicates", cmd_duplicates, "Find duplicate files")
    sp.add_argument("paths", nargs="+")
    sp.add_argument(
        "--min-size",
        type=_size_arg,
        default=parse_size("1MB"),
        help="ignore smaller files (default 1MB)",
    )
    sp.add_argument("--top", type=int, default=20, help="groups to print")
    sp.add_argument("--json", metavar="FILE", help="export groups to JSON")
    sp.add_argument(
        "--remove",
        action="store_true",
        help="recycle all but one copy of each group (dry run unless --apply)",
    )
    sp.add_argument("--keep", choices=["oldest", "newest", "shortest", "first"], default="oldest")
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("-y", "--yes", action="store_true")

    sp = add("large", cmd_large, "Find large and/or old files")
    sp.add_argument("paths", nargs="+")
    sp.add_argument("--min-size", type=_size_arg, default=parse_size("500MB"))
    sp.add_argument(
        "--older-than",
        type=float,
        default=None,
        metavar="DAYS",
        help="only files not modified for DAYS days",
    )
    sp.add_argument("--top", type=int, default=50)
    sp.add_argument("--json", metavar="FILE")

    sp = add("schedule", cmd_schedule, "Schedule automatic cleanup (Task Scheduler)")
    when = sp.add_mutually_exclusive_group()
    when.add_argument("--daily", action="store_true")
    when.add_argument("--weekly", action="store_true", help="(default)")
    when.add_argument("--monthly", action="store_true")
    when.add_argument("--remove", action="store_true", help="delete the scheduled task")
    when.add_argument("--show", action="store_true", help="show the scheduled task")
    sp.add_argument("--profile", choices=list(PROFILES), default="safe")
    sp.add_argument("--time", default="03:00", help="HH:MM (default 03:00)")
    sp.add_argument(
        "--day", default="SUN", type=str.upper, choices=DAYS, help="weekday for --weekly"
    )
    sp.add_argument(
        "--highest",
        action="store_true",
        help="run with highest privileges (needed for admin-only targets)",
    )
    sp.add_argument("--dry-run", action="store_true", help="print the schtasks command only")

    add("targets", cmd_targets, "List cleanup targets and profiles").add_argument(
        "--json", action="store_true"
    )

    sp = add("log", cmd_log, "Show the audit log of deleted files")
    sp.add_argument("--tail", type=int, default=50)
    sp.add_argument("--path", action="store_true", help="print the log folder")

    add("update-check", cmd_update_check, "Check GitHub for a newer release (opt-in)")
    add("gui", cmd_gui, "Launch the graphical interface")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(raw)
    args.raw_argv = raw
    configure_logging(
        args.verbose if hasattr(args, "verbose") else 0, getattr(args, "log_format", "text")
    )
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return int(args.func(args))
    except KeyError as exc:
        print(f"error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return EXIT_USAGE
    except (NotADirectoryError, FileNotFoundError) as exc:
        print(f"error: not a folder: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
