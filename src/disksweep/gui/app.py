"""Main application window."""

from __future__ import annotations

import os
import sys
from tkinter import messagebox

import customtkinter as ctk

from disksweep import osutils
from disksweep.audit import log_dir, tail
from disksweep.gui.analyzer_tab import AnalyzerTab, style_treeview
from disksweep.gui.dashboard import Dashboard
from disksweep.gui.workers import TaskRunner
from disksweep.logging_setup import configure_logging
from disksweep.version import __version__


class LogTab(ctk.CTkFrame):
    """Shows the tail of the audit log."""

    def __init__(self, master: ctk.CTkBaseClass) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        ctk.CTkButton(bar, text="Refresh", width=100, command=self.refresh).pack(side="left")
        ctk.CTkButton(bar, text="Open log folder", width=140, command=self.open_folder).pack(
            side="left", padx=8
        )
        ctk.CTkLabel(bar, text=str(log_dir()), text_color="gray55").pack(side="left", padx=8)
        self.text = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas", size=12), wrap="none")
        self.text.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.refresh()

    def refresh(self) -> None:
        """Reload the last 300 audit entries."""
        lines = tail(300)
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", "\n".join(lines) if lines else "No deletions recorded yet.")
        self.text.configure(state="disabled")

    def open_folder(self) -> None:
        """Open the audit log folder in Explorer."""
        folder = log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        if osutils.IS_WINDOWS:
            os.startfile(folder)  # type: ignore[attr-defined]


class DiskSweepApp(ctk.CTk):
    """Top-level window: header + Dashboard / Analyzer / Audit log tabs."""

    def __init__(self) -> None:
        super().__init__()
        self.title(f"DiskSweep {__version__}")
        self.geometry("1040x760")
        self.minsize(860, 600)
        self.runner = TaskRunner(self)
        self.is_admin = osutils.is_admin()

        header = ctk.CTkFrame(self, corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(header, text="DiskSweep", font=ctk.CTkFont(size=22, weight="bold")).pack(
            side="left", padx=16, pady=10
        )
        ctk.CTkLabel(
            header,
            text="Dry-run by default. Protected folders are never touched.",
            text_color="gray55",
        ).pack(side="left")
        self.theme_var = ctk.StringVar(value="System")
        ctk.CTkOptionMenu(
            header,
            values=["System", "Dark", "Light"],
            variable=self.theme_var,
            width=100,
            command=self.set_theme,
        ).pack(side="right", padx=16)
        ctk.CTkLabel(header, text="Theme").pack(side="right")
        if osutils.IS_WINDOWS and not self.is_admin:
            ctk.CTkButton(
                header, text="Restart as admin", width=140, command=self.restart_as_admin
            ).pack(side="right", padx=16)
        else:
            ctk.CTkLabel(
                header, text="Administrator" if self.is_admin else "", text_color="#f57c00"
            ).pack(side="right", padx=16)

        tabs = ctk.CTkTabview(self)
        tabs.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        for name in ("Dashboard", "Analyzer", "Audit log"):
            tabs.add(name)
            tabs.tab(name).grid_columnconfigure(0, weight=1)
            tabs.tab(name).grid_rowconfigure(0, weight=1)
        Dashboard(tabs.tab("Dashboard"), self.runner).grid(row=0, column=0, sticky="nsew")
        AnalyzerTab(tabs.tab("Analyzer"), self.runner).grid(row=0, column=0, sticky="nsew")
        self.log_tab = LogTab(tabs.tab("Audit log"))
        self.log_tab.grid(row=0, column=0, sticky="nsew")
        tabs.configure(
            command=lambda: self.log_tab.refresh() if tabs.get() == "Audit log" else None
        )
        self.set_theme("System")

    def set_theme(self, value: str) -> None:
        """Switch between system, dark and light appearance."""
        ctk.set_appearance_mode(value.lower())
        style_treeview(ctk.get_appearance_mode() == "Dark")

    def restart_as_admin(self) -> None:
        """Relaunch the GUI elevated (UAC prompt) and close this window."""
        try:
            started = osutils.relaunch_elevated(["gui"])
        except OSError as exc:
            messagebox.showerror("DiskSweep", str(exc))
            return
        if started:
            self.destroy()


def main() -> None:
    """GUI entry point."""
    configure_logging(1 if "-v" in sys.argv else 0)
    ctk.set_default_color_theme("blue")
    app = DiskSweepApp()
    app.mainloop()


if __name__ == "__main__":
    main()
