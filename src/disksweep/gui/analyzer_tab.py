"""Analyzer tab: largest folders and files, with JSON/CSV export."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

import customtkinter as ctk

from disksweep import osutils
from disksweep.analyzer import AnalysisResult, analyze, export_csv, export_json
from disksweep.gui.workers import TaskRunner
from disksweep.units import human_size


def style_treeview(dark: bool) -> None:
    """Make ttk.Treeview match the CustomTkinter theme."""
    style = ttk.Style()
    style.theme_use("clam")
    bg, fg, sel, head = (
        ("#2b2b2b", "#dce4ee", "#1f6aa5", "#333333")
        if dark
        else ("#ffffff", "#1a1a1a", "#3a7ebf", "#e5e5e5")
    )
    style.configure(
        "Treeview", background=bg, fieldbackground=bg, foreground=fg, rowheight=24, borderwidth=0
    )
    style.map("Treeview", background=[("selected", sel)], foreground=[("selected", "white")])
    style.configure("Treeview.Heading", background=head, foreground=fg, relief="flat")


class AnalyzerTab(ctk.CTkFrame):
    """Find what is taking up space."""

    def __init__(self, master: Any, runner: TaskRunner) -> None:
        super().__init__(master, fg_color="transparent")
        self.runner = runner
        self.result: AnalysisResult | None = None
        self.cancel: threading.Event | None = None
        self._build()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        bar.grid_columnconfigure(0, weight=1)
        self.path_var = ctk.StringVar(value=osutils.system_drive())
        ctk.CTkEntry(bar, textvariable=self.path_var).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(bar, text="Browse...", width=90, command=self.browse).grid(
            row=0, column=1, padx=6
        )
        ctk.CTkLabel(bar, text="Top").grid(row=0, column=2, padx=(10, 4))
        self.top_var = ctk.StringVar(value="30")
        ctk.CTkEntry(bar, textvariable=self.top_var, width=50).grid(row=0, column=3)
        self.run_btn = ctk.CTkButton(bar, text="Analyze", width=100, command=self.start)
        self.run_btn.grid(row=0, column=4, padx=6)
        self.cancel_btn = ctk.CTkButton(
            bar, text="Cancel", width=80, state="disabled", fg_color="gray40", command=self.stop
        )
        self.cancel_btn.grid(row=0, column=5, padx=(0, 6))
        self.json_btn = ctk.CTkButton(
            bar,
            text="Export JSON",
            width=100,
            state="disabled",
            command=lambda: self.export("json"),
        )
        self.json_btn.grid(row=0, column=6, padx=6)
        self.csv_btn = ctk.CTkButton(
            bar, text="Export CSV", width=100, state="disabled", command=lambda: self.export("csv")
        )
        self.csv_btn.grid(row=0, column=7)

        self.dirs = self._tree("Largest folders", 1)
        self.files = self._tree("Largest files", 2)
        self.status = ctk.CTkLabel(
            self, text="Choose a folder or drive and click Analyze. This is read-only.", anchor="w"
        )
        self.status.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))

    def _tree(self, title: str, row: int) -> ttk.Treeview:
        frame = ctk.CTkFrame(self)
        frame.grid(row=row, column=0, sticky="nsew", padx=10, pady=4)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(4, 0)
        )
        tree = ttk.Treeview(frame, columns=("size", "path"), show="headings", height=8)
        tree.heading("size", text="Size")
        tree.heading("path", text="Path")
        tree.column("size", width=100, anchor="e", stretch=False)
        tree.column("path", width=600, anchor="w")
        tree.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=8)
        scroll = ctk.CTkScrollbar(frame, command=tree.yview)
        scroll.grid(row=1, column=1, sticky="ns", pady=8)
        tree.configure(yscrollcommand=scroll.set)
        tree.bind("<Double-1>", lambda _e, t=tree: self._open_selected(t))
        return tree

    def _open_selected(self, tree: ttk.Treeview) -> None:
        sel = tree.selection()
        if not sel or not osutils.IS_WINDOWS:
            return
        path = tree.item(sel[0], "values")[1]
        target = path if os.path.isdir(path) else os.path.dirname(path)
        os.startfile(target)  # type: ignore[attr-defined]  # opens Explorer, read-only

    def browse(self) -> None:
        """Pick a folder."""
        path = filedialog.askdirectory(initialdir=self.path_var.get() or None)
        if path:
            self.path_var.set(os.path.normpath(path))

    def start(self) -> None:
        """Run the analysis in the background."""
        path = self.path_var.get().strip()
        try:
            top = max(1, int(self.top_var.get()))
        except ValueError:
            top = 30
        if not os.path.isdir(path):
            messagebox.showerror("DiskSweep", f"Not a folder:\n{path}")
            return
        self.run_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.status.configure(text=f"Analyzing {path}...")

        def work(report: Any, cancel: threading.Event) -> AnalysisResult:
            return analyze(path, top=top, cancel=cancel, progress=lambda d, f, b: report((d, f, b)))

        def on_progress(payload: tuple[int, int, int]) -> None:
            d, f, b = payload
            self.status.configure(text=f"Scanned {d:,} folders, {f:,} files, {human_size(b)}...")

        self.cancel = self.runner.submit(
            work, on_done=self._done, on_error=self._failed, on_progress=on_progress
        )

    def stop(self) -> None:
        """Cancel the running analysis."""
        if self.cancel:
            self.cancel.set()

    def _fill(self, tree: ttk.Treeview, entries: list[Any]) -> None:
        tree.delete(*tree.get_children())
        for e in entries:
            tree.insert("", tk.END, values=(human_size(e.bytes), e.path))

    def _done(self, result: AnalysisResult) -> None:
        self.result = result
        self._fill(self.dirs, result.top_dirs)
        self._fill(self.files, result.top_files)
        self.run_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.json_btn.configure(state="normal")
        self.csv_btn.configure(state="normal")
        partial = " (cancelled - partial)" if self.cancel and self.cancel.is_set() else ""
        self.status.configure(
            text=f"{result.root}: {human_size(result.total_bytes)} in {result.file_count:,} "
            f"files ({result.elapsed:.1f}s){partial}. "
            f"{len(result.errors)} folders unreadable. Double-click to open in Explorer."
        )

    def _failed(self, exc: BaseException) -> None:
        self.run_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.status.configure(text=f"Error: {exc}")

    def export(self, kind: str) -> None:
        """Save results as JSON or CSV."""
        if not self.result:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=f".{kind}",
            filetypes=[(kind.upper(), f"*.{kind}")],
            initialfile=f"disksweep-analysis.{kind}",
        )
        if not path:
            return
        (export_json if kind == "json" else export_csv)(self.result, path)
        self.status.configure(text=f"Exported to {path}")
