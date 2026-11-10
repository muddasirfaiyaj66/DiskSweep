"""Reusable, theme-aware widgets for the modern GUI."""

from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Callable, Sequence
from typing import Any

import customtkinter as ctk

from disksweep.gui import theme as T


class Card(ctk.CTkFrame):
    """Rounded surface with a hairline border."""

    def __init__(self, master: Any, **kw: Any) -> None:
        kw.setdefault("fg_color", T.CARD)
        kw.setdefault("corner_radius", T.RADIUS)
        kw.setdefault("border_width", 1)
        kw.setdefault("border_color", T.BORDER)
        super().__init__(master, **kw)


def label(
    master: Any,
    text: str = "",
    *,
    size: int = 13,
    weight: str = "normal",
    color: tuple[str, str] = T.TEXT,
    **kw: Any,
) -> ctk.CTkLabel:
    """Themed label shortcut."""
    kw.setdefault("anchor", "w")
    return ctk.CTkLabel(master, text=text, font=T.font(size, weight), text_color=color, **kw)


def button(
    master: Any,
    text: str,
    command: Callable[[], None],
    *,
    kind: str = "primary",
    width: int = 120,
    **kw: Any,
) -> ctk.CTkButton:
    """Primary / danger / neutral button with consistent geometry."""
    colors = {
        "primary": (T.ACCENT_BTN, T.ACCENT_HOVER, ("#ffffff", "#ffffff")),
        "danger": (T.DANGER, T.DANGER_HOVER, ("#ffffff", "#ffffff")),
        "neutral": (T.NEUTRAL_BTN, T.NEUTRAL_BTN_HOVER, T.TEXT),
    }[kind]
    return ctk.CTkButton(
        master,
        text=text,
        command=command,
        width=width,
        height=36,
        corner_radius=8,
        fg_color=colors[0],
        hover_color=colors[1],
        text_color=colors[2],
        text_color_disabled=T.TEXT_3,
        font=T.font(13, "bold"),
        **kw,
    )


class RiskBadge(ctk.CTkLabel):
    """Tinted pill with the risk name (color is never the only signal)."""

    def __init__(self, master: Any, risk: str) -> None:
        style = T.RISK[risk]
        super().__init__(
            master,
            text=risk.capitalize(),
            fg_color=style["fg"],
            text_color=style["text"],
            corner_radius=10,
            width=78,
            height=22,
            font=T.font(11, "bold"),
        )


class Chip(ctk.CTkLabel):
    """Small neutral tag such as "Admin"."""

    def __init__(self, master: Any, text: str) -> None:
        super().__init__(
            master,
            text=text,
            fg_color=T.CARD_ALT,
            text_color=T.TEXT_2,
            corner_radius=10,
            width=56,
            height=22,
            font=T.font(11, "bold"),
        )


class StatCard(Card):
    """Caption, big number, and a secondary line."""

    def __init__(self, master: Any, caption: str) -> None:
        super().__init__(master)
        self.grid_columnconfigure(0, weight=1)
        label(self, caption.upper(), size=11, weight="bold", color=T.TEXT_3).grid(
            row=0, column=0, sticky="ew", padx=T.S3, pady=(T.S3, 0)
        )
        self.value = label(self, "—", size=30, weight="bold")
        self.value.grid(row=1, column=0, sticky="ew", padx=T.S3)
        self.detail = label(self, "", size=12, color=T.TEXT_2)
        self.detail.grid(row=2, column=0, sticky="ew", padx=T.S3, pady=(0, T.S3))

    def set(self, value: str, detail: str = "") -> None:
        """Update the number and the secondary line."""
        self.value.configure(text=value)
        self.detail.configure(text=detail)


class UsageRing(tk.Canvas):
    """Donut showing used/total, with the percentage in the middle."""

    def __init__(self, master: Any, size: int = 96) -> None:
        super().__init__(master, width=size, height=size, highlightthickness=0, borderwidth=0)
        self.size = size
        self.fraction = 0.0
        self.font = T.font(17, "bold")
        self.small = T.font(10)

    def set(self, fraction: float) -> None:
        """Set the used fraction (0..1) and redraw."""
        self.fraction = max(0.0, min(1.0, fraction))
        self.redraw()

    def redraw(self) -> None:
        """Draw for the current theme."""
        s, w = self.size, 10
        self.configure(bg=T.resolve(T.CARD))
        self.delete("all")
        level = "full" if self.fraction >= 0.9 else "warn" if self.fraction >= 0.8 else "ok"
        box = (w / 2 + 1, w / 2 + 1, s - w / 2 - 1, s - w / 2 - 1)
        self.create_oval(*box, outline=T.resolve(T.CARD_ALT), width=w)
        extent = -359.9 * self.fraction
        if self.fraction > 0:
            self.create_arc(
                *box,
                start=90,
                extent=extent,
                style="arc",
                outline=T.resolve(T.USAGE[level]),
                width=w,
            )
            # round caps: dots at both ends of the arc
            r = (s - w) / 2 - 1
            for ang in (90, 90 + extent):
                cx = s / 2 + r * math.cos(math.radians(ang))
                cy = s / 2 - r * math.sin(math.radians(ang))
                self.create_oval(
                    cx - w / 2,
                    cy - w / 2,
                    cx + w / 2,
                    cy + w / 2,
                    fill=T.resolve(T.USAGE[level]),
                    outline="",
                )
        self.create_text(
            s / 2, s / 2 - 6, text=f"{self.fraction:.0%}", font=self.font, fill=T.resolve(T.TEXT)
        )
        self.create_text(s / 2, s / 2 + 14, text="used", font=self.small, fill=T.resolve(T.TEXT_3))


