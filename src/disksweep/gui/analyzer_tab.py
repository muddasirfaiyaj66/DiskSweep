"""Analyzer page: charts and tables of what uses space, with drill-down and export."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any

import customtkinter as ctk

from disksweep import osutils
from disksweep.analyzer import AnalysisResult, analyze, export_csv, export_json
from disksweep.gui import theme as T
from disksweep.gui.charts import Bar, BarChart
from disksweep.gui.widgets import Card, ModalDialog, button, label
from disksweep.gui.workers import TaskRunner
from disksweep.units import human_size


def style_treeview(dark: bool) -> None:
    """Make ttk.Treeview match the card surfaces of the current theme."""
    style = ttk.Style()
    style.theme_use("clam")
    i = 1 if dark else 0
    family = T.font(12).cget("family")
    style.configure(
        "Treeview",
        background=T.CARD[i],
        fieldbackground=T.CARD[i],
        foreground=T.TEXT[i],
        rowheight=30,
        borderwidth=0,
        font=(family, 10),
    )
    style.map(
        "Treeview", background=[("selected", T.ACCENT[i])], foreground=[("selected", "#ffffff")]
    )
    style.configure(
        "Treeview.Heading",
        background=T.CARD_ALT[i],
        foreground=T.TEXT_2[i],
        relief="flat",
        borderwidth=0,
        padding=(8, 6),
        font=(family, 10, "bold"),
    )
    style.map("Treeview.Heading", background=[("active", T.HOVER[i])])
    # Drop the clam theme's bright focus border around the table.
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])


class AnalyzerTab(ctk.CTkFrame):
    """Find what is taking up space."""

    def __init__(self, master: Any, runner: TaskRunner) -> None:
        super().__init__(master, fg_color="transparent")
        self.runner = runner
        self.result: AnalysisResult | None = None
        self.cancel: threading.Event | None = None
        self._build()

    def _entry(self, master: Any, var: ctk.StringVar, **kw: Any) -> ctk.CTkEntry:
        return ctk.CTkEntry(
            master,
            textvariable=var,
            height=36,
            corner_radius=8,
            border_width=1,
            border_color=T.BORDER,
            fg_color=T.CARD_ALT,
            text_color=T.TEXT,
            font=T.font(13),
            **kw,
        )

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, T.S3))
        label(head, "Analyzer", size=26, weight="bold").pack(anchor="w")
        label(
            head,
            "See which folders and file types use the most space. Read-only.",
            size=13,
            color=T.TEXT_2,
        ).pack(anchor="w")

        bar = Card(self)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, T.S3))
        bar.grid_columnconfigure(0, weight=1)
        self.path_var = ctk.StringVar(value=osutils.system_drive())
        self._entry(bar, self.path_var).grid(
            row=0, column=0, sticky="ew", padx=(T.S3, T.S2), pady=T.S3
        )
        button(bar, "Browse", self.browse, kind="neutral", width=90).grid(row=0, column=1)
        label(bar, "Top", size=12, color=T.TEXT_2).grid(row=0, column=2, padx=(T.S3, T.S1))
        self.top_var = ctk.StringVar(value="30")
        self._entry(bar, self.top_var, width=54, justify="center").grid(row=0, column=3)
        self.run_btn = button(bar, "Analyze", self.start, width=104)
        self.run_btn.grid(row=0, column=4, padx=(T.S3, T.S2))
        self.cancel_btn = button(
            bar, "Cancel", self.stop, kind="neutral", width=86, state="disabled"
        )
        self.cancel_btn.grid(row=0, column=5, padx=(0, T.S3))

        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=2, column=0, sticky="ew", pady=(0, T.S3))
        nav.grid_columnconfigure(1, weight=1)
        self.up_btn = button(
            nav, "\u2191  Up", self.go_up, kind="neutral", width=76, state="disabled"
        )
        self.up_btn.grid(row=0, column=0)
        self.where = label(nav, "No folder analyzed yet", size=14, weight="bold")
        self.where.grid(row=0, column=1, sticky="ew", padx=T.S3)
        self.json_btn = button(
            nav,
            "Export JSON",
            lambda: self.export("json"),
            kind="neutral",
            width=108,
            state="disabled",
        )
        self.json_btn.grid(row=0, column=2, padx=(0, T.S2))
        self.csv_btn = button(
            nav,
            "Export CSV",
            lambda: self.export("csv"),
            kind="neutral",
            width=100,
            state="disabled",
        )
        self.csv_btn.grid(row=0, column=3, padx=(0, T.S3))
        self.view_var = ctk.StringVar(value="Charts")
        ctk.CTkSegmentedButton(
            nav,
            values=["Charts", "Tables"],
            variable=self.view_var,
            command=self.show_view,
            height=34,
            font=T.font(12, "bold"),
            selected_color=T.ACCENT_BTN,
            selected_hover_color=T.ACCENT_HOVER,
            unselected_color=T.CARD_ALT,
            unselected_hover_color=T.HOVER,
            fg_color=T.CARD_ALT,
            text_color=("#0b0b0b", "#f0efec"),
        ).grid(row=0, column=4)

        # Charts view
        self.charts = ctk.CTkFrame(self, fg_color="transparent")
        self.charts.grid_columnconfigure((0, 1), weight=1, uniform="charts")
        self.charts.grid_rowconfigure(0, weight=1)
        self.folder_chart = self._chart_card(
            0,
            "Largest subfolders",
            "Click a bar to look inside that folder",
            on_click=self.drill_down,
            empty="Run an analysis to see where the space goes.",
        )
        self.type_chart = self._chart_card(
            1,
            "Space by file type",
            "Top 8 extensions; the rest is grouped as Other",
            on_click=None,
            empty="File types appear here after an analysis.",
        )

        # Tables view (also the accessible, exact-numbers view of the charts)
        self.tables = ctk.CTkFrame(self, fg_color="transparent")
        self.tables.grid_columnconfigure((0, 1), weight=1, uniform="tables")
        self.tables.grid_rowconfigure(0, weight=1)
        self.dirs = self._tree(self.tables, "Largest folders", 0)
        self.files = self._tree(self.tables, "Largest files", 1)
        self.show_view("Charts")

        self.status = label(
            self, "Choose a folder or drive and select Analyze.", size=12, color=T.TEXT_2
        )
        self.status.grid(row=4, column=0, sticky="ew", pady=(T.S2, 0))

    def _chart_card(
        self, column: int, title: str, subtitle: str, *, on_click: Any, empty: str
    ) -> BarChart:
        card = Card(self.charts)
        card.grid(row=0, column=column, sticky="nsew", padx=(0, T.S2) if column == 0 else (T.S2, 0))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(2, weight=1)
        label(card, title, size=15, weight="bold").grid(
            row=0, column=0, sticky="ew", padx=T.S3, pady=(T.S3, 0)
        )
        label(card, subtitle, size=12, color=T.TEXT_3).grid(row=1, column=0, sticky="ew", padx=T.S3)
        chart = BarChart(card, on_click=on_click, empty_text=empty)
        chart.grid(row=2, column=0, sticky="nsew", padx=T.S2, pady=(T.S2, T.S3))
        return chart

    def show_view(self, name: str) -> None:
        """Switch between the charts and the tables."""
        (self.charts if name == "Charts" else self.tables).grid(row=3, column=0, sticky="nsew")
        (self.tables if name == "Charts" else self.charts).grid_remove()

    def refresh_theme(self) -> None:
        """Redraw charts after a light/dark switch."""
        self.folder_chart.schedule_redraw()
        self.type_chart.schedule_redraw()

    def drill_down(self, path: str) -> None:
        """Analyze a subfolder (from a chart click)."""
        if self.run_btn.cget("state") == "disabled":
            return  # an analysis is already running
        self.path_var.set(path)
        self.start()

    def go_up_to(self, path: str) -> None:
        """Analyze a specific folder (used by tooling and future breadcrumbs)."""
        self.drill_down(path)

    def go_up(self) -> None:
        """Analyze the parent of the current folder."""
        if self.result:
            parent = os.path.dirname(self.result.root)
            if parent and parent != self.result.root and os.path.isdir(parent):
                self.drill_down(parent)

    def _update_charts(self, result: AnalysisResult) -> None:
        total = result.total_bytes or 1
        name = os.path.basename(result.root.rstrip(r"\/")) or result.root
        folder_bars = [
            Bar(
                os.path.basename(e.path) or e.path,
                e.bytes,
                f"{human_size(e.bytes)}  ·  {e.bytes / total:.1%} of {name}  ·  click to open",
                key=e.path,
            )
            for e in result.children[:12]
        ]
        if result.root_files_bytes:
            folder_bars.append(
                Bar(
                    "(files in this folder)",
                    result.root_files_bytes,
                    f"{human_size(result.root_files_bytes)}  ·  "
                    f"{result.root_files_bytes / total:.1%} of {name}",
                )
            )
            folder_bars.sort(key=lambda b: b.value, reverse=True)
        type_bars = [
            Bar(
                t.ext,
                t.bytes,
                f"{human_size(t.bytes)}  ·  {t.bytes / total:.1%}  ·  {t.files:,} files",
            )
            for t in result.by_type
        ]
        self.folder_chart.set_data(folder_bars)
        self.type_chart.set_data(type_bars)

    def _tree(self, parent: ctk.CTkFrame, title: str, column: int) -> ttk.Treeview:
        card = Card(parent)
        card.grid(row=0, column=column, sticky="nsew", padx=(0, T.S2) if column == 0 else (T.S2, 0))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        label(card, title, size=15, weight="bold").grid(
            row=0, column=0, sticky="ew", padx=T.S3, pady=(T.S3, T.S2)
        )
        tree = ttk.Treeview(card, columns=("size", "path"), show="headings")
        tree.heading("size", text="Size", anchor="e")
        tree.heading("path", text="Path", anchor="w")
        tree.column("size", width=90, anchor="e", stretch=False)
        tree.column("path", width=360, anchor="w")
        tree.grid(row=1, column=0, sticky="nsew", padx=(T.S3, 0), pady=(0, T.S3))
        scroll = ctk.CTkScrollbar(card, command=tree.yview, button_color=T.BORDER)
        scroll.grid(row=1, column=1, sticky="ns", pady=(0, T.S3), padx=(0, T.S1))
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
            dlg = ModalDialog(
                self, "DiskSweep", "That folder doesn't exist", icon="!", icon_color=T.DANGER
            )
            label(dlg.body, path, size=12, color=T.TEXT_2, wraplength=420).grid(sticky="ew")
            dlg.add_buttons([("OK", "primary")], default="OK")
            dlg.show()
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
        self._update_charts(result)
        folder = os.path.basename(os.path.normpath(result.root)) or result.root
        self.where.configure(text=f"{folder}  ·  {human_size(result.total_bytes)}")
        at_top = os.path.dirname(result.root) in ("", result.root)
        self.up_btn.configure(state="disabled" if at_top else "normal")
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
