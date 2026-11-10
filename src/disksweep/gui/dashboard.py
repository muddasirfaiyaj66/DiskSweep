"""Dashboard page: drive usage, scan, grouped target checklist and cleanup."""

from __future__ import annotations

import threading
from typing import Any

import customtkinter as ctk

from disksweep import osutils
from disksweep.audit import AuditLog
from disksweep.engine import clean_targets, make_context, scan_targets
from disksweep.gui import theme as T
from disksweep.gui.widgets import (
    Card,
    Chip,
    ModalDialog,
    RiskBadge,
    SizeBar,
    StatCard,
    UsageRing,
    button,
    label,
)
from disksweep.gui.workers import TaskRunner
from disksweep.models import CleanReport, Risk, ScanResult
from disksweep.profiles import PROFILES
from disksweep.targets.base import CleanupTarget
from disksweep.targets.registry import all_targets
from disksweep.units import human_size

GROUPS = (
    ("system", "System"),
    ("browser", "Browsers"),
    ("developer", "Developer tools"),
    ("advanced", "Advanced"),
)


class TargetRow:
    """One target in the checklist: checkbox, name/description, badges, size."""

    def __init__(
        self, parent: ctk.CTkFrame, target: CleanupTarget, row: int, is_admin: bool
    ) -> None:
        self.target = target
        self.var = ctk.BooleanVar(value=False)
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.frame.grid(row=row, column=0, sticky="ew", padx=T.S2)
        self.frame.grid_columnconfigure(1, weight=1)

        self.check = ctk.CTkCheckBox(
            self.frame,
            text="",
            variable=self.var,
            width=24,
            checkbox_width=20,
            checkbox_height=20,
            corner_radius=6,
            fg_color=T.ACCENT,
            hover_color=T.ACCENT_HOVER,
            border_color=T.TEXT_3,
        )
        self.check.grid(row=0, column=0, rowspan=2, padx=(T.S2, T.S3), pady=T.S3)

        name = label(self.frame, target.name, size=13, weight="bold")
        name.grid(row=0, column=1, sticky="sw", pady=(T.S2, 0))
        desc = label(
            self.frame, target.description, size=11, color=T.TEXT_3, wraplength=520, justify="left"
        )
        desc.grid(row=1, column=1, sticky="nw", pady=(0, T.S2))
        for w in (name, desc):
            w.bind("<Button-1>", lambda _e: self.toggle())

        chips = ctk.CTkFrame(self.frame, fg_color="transparent")
        chips.grid(row=0, column=2, rowspan=2, padx=T.S2)
        if target.requires_admin and not is_admin:
            Chip(chips, "Admin").pack(side="left", padx=(0, T.S1))
        RiskBadge(chips, target.risk.value).pack(side="left")

        sizes = ctk.CTkFrame(self.frame, fg_color="transparent", width=120)
        sizes.grid(row=0, column=3, rowspan=2, padx=(T.S2, T.S3), sticky="e")
        self.size = label(sizes, "—", size=13, weight="bold", anchor="e", width=120)
        self.size.pack(anchor="e")
        self.bar = SizeBar(sizes, width=120)
        self.bar.pack(anchor="e", pady=(T.S1, 0))

    def toggle(self) -> None:
        """Click on the text toggles the checkbox (if enabled)."""
        if self.check.cget("state") != "disabled":
            self.var.set(not self.var.get())

    def set_result(self, result: ScanResult | None, largest: int) -> None:
        """Show size (or availability) and the relative size bar."""
        if result is None:
            self.size.configure(text="—", text_color=T.TEXT)
            self.bar.set(0)
            return
        if not result.available:
            self.size.configure(text="Not found", text_color=T.TEXT_3)
            self.check.configure(state="disabled")
            self.var.set(False)
            self.bar.set(0)
            return
        self.check.configure(state="normal")
        if result.estimated:
            text = f"up to {human_size(result.bytes)}" if result.bytes else "Size on run"
            self.size.configure(text=text, text_color=T.TEXT_2)
            self.bar.set(0)
        else:
            self.size.configure(text=human_size(result.bytes), text_color=T.TEXT)
            self.bar.set(result.bytes / largest if largest else 0)

    def refresh_theme(self) -> None:
        """Redraw canvas parts."""
        self.bar.redraw()


