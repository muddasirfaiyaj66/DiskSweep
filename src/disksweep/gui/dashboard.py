"""Dashboard tab: drive usage, scan, target checklist and cleanup."""

from __future__ import annotations

import threading
from tkinter import messagebox
from typing import Any

import customtkinter as ctk

from disksweep import osutils
from disksweep.audit import AuditLog
from disksweep.engine import clean_targets, make_context, scan_targets
from disksweep.gui.workers import TaskRunner
from disksweep.models import CleanReport, Risk, ScanResult
from disksweep.profiles import PROFILES
from disksweep.targets.base import CleanupTarget
from disksweep.targets.registry import all_targets
from disksweep.units import human_size

RISK_COLORS = {
    Risk.SAFE: ("#2e7d32", "#388e3c"),
    Risk.MODERATE: ("#b26a00", "#f57c00"),
    Risk.ADVANCED: ("#b71c1c", "#d32f2f"),
}


class TargetRow:
    """One line in the target checklist."""

    def __init__(
        self, parent: ctk.CTkScrollableFrame, target: CleanupTarget, row: int, is_admin: bool
    ) -> None:
        self.target = target
        self.var = ctk.BooleanVar(value=False)
        self.check = ctk.CTkCheckBox(parent, text=target.name, variable=self.var)
        self.check.grid(row=row * 2, column=0, sticky="w", padx=(8, 4), pady=(6, 0))
        badge_text = target.risk.value.upper()
        self.badge = ctk.CTkLabel(
            parent,
            text=f" {badge_text} ",
            corner_radius=6,
            fg_color=RISK_COLORS[target.risk],
            text_color="white",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.badge.grid(row=row * 2, column=1, padx=4, pady=(6, 0))
        admin_text = "admin" if target.requires_admin else ""
        self.admin = ctk.CTkLabel(
            parent,
            text=admin_text,
            width=50,
            text_color=("#b71c1c" if not is_admin else "gray50")
            if target.requires_admin
            else "gray50",
        )
        self.admin.grid(row=row * 2, column=2, padx=4, pady=(6, 0))
        self.size = ctk.CTkLabel(parent, text="-", width=90, anchor="e")
        self.size.grid(row=row * 2, column=3, padx=(4, 12), pady=(6, 0), sticky="e")
        self.desc = ctk.CTkLabel(
            parent,
            text=target.description,
            text_color="gray55",
            font=ctk.CTkFont(size=11),
            anchor="w",
            justify="left",
            wraplength=620,
        )
        self.desc.grid(row=row * 2 + 1, column=0, columnspan=4, sticky="w", padx=(36, 8))

    def set_result(self, result: ScanResult | None) -> None:
        """Show the scan size (or why the target is unavailable)."""
        if result is None:
            self.size.configure(text="-")
        elif not result.available:
            self.size.configure(text="n/a")
            self.check.configure(state="disabled")
            self.var.set(False)
        else:
            prefix = "~" if result.estimated else ""
            text = (
                prefix + human_size(result.bytes) if result.bytes or not result.estimated else "?"
            )
            self.size.configure(text=text)
            self.check.configure(state="normal")


class Dashboard(ctk.CTkFrame):
    """Main tab."""

    def __init__(self, master: Any, runner: TaskRunner) -> None:
        super().__init__(master, fg_color="transparent")
        self.runner = runner
        self.is_admin = osutils.is_admin()
        self.targets = all_targets()
        self.results: dict[str, ScanResult] = {}
        self.cancel: threading.Event | None = None
        self._build()
        self.refresh_drive()
        self.apply_profile("safe")

    # -- layout ---------------------------------------------------------- #

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        drive_box = ctk.CTkFrame(self)
        drive_box.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        drive_box.grid_columnconfigure(1, weight=1)
        drives = osutils.list_drives() or [osutils.system_drive()]
        sysdrive = osutils.system_drive()
        self.drive_var = ctk.StringVar(value=sysdrive if sysdrive in drives else drives[0])
        ctk.CTkOptionMenu(
            drive_box,
            values=drives,
            variable=self.drive_var,
            width=90,
            command=lambda _v: self.refresh_drive(),
        ).grid(row=0, column=0, padx=10, pady=10)
        self.usage_bar = ctk.CTkProgressBar(drive_box, height=18)
        self.usage_bar.grid(row=0, column=1, sticky="ew", padx=10)
        self.usage_label = ctk.CTkLabel(drive_box, text="", width=260, anchor="e")
        self.usage_label.grid(row=0, column=2, padx=10)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        ctk.CTkLabel(bar, text="Profile:").pack(side="left", padx=(0, 6))
        self.profile_var = ctk.StringVar(value="Safe")
        ctk.CTkSegmentedButton(
            bar,
            values=["Safe", "Developer", "Aggressive"],
            variable=self.profile_var,
            command=lambda v: self.apply_profile(v.lower()),
        ).pack(side="left")
        self.scan_btn = ctk.CTkButton(bar, text="Scan", width=110, command=self.start_scan)
        self.scan_btn.pack(side="left", padx=(16, 6))
        self.clean_btn = ctk.CTkButton(
            bar,
            text="Clean selected",
            width=140,
            fg_color=("#c62828", "#b71c1c"),
            hover_color=("#8e0000", "#7f0000"),
            command=self.confirm_clean,
        )
        self.clean_btn.pack(side="left", padx=6)
        self.cancel_btn = ctk.CTkButton(
            bar,
            text="Cancel",
            width=90,
            state="disabled",
            fg_color="gray40",
            command=self.cancel_task,
        )
        self.cancel_btn.pack(side="left", padx=6)
        self.total_label = ctk.CTkLabel(bar, text="", font=ctk.CTkFont(weight="bold"))
        self.total_label.pack(side="right", padx=6)

        self.list_frame = ctk.CTkScrollableFrame(self, label_text="Cleanup targets")
        self.list_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=6)
        self.list_frame.grid_columnconfigure(0, weight=1)
        self.rows = {
            t.id: TargetRow(self.list_frame, t, i, self.is_admin)
            for i, t in enumerate(self.targets)
        }
        for row in self.rows.values():
            row.var.trace_add("write", lambda *_: self.update_total())

        status = ctk.CTkFrame(self, fg_color="transparent")
        status.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        status.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(status, mode="determinate")
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.status = ctk.CTkLabel(
            status, text="Ready. Nothing is deleted until you confirm.", anchor="w"
        )
        self.status.grid(row=1, column=0, sticky="w")

    # -- state helpers ----------------------------------------------------- #

    def refresh_drive(self) -> None:
        """Update the drive usage bar."""
        try:
            total, used, free = osutils.disk_usage(self.drive_var.get())
        except OSError:
            self.usage_label.configure(text="unavailable")
            return
        self.usage_bar.set(used / total if total else 0)
        self.usage_bar.configure(
            progress_color="#d32f2f"
            if free / total < 0.1
            else ("#f57c00" if free / total < 0.2 else ("#3a7ebf", "#1f6aa5"))
        )
        self.usage_label.configure(
            text=f"{human_size(free)} free of {human_size(total)} ({used / total:.0%} used)"
        )

    def apply_profile(self, name: str) -> None:
        """Tick the targets belonging to a profile."""
        ids = set(PROFILES.get(name, ()))
        for tid, row in self.rows.items():
            available = self.results.get(tid, ScanResult(tid)).available
            row.var.set(tid in ids and available)

    def selected(self) -> list[CleanupTarget]:
        """Currently ticked targets."""
        return [r.target for r in self.rows.values() if r.var.get()]

    def update_total(self) -> None:
        """Show the total size of the selected targets."""
        total = sum(
            self.results[t.id].bytes
            for t in self.selected()
            if t.id in self.results and not self.results[t.id].estimated
        )
        self.total_label.configure(text=f"Selected: {human_size(total)}" if self.results else "")

    def _busy(self, busy: bool, message: str = "") -> None:
        state = "disabled" if busy else "normal"
        self.scan_btn.configure(state=state)
        self.clean_btn.configure(state=state)
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        if message:
            self.status.configure(text=message)

    def cancel_task(self) -> None:
        """Request cancellation of the running task."""
        if self.cancel:
            self.cancel.set()
            self.status.configure(text="Cancelling...")

    # -- scan -------------------------------------------------------------- #

    def start_scan(self) -> None:
        """Scan all targets in the background."""
        self._busy(True, "Scanning...")
        self.progress.set(0)
        targets = self.targets

        def work(report: Any, cancel: threading.Event) -> dict[str, ScanResult]:
            ctx = make_context(is_admin=self.is_admin, cancel=cancel)
            return scan_targets(
                targets, ctx, progress=lambda tid, done, total: report((tid, done, total))
            )

        def on_progress(payload: tuple[str, int, int]) -> None:
            tid, done, total = payload
            self.progress.set(done / total)
            self.status.configure(text=f"Scanned {done}/{total}: {tid}")

        self.cancel = self.runner.submit(
            work, on_done=self._scan_done, on_error=self._task_failed, on_progress=on_progress
        )

    def _scan_done(self, results: dict[str, ScanResult]) -> None:
        self.results = results
        for tid, row in self.rows.items():
            row.set_result(results.get(tid))
        self.apply_profile(self.profile_var.get().lower())
        measured = sum(r.bytes for r in results.values() if r.available and not r.estimated)
        self._busy(
            False,
            f"Scan complete: {human_size(measured)} reclaimable. "
            "Review the selection, then click 'Clean selected'.",
        )
        self.update_total()

    def _task_failed(self, exc: BaseException) -> None:
        self._busy(False, f"Error: {exc}")
        messagebox.showerror("DiskSweep", f"The operation failed:\n{exc}")

    # -- clean ------------------------------------------------------------- #

    def confirm_clean(self) -> None:
        """Show a confirmation dialog, then clean the selected targets."""
        targets = self.selected()
        if not targets:
            messagebox.showinfo("DiskSweep", "Select at least one target first.")
            return
        if not self.results:
            messagebox.showinfo(
                "DiskSweep", "Run a scan first so you can see what will be removed."
            )
            return
        total = sum(self.results[t.id].bytes for t in targets if t.id in self.results)
        lines = [f"- {t.name} [{t.risk.value}]" for t in targets]
        warnings = []
        if any(t.risk is Risk.ADVANCED for t in targets):
            warnings.append(
                "ADVANCED targets are selected. These make system changes that cannot be undone."
            )
        skipped_admin = [t.name for t in targets if t.requires_admin and not self.is_admin]
        if skipped_admin:
            warnings.append(
                f"{len(skipped_admin)} target(s) need administrator rights and "
                "will be skipped. Use 'Restart as admin' to include them."
            )
        msg = (
            f"Clean {len(targets)} target(s), about {human_size(total)}?\n\n"
            + "\n".join(lines)
            + "\n\nCache and temp files are deleted permanently. Every deleted path is "
            "written to the audit log." + ("\n\n" + "\n".join(warnings) if warnings else "")
        )
        icon = "warning" if warnings else "question"
        if messagebox.askyesno("Confirm cleanup", msg, icon=icon):
            self.start_clean(targets)

    def start_clean(self, targets: list[CleanupTarget]) -> None:
        """Clean in the background (only called after confirmation)."""
        self._busy(True, "Cleaning...")
        self.progress.set(0)

        def work(report: Any, cancel: threading.Event) -> CleanReport:
            with AuditLog() as audit:
                ctx = make_context(audit=audit, is_admin=self.is_admin, cancel=cancel)
                return clean_targets(
                    targets, ctx, apply=True, progress=lambda tid, d, t: report((tid, d, t))
                )

        def on_progress(payload: tuple[str, int, int]) -> None:
            tid, done, total = payload
            self.progress.set(done / total)
            self.status.configure(text=f"Cleaned {done}/{total}: {tid}")

        self.cancel = self.runner.submit(
            work, on_done=self._clean_done, on_error=self._task_failed, on_progress=on_progress
        )

    def _clean_done(self, report: CleanReport) -> None:
        self.refresh_drive()
        before = human_size(report.free_before) if report.free_before is not None else "?"
        after = human_size(report.free_after) if report.free_after is not None else "?"
        skipped = [r for r in report.results if not r.ran]
        msg = (
            f"Freed {human_size(report.total_bytes)} in {report.total_files:,} files.\n"
            f"Free space on {report.drive}: {before} -> {after}\n"
            f"{report.total_skipped} locked/protected items were skipped."
        )
        if skipped:
            msg += "\n\nNot run:\n" + "\n".join(f"- {r.target_id}: {r.note}" for r in skipped)
        self._busy(False, f"Done: freed {human_size(report.total_bytes)}.")
        messagebox.showinfo("Cleanup complete", msg)
        self.start_scan()
