"""Main application window: sidebar navigation + pages."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from tkinter import ttk

import customtkinter as ctk

from disksweep import osutils
from disksweep.audit import log_dir, tail
from disksweep.gui import theme as T
from disksweep.gui.analyzer_tab import AnalyzerTab, style_treeview
from disksweep.gui.dashboard import Dashboard
from disksweep.gui.widgets import Card, ModalDialog, button, label
from disksweep.gui.workers import TaskRunner
from disksweep.logging_setup import configure_logging
from disksweep.units import human_size
from disksweep.version import __version__

# Segoe Fluent Icons (Windows 11) glyphs for the navigation.
ICON_FONT = "Segoe Fluent Icons" if sys.platform == "win32" else None
ICONS = {"Dashboard": "", "Analyzer": "", "Audit log": "", "shield": ""}


class LogPage(ctk.CTkFrame):
    """Audit log as a table of the latest entries."""

    def __init__(self, master: ctk.CTkBaseClass) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, T.S3))
        head.grid_columnconfigure(0, weight=1)
        label(head, "Audit log", size=26, weight="bold").grid(row=0, column=0, sticky="w")
        label(
            head,
            "Every file DiskSweep deleted, with its size. Newest first.",
            size=13,
            color=T.TEXT_2,
        ).grid(row=1, column=0, sticky="w")
        button(head, "Open folder", self.open_folder, kind="neutral", width=120).grid(
            row=0, column=1, rowspan=2, padx=(T.S2, 0)
        )
        button(head, "Refresh", self.refresh, width=100).grid(
            row=0, column=2, rowspan=2, padx=(T.S2, 0)
        )

        card = Card(self)
        card.grid(row=1, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(0, weight=1)
        cols = ("time", "event", "target", "size", "path")
        self.tree = ttk.Treeview(card, columns=cols, show="headings")
        for col, text, width, anchor, stretch in (
            ("time", "Time", 150, "w", False),
            ("event", "Event", 110, "w", False),
            ("target", "Target", 150, "w", False),
            ("size", "Size", 90, "e", False),
            ("path", "Path / details", 500, "w", True),
        ):
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, width=width, anchor=anchor, stretch=stretch)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(T.S3, 0), pady=T.S3)
        sb = ctk.CTkScrollbar(card, command=self.tree.yview, button_color=T.BORDER)
        sb.grid(row=0, column=1, sticky="ns", pady=T.S3, padx=(0, T.S1))
        self.tree.configure(yscrollcommand=sb.set)
        self.footer = label(self, str(log_dir()), size=11, color=T.TEXT_3)
        self.footer.grid(row=2, column=0, sticky="ew", pady=(T.S2, 0))
        self.refresh()

    def refresh(self) -> None:
        """Reload the latest 500 audit entries."""
        self.tree.delete(*self.tree.get_children())
        lines = list(reversed(tail(500)))
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            try:
                when = datetime.fromisoformat(e.get("ts", "")).astimezone()
                stamp = when.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                stamp = e.get("ts", "")
            event = str(e.get("event", ""))
            size = human_size(e.get("bytes") or e.get("bytes_freed") or 0)
            detail = e.get("path") or ", ".join(
                f"{k}={v}" for k, v in e.items() if k not in ("ts", "event", "target", "path")
            )
            self.tree.insert(
                "",
                "end",
                values=(stamp, event.replace("_", " "), e.get("target", ""), size, detail),
            )
        self.footer.configure(text=f"{len(lines)} entries shown · {log_dir()}")

    def open_folder(self) -> None:
        """Open the audit log folder in Explorer."""
        folder = log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        if osutils.IS_WINDOWS:
            os.startfile(folder)  # type: ignore[attr-defined]


class DiskSweepApp(ctk.CTk):
    """Top-level window: sidebar on the left, the selected page on the right."""

    def __init__(self) -> None:
        super().__init__(fg_color=T.APP_BG)
        self.title(f"DiskSweep {__version__}")
        self.geometry("1180x800")
        self.minsize(980, 660)
        self.runner = TaskRunner(self)
        self.is_admin = osutils.is_admin()
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=0, column=1, sticky="nsew", padx=T.S4 + 4, pady=T.S4)
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)
        self.dashboard = Dashboard(content, self.runner)
        self.analyzer = AnalyzerTab(content, self.runner)
        self.log_tab = LogPage(content)
        self.pages = {
            "Dashboard": self.dashboard,
            "Analyzer": self.analyzer,
            "Audit log": self.log_tab,
        }
        self.show_page("Dashboard")
        self.set_theme("System")

    # -- sidebar --------------------------------------------------------- #

    def _build_sidebar(self) -> None:
        side = ctk.CTkFrame(self, fg_color=T.SIDEBAR, corner_radius=0, width=232)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(5, weight=1)

        brand = ctk.CTkFrame(side, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=T.S4, pady=(T.S4, T.S4 + 4))
        ctk.CTkLabel(
            brand,
            text="DS",
            width=36,
            height=36,
            corner_radius=10,
            fg_color=T.ACCENT,
            text_color="#ffffff",
            font=T.font(14, "bold"),
        ).pack(side="left")
        names = ctk.CTkFrame(brand, fg_color="transparent")
        names.pack(side="left", padx=(T.S2 + 2, 0))
        label(names, "DiskSweep", size=16, weight="bold").pack(anchor="w")
        label(names, f"Version {__version__}", size=11, color=T.TEXT_3).pack(anchor="w")

        self.nav: dict[str, ctk.CTkButton] = {}
        for i, name in enumerate(("Dashboard", "Analyzer", "Audit log"), start=1):
            self.nav[name] = self._nav_button(side, name)
            self.nav[name].grid(row=i, column=0, sticky="ew", padx=T.S3, pady=2)

        bottom = ctk.CTkFrame(side, fg_color="transparent")
        bottom.grid(row=6, column=0, sticky="ew", padx=T.S3, pady=T.S3)
        bottom.grid_columnconfigure(0, weight=1)
        status = Card(bottom, fg_color=T.CARD_ALT, border_width=0)
        status.grid(row=0, column=0, sticky="ew", pady=(0, T.S3))
        status.grid_columnconfigure(1, weight=1)
        icon_font = ctk.CTkFont(family=ICON_FONT, size=16) if ICON_FONT else T.font(14)
        ctk.CTkLabel(
            status,
            text=ICONS["shield"] if ICON_FONT else "◆",
            font=icon_font,
            text_color=T.ACCENT,
            width=24,
        ).grid(row=0, column=0, rowspan=2, padx=(T.S3, T.S2), pady=T.S3)
        label(
            status, "Administrator" if self.is_admin else "Standard user", size=12, weight="bold"
        ).grid(row=0, column=1, sticky="sw", pady=(T.S2, 0))
        label(
            status,
            "All targets available" if self.is_admin else "Admin targets skipped",
            size=11,
            color=T.TEXT_3,
        ).grid(row=1, column=1, sticky="nw", pady=(0, T.S2))
        if osutils.IS_WINDOWS and not self.is_admin:
            button(
                status, "Restart as admin", self.restart_as_admin, kind="neutral", width=170
            ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=T.S3, pady=(0, T.S3))

        label(bottom, "APPEARANCE", size=11, weight="bold", color=T.TEXT_3).grid(
            row=1, column=0, sticky="w", pady=(0, T.S1)
        )
        self.theme_var = ctk.StringVar(value="System")
        ctk.CTkSegmentedButton(
            bottom,
            values=["System", "Light", "Dark"],
            variable=self.theme_var,
            command=self.set_theme,
            height=30,
            font=T.font(12),
            selected_color=T.ACCENT_BTN,
            selected_hover_color=T.ACCENT_HOVER,
            unselected_color=T.CARD_ALT,
            unselected_hover_color=T.HOVER,
            fg_color=T.CARD_ALT,
            text_color=("#0b0b0b", "#f0efec"),
        ).grid(row=2, column=0, sticky="ew")

    def _nav_button(self, master: ctk.CTkFrame, name: str) -> ctk.CTkButton:
        btn = ctk.CTkButton(
            master,
            text=f"   {name}",
            anchor="w",
            height=40,
            corner_radius=8,
            fg_color="transparent",
            hover_color=T.HOVER,
            text_color=T.TEXT_2,
            font=T.font(13, "bold"),
            command=lambda: self.show_page(name),
        )
        if ICON_FONT:
            icon = ctk.CTkLabel(
                btn,
                text=ICONS[name],
                width=20,
                font=ctk.CTkFont(family=ICON_FONT, size=16),
                fg_color="transparent",
                text_color=T.TEXT_2,
            )
            icon.place(x=14, rely=0.5, anchor="w")
            icon.bind("<Button-1>", lambda _e: self.show_page(name))
            btn._nav_icon = icon  # type: ignore[attr-defined]
            btn.configure(text=f"        {name}")
        return btn

    # -- navigation & theme ------------------------------------------------ #

    def show_page(self, name: str) -> None:
        """Show one page and highlight its nav item."""
        for key, page in self.pages.items():
            if key == name:
                page.grid(row=0, column=0, sticky="nsew")
            else:
                page.grid_remove()
        for key, btn in self.nav.items():
            active = key == name
            btn.configure(
                fg_color=T.ACCENT_SOFT if active else "transparent",
                text_color=T.ACCENT_TEXT if active else T.TEXT_2,
            )
            icon = getattr(btn, "_nav_icon", None)
            if icon is not None:
                icon.configure(
                    text_color=T.ACCENT_TEXT if active else T.TEXT_2,
                    fg_color=T.ACCENT_SOFT if active else "transparent",
                )
        if name == "Audit log":
            self.log_tab.refresh()

    def set_theme(self, value: str) -> None:
        """Switch between system, light and dark appearance."""
        ctk.set_appearance_mode(value.lower())
        style_treeview(ctk.get_appearance_mode() == "Dark")
        self.dashboard.refresh_theme()
        self.analyzer.refresh_theme()

    def restart_as_admin(self) -> None:
        """Relaunch the GUI elevated (UAC prompt) and close this window."""
        try:
            started = osutils.relaunch_elevated(["gui"])
        except OSError as exc:
            dlg = ModalDialog(self, "DiskSweep", "Could not restart", icon="!", icon_color=T.DANGER)
            label(dlg.body, str(exc), size=12, color=T.TEXT_2).grid(sticky="ew")
            dlg.add_buttons([("OK", "primary")], default="OK")
            dlg.show()
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