class Dashboard(ctk.CTkFrame):
    """Main page."""

    def __init__(self, master: Any, runner: TaskRunner) -> None:
        super().__init__(master, fg_color="transparent")
        self.runner = runner
        self.is_admin = osutils.is_admin()
        self.targets = all_targets()
        self.results: dict[str, ScanResult] = {}
        self.cancel: threading.Event | None = None
        self.active_dialog: ModalDialog | None = None
        self._build()
        self.refresh_drive()
        self.apply_profile("safe")

    # -- layout ---------------------------------------------------------- #

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, T.S3))
        label(head, "Dashboard", size=26, weight="bold").pack(anchor="w")
        label(
            head,
            "Scan to see what can be freed. Nothing is deleted until you confirm.",
            size=13,
            color=T.TEXT_2,
        ).pack(anchor="w")

        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", pady=(0, T.S3))
        stats.grid_columnconfigure((0, 1, 2), weight=1, uniform="stats")
        self._drive_card(stats).grid(row=0, column=0, sticky="nsew", padx=(0, T.S2))
        self.reclaim_card = StatCard(stats, "Reclaimable")
        self.reclaim_card.grid(row=0, column=1, sticky="nsew", padx=T.S2)
        self.reclaim_card.set("—", "Run a scan to measure")
        self.selected_card = StatCard(stats, "Selected")
        self.selected_card.grid(row=0, column=2, sticky="nsew", padx=(T.S2, 0))
        self.selected_card.set("—", "Pick a profile or targets")

        bar = Card(self)
        bar.grid(row=2, column=0, sticky="ew", pady=(0, T.S3))
        label(bar, "Profile", size=12, weight="bold", color=T.TEXT_2).pack(
            side="left", padx=(T.S3, T.S2), pady=T.S3
        )
        self.profile_var = ctk.StringVar(value="Safe")
        ctk.CTkSegmentedButton(
            bar,
            values=["Safe", "Developer", "Aggressive"],
            variable=self.profile_var,
            command=lambda v: self.apply_profile(v.lower()),
            height=34,
            font=T.font(12, "bold"),
            selected_color=T.ACCENT_BTN,
            selected_hover_color=T.ACCENT_HOVER,
            unselected_color=T.CARD_ALT,
            unselected_hover_color=T.HOVER,
            fg_color=T.CARD_ALT,
            text_color=(("#0b0b0b"), "#f0efec"),
        ).pack(side="left", pady=T.S3)
        self.cancel_btn = button(
            bar, "Cancel", self.cancel_task, kind="neutral", width=96, state="disabled"
        )
        self.cancel_btn.pack(side="right", padx=(T.S2, T.S3), pady=T.S3)
        self.clean_btn = button(bar, "Clean selected", self.confirm_clean, kind="danger", width=140)
        self.clean_btn.pack(side="right", padx=T.S2, pady=T.S3)
        self.scan_btn = button(bar, "Scan", self.start_scan, width=110)
        self.scan_btn.pack(side="right", padx=T.S2, pady=T.S3)

        card = Card(self)
        card.grid(row=3, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=T.S3, pady=(T.S3, T.S1))
        label(top, "Cleanup targets", size=15, weight="bold").pack(side="left")
        self.count_label = label(top, "", size=12, color=T.TEXT_3)
        self.count_label.pack(side="right")
        self.list_frame = ctk.CTkScrollableFrame(
            card, fg_color="transparent", scrollbar_button_color=T.BORDER
        )
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=T.S1, pady=(0, T.S2))
        self.list_frame.grid_columnconfigure(0, weight=1)
        self.rows: dict[str, TargetRow] = {}
        r = 0
        for key, title in GROUPS:
            members = [t for t in self.targets if t.category == key]
            if not members:
                continue
            label(self.list_frame, title.upper(), size=11, weight="bold", color=T.TEXT_3).grid(
                row=r, column=0, sticky="ew", padx=T.S3, pady=(T.S3 if r else T.S1, T.S1)
            )
            r += 1
            for i, t in enumerate(members):
                if i:
                    ctk.CTkFrame(self.list_frame, height=1, fg_color=T.BORDER).grid(
                        row=r, column=0, sticky="ew", padx=(56, T.S3)
                    )
                    r += 1
                self.rows[t.id] = TargetRow(self.list_frame, t, r, self.is_admin)
                r += 1
        for row in self.rows.values():
            row.var.trace_add("write", lambda *_: self.update_total())

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", pady=(T.S3, 0))
        foot.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(
            foot, height=6, corner_radius=3, progress_color=T.ACCENT, fg_color=T.CARD_ALT
        )
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.status = label(
            foot, "Ready. Nothing is deleted until you confirm.", size=12, color=T.TEXT_2
        )
        self.status.grid(row=1, column=0, sticky="ew", pady=(T.S1, 0))

    def _drive_card(self, master: Any) -> Card:
        card = Card(master)
        card.grid_columnconfigure(1, weight=1)
        self.ring = UsageRing(card, size=92)
        self.ring.grid(row=0, column=0, rowspan=3, padx=T.S3, pady=T.S3)
        label(card, "DRIVE", size=11, weight="bold", color=T.TEXT_3).grid(
            row=0, column=1, sticky="sw", pady=(T.S3, 0)
        )
        drives = osutils.list_drives() or [osutils.system_drive()]
        sysdrive = osutils.system_drive()
        self.drive_var = ctk.StringVar(value=sysdrive if sysdrive in drives else drives[0])
        ctk.CTkOptionMenu(
            card,
            values=drives,
            variable=self.drive_var,
            width=84,
            height=28,
            fg_color=T.CARD_ALT,
            button_color=T.CARD_ALT,
            text_color=T.TEXT,
            button_hover_color=T.HOVER,
            font=T.font(13, "bold"),
            dropdown_font=T.font(12),
            command=lambda _v: self.refresh_drive(),
        ).grid(row=1, column=1, sticky="w", pady=T.S1)
        self.usage_label = label(card, "", size=12, color=T.TEXT_2)
        self.usage_label.grid(row=2, column=1, sticky="nw", pady=(0, T.S3), padx=(0, T.S3))
        return card

    # -- state helpers ----------------------------------------------------- #

    def refresh_drive(self) -> None:
        """Update the usage ring."""
        try:
            total, used, free = osutils.disk_usage(self.drive_var.get())
        except OSError:
            self.usage_label.configure(text="Unavailable")
            return
        self.ring.set(used / total if total else 0)
        self.usage_label.configure(text=f"{human_size(free)} free of {human_size(total)}")

    def refresh_theme(self) -> None:
        """Redraw canvas-based widgets after a theme change."""
        self.ring.redraw()
        for row in self.rows.values():
            row.refresh_theme()

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
        """Refresh the Selected card."""
        chosen = self.selected()
        self.count_label.configure(text=f"{len(chosen)} of {len(self.rows)} selected")
        if not self.results:
            self.selected_card.set(f"{len(chosen)}", "targets selected · scan to size them")
            return
        total = sum(
            self.results[t.id].bytes
            for t in chosen
            if t.id in self.results and not self.results[t.id].estimated
        )
        self.selected_card.set(human_size(total), f"{len(chosen)} targets selected")

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
            self.status.configure(text="Cancelling…")

    # -- scan -------------------------------------------------------------- #

    def start_scan(self) -> None:
        """Scan all targets in the background."""
        self._busy(True, "Scanning…")
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
            name = self.rows[tid].target.name if tid in self.rows else tid
            self.status.configure(text=f"Scanning {done} of {total} · {name}")

        self.cancel = self.runner.submit(
            work, on_done=self._scan_done, on_error=self._task_failed, on_progress=on_progress
        )

    def _scan_done(self, results: dict[str, ScanResult]) -> None:
        self.results = results
        measured = [r.bytes for r in results.values() if r.available and not r.estimated]
        largest = max(measured, default=0)
        for tid, row in self.rows.items():
            row.set_result(results.get(tid), largest)
        self.apply_profile(self.profile_var.get().lower())
        total = sum(measured)
        found = sum(1 for r in results.values() if r.available)
        self.reclaim_card.set(human_size(total), f"across {found} targets found on this PC")
        self._busy(
            False,
            f"Scan complete · {human_size(total)} reclaimable. Review the "
            "selection, then choose Clean selected.",
        )
        self.update_total()

    def _task_failed(self, exc: BaseException) -> None:
        self._busy(False, f"Error: {exc}")
        dlg = ModalDialog(self, "DiskSweep", "Something went wrong", icon="!", icon_color=T.DANGER)
        label(dlg.body, str(exc), size=12, color=T.TEXT_2, wraplength=400).grid(sticky="ew")
        dlg.add_buttons([("OK", "primary")], default="OK")
        self._show(dlg)

    def _show(self, dlg: ModalDialog) -> str | None:
        self.active_dialog = dlg
        try:
            return dlg.show()
        finally:
            self.active_dialog = None

    def _info(self, heading: str, text: str) -> None:
        dlg = ModalDialog(self, "DiskSweep", heading)
        label(dlg.body, text, size=12, color=T.TEXT_2, wraplength=400).grid(sticky="ew")
        dlg.add_buttons([("OK", "primary")], default="OK")
        self._show(dlg)

    # -- clean ------------------------------------------------------------- #

    def confirm_clean(self) -> None:
        """Show the confirmation dialog, then clean the selected targets."""
        targets = self.selected()
        if not targets:
            self._info("Nothing selected", "Select at least one target first.")
            return
        if not self.results:
            self._info("Scan first", "Run a scan so you can see what will be removed.")
            return
        total = sum(
            self.results[t.id].bytes
            for t in targets
            if t.id in self.results and not self.results[t.id].estimated
        )
        advanced = any(t.risk is Risk.ADVANCED for t in targets)
        dlg = ModalDialog(
            self,
            "Confirm cleanup",
            f"Clean {len(targets)} targets and free about {human_size(total)}?",
            icon="!" if advanced else "?",
            icon_color=T.DANGER if advanced else T.ACCENT,
            width=560,
        )
        lst = ctk.CTkScrollableFrame(dlg.body, fg_color=T.CARD_ALT, height=200, corner_radius=8)
        lst.grid(row=0, column=0, sticky="ew", pady=(0, T.S2))
        lst.grid_columnconfigure(0, weight=1)
        for i, t in enumerate(targets):
            r = self.results.get(t.id)
            size = "" if r is None or r.estimated else human_size(r.bytes)
            label(lst, t.name, size=12).grid(row=i, column=0, sticky="ew", padx=T.S2, pady=2)
            RiskBadge(lst, t.risk.value).grid(row=i, column=1, padx=T.S2)
            label(lst, size, size=12, weight="bold", anchor="e", width=80).grid(
                row=i, column=2, padx=(0, T.S2)
            )
        notes = [
            "Cache and temp files are deleted permanently. Every deleted path is "
            "written to the audit log."
        ]
        if advanced:
            notes.append("Advanced targets make system changes that cannot be undone.")
        need_admin = [t for t in targets if t.requires_admin and not self.is_admin]
        if need_admin:
            notes.append(
                f"{len(need_admin)} target(s) need administrator rights and will be "
                "skipped. Use Restart as admin to include them."
            )
        for j, note in enumerate(notes, start=1):
            label(
                dlg.body, "•  " + note, size=12, color=T.TEXT_2, wraplength=440, justify="left"
            ).grid(row=j, column=0, sticky="ew", pady=1)
        dlg.add_buttons([("Cancel", "neutral"), ("Clean now", "danger")], default="Cancel")
        if self._show(dlg) == "Clean now":
            self.start_clean(targets)

    def start_clean(self, targets: list[CleanupTarget]) -> None:
        """Clean in the background (only called after confirmation)."""
        self._busy(True, "Cleaning…")
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
            name = self.rows[tid].target.name if tid in self.rows else tid
            self.status.configure(text=f"Cleaned {done} of {total} · {name}")

        self.cancel = self.runner.submit(
            work, on_done=self._clean_done, on_error=self._task_failed, on_progress=on_progress
        )

    def _clean_done(self, report: CleanReport) -> None:
        self.refresh_drive()
        before = human_size(report.free_before) if report.free_before is not None else "?"
        after = human_size(report.free_after) if report.free_after is not None else "?"
        self._busy(False, f"Done · freed {human_size(report.total_bytes)}.")
        dlg = ModalDialog(
            self,
            "Cleanup complete",
            f"Freed {human_size(report.total_bytes)}",
            icon="✓",
            icon_color=(("#13651a", "#2e9e3e")),
        )
        facts = [
            ("Files removed", f"{report.total_files:,}"),
            ("Free space", f"{before}  →  {after}"),
            ("Items skipped", f"{report.total_skipped:,} (locked or protected)"),
        ]
        for i, (k, v) in enumerate(facts):
            label(dlg.body, k, size=12, color=T.TEXT_3).grid(row=i, column=0, sticky="w")
            label(dlg.body, v, size=12, weight="bold").grid(
                row=i, column=1, sticky="w", padx=(T.S3, 0)
            )
        skipped = [r for r in report.results if not r.ran]
        if skipped:
            label(dlg.body, "Not run", size=12, weight="bold").grid(
                row=len(facts), column=0, columnspan=2, sticky="w", pady=(T.S2, 0)
            )
            for j, r in enumerate(skipped, start=len(facts) + 1):
                name = (
                    self.rows[r.target_id].target.name if r.target_id in self.rows else r.target_id
                )
                label(
                    dlg.body,
                    f"•  {name}: {r.note}",
                    size=12,
                    color=T.TEXT_2,
                    wraplength=420,
                    justify="left",
                ).grid(row=j, column=0, columnspan=2, sticky="w")
        dlg.add_buttons([("Done", "primary")], default="Done")
        self._show(dlg)
        self.start_scan()