class SizeBar(tk.Canvas):
    """Thin proportional bar under a row: this item's size relative to the largest."""

    def __init__(self, master: Any, width: int = 120) -> None:
        super().__init__(master, width=width, height=6, highlightthickness=0, borderwidth=0)
        self.fraction = 0.0
        self.bind("<Configure>", lambda _e: self.redraw())

    def set(self, fraction: float) -> None:
        """0..1 of the track."""
        self.fraction = max(0.0, min(1.0, fraction))
        self.redraw()

    def redraw(self) -> None:
        """Draw for the current theme."""
        self.configure(bg=T.resolve(T.CARD))
        self.delete("all")
        w = self.winfo_width()
        self.create_line(3, 3, w - 3, 3, fill=T.resolve(T.CARD_ALT), width=6, capstyle="round")
        if self.fraction > 0:
            x1 = 3 + (w - 6) * self.fraction
            self.create_line(
                3, 3, max(x1, 4), 3, fill=T.resolve(T.ACCENT), width=6, capstyle="round"
            )


class ModalDialog(ctk.CTkToplevel):
    """Modern modal dialog: title, body widgets and right-aligned buttons.

    ``show()`` blocks (``wait_window``) while still processing events, and returns
    the label of the button pressed, or None if the window was closed.
    """

    def __init__(
        self,
        master: Any,
        title: str,
        heading: str,
        *,
        icon: str = "i",
        icon_color: tuple[str, str] = T.ACCENT,
        width: int = 520,
    ) -> None:
        super().__init__(master, fg_color=T.CARD)
        self.title(title)
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        self.result: str | None = None
        self.protocol("WM_DELETE_WINDOW", lambda: self.close(None))
        self.grid_columnconfigure(1, weight=1)
        # Fixed 36x36 circle; the glyph is placed inside so it can never stretch it.
        badge = ctk.CTkFrame(self, width=36, height=36, corner_radius=18, fg_color=icon_color)
        badge.grid(row=0, column=0, padx=(T.S4, T.S3), pady=(T.S4, 0), sticky="n")
        badge.grid_propagate(False)
        ctk.CTkLabel(
            badge,
            text=icon,
            text_color="#ffffff",
            fg_color="transparent",
            font=T.font(16, "bold"),
            width=20,
            height=20,
        ).place(relx=0.5, rely=0.5, anchor="center")
        label(self, heading, size=17, weight="bold", wraplength=width - 120, justify="left").grid(
            row=0, column=1, sticky="ew", padx=(0, T.S4), pady=(T.S4, 0)
        )
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=1, sticky="nsew", padx=(0, T.S4), pady=(T.S2, T.S3))
        self.body.grid_columnconfigure(0, weight=1)
        self.footer = ctk.CTkFrame(self, fg_color=T.CARD_ALT, corner_radius=0)
        self.footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        self._width = width

    def add_buttons(self, buttons: Sequence[tuple[str, str]], default: str) -> None:
        """``[(label, kind), ...]`` right-aligned; ``default`` gets Enter."""
        for text, kind in reversed(buttons):
            b = button(self.footer, text, lambda t=text: self.close(t), kind=kind, width=110)
            b.pack(side="right", padx=(0, T.S3) if text == buttons[-1][0] else (0, T.S2), pady=T.S3)
        self.bind("<Return>", lambda _e: self.close(default))
        self.bind("<Escape>", lambda _e: self.close(None))

    def close(self, result: str | None) -> None:
        """Close with a result."""
        self.result = result
        self.grab_release()
        self.destroy()

    def show(self) -> str | None:
        """Center on the parent, make modal, and wait for a choice."""
        self.update_idletasks()
        parent = self.master.winfo_toplevel()
        scale = self._get_window_scaling()  # CTk scales size (not position) by DPI
        w = max(self._width, round(self.winfo_reqwidth() / scale))
        h = round(self.winfo_reqheight() / scale)
        x = parent.winfo_rootx() + (parent.winfo_width() - round(w * scale)) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - round(h * scale)) // 3
        self.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        self.lift()
        self.focus_force()
        self.after(10, self.grab_set)
        self.wait_window()
        return self.result
