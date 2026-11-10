"""Lightweight horizontal bar charts drawn on a Tk canvas (no matplotlib needed).

Design rules (kept deliberately small and consistent):

* one series, one hue (validated >= 3:1 against both theme surfaces), no legend;
* bars <= 20px thick with a 4px rounded data-end, square at the baseline;
* value at each bar's tip in a text color, never the bar color;
* a hairline baseline, no gridlines (every bar is labelled);
* hover tooltip per bar (row-wide hit target) and optional click action;
* redrawn on resize and theme change.
"""

from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import customtkinter as ctk

from disksweep.units import human_size

# Light / dark tokens. Bar colors validated against the surfaces they sit on.
THEME = {
    "Light": {
        "surface": "#ffffff",  # theme.CARD (light)
        "bar": "#2a78d6",
        "bar_hover": "#1c5fae",
        "text": "#0b0b0b",
        "text2": "#52514e",
        "axis": "#a8a7a0",
        "tip_bg": "#f7f7f5",
        "tip_border": "#b9b8b0",
    },
    "Dark": {
        "surface": "#1f1f1f",  # theme.CARD (dark)
        "bar": "#3987e5",
        "bar_hover": "#6aa6ee",
        "text": "#f0efec",
        "text2": "#c3c2b7",
        "axis": "#5a5955",
        "tip_bg": "#2a2a2a",
        "tip_border": "#4a4945",
    },
}

MAX_BAR = 20
RADIUS = 4


def palette() -> dict[str, str]:
    """Tokens for the current appearance mode."""
    return THEME["Dark" if ctk.get_appearance_mode() == "Dark" else "Light"]


@dataclass
class Bar:
    """One bar: label on the left, value drives the length."""

    label: str
    value: int
    tooltip: str = ""
    key: str | None = None  # passed to the click callback (e.g. a folder path)


def ellipsize(text: str, font: ctk.CTkFont, width: int, *, middle: bool = False) -> str:
    """Shorten ``text`` with an ellipsis so it fits ``width`` pixels."""
    if font.measure(text) <= width:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    best = ell
    while lo <= hi:
        n = (lo + hi) // 2
        if middle:
            head, tail = text[: (n + 1) // 2], text[len(text) - n // 2 :] if n // 2 else ""
            cand = head + ell + tail
        else:
            cand = text[:n] + ell
        if font.measure(cand) <= width:
            best, lo = cand, n + 1
        else:
            hi = n - 1
    return best


def rounded_end_points(
    x0: float, y0: float, x1: float, y1: float, r: float, steps: int = 6
) -> list[float]:
    """Polygon outline of a bar: square at ``x0``, quarter-circle corners at ``x1``."""
    pts: list[float] = [x0, y0, x1 - r, y0]
    cx, top_cy, bot_cy = x1 - r, y0 + r, y1 - r
    for k in range(1, steps + 1):  # top-right corner, 90 deg -> 0 deg
        a = math.pi / 2 * (1 - k / steps)
        pts += [cx + r * math.cos(a), top_cy - r * math.sin(a)]
    for k in range(1, steps + 1):  # bottom-right corner, 0 deg -> -90 deg
        a = -math.pi / 2 * k / steps
        pts += [cx + r * math.cos(a), bot_cy - r * math.sin(a)]
    pts += [x0, y1]
    return pts


class BarChart(tk.Canvas):
    """Ranked horizontal bar chart with hover tooltips.

    Args:
        master: parent widget.
        on_click: called with ``Bar.key`` when a bar with a key is clicked.
        empty_text: shown when there is no data.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_click: Callable[[str], None] | None = None,
        empty_text: str = "No data yet.",
    ) -> None:
        super().__init__(master, highlightthickness=0, borderwidth=0, height=240)
        self.on_click = on_click
        self.empty_text = empty_text
        self.bars: list[Bar] = []
        self.font = ctk.CTkFont(size=12)
        self.font_bold = ctk.CTkFont(size=12, weight="bold")
        self._rows: list[tuple[int, int]] = []  # (y_top, y_bottom) per row
        self._hover: int | None = None
        self._pending = False
        self.bind("<Configure>", lambda _e: self.schedule_redraw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        self.bind("<Button-1>", self._on_click)

    # -- data / drawing ------------------------------------------------------ #

    def set_data(self, bars: Sequence[Bar]) -> None:
        """Replace the bars (already sorted) and redraw."""
        self.bars = list(bars)
        self._hover = None
        self.schedule_redraw()

    def schedule_redraw(self) -> None:
        """Coalesce bursts of resize events into one redraw."""
        if not self._pending:
            self._pending = True
            self.after_idle(self.redraw)

    def redraw(self) -> None:
        """Draw the whole chart for the current size and theme."""
        self._pending = False
        p = palette()
        self.configure(bg=p["surface"])
        self.delete("all")
        self._rows = []
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 30:
            return
        if not self.bars:
            self.create_text(w / 2, h / 2, text=self.empty_text, fill=p["text2"], font=self.font)
            return

        pad = 12
        n = len(self.bars)
        row_h = max(18, min(34, (h - 2 * pad) // n))
        thick = min(MAX_BAR, max(8, int(row_h * 0.62)))
        label_w = min(int(w * 0.38), max(self.font.measure(b.label) for b in self.bars) + 12)
        value_texts = [human_size(b.value) for b in self.bars]
        value_w = max(self.font.measure(t) for t in value_texts) + 10
        x0 = pad + label_w
        plot_w = max(10, w - x0 - value_w - pad)
        vmax = max((b.value for b in self.bars), default=0) or 1

        top = pad
        for i, (bar, vtext) in enumerate(zip(self.bars, value_texts, strict=True)):
            y_mid = top + i * row_h + row_h / 2
            self._rows.append((int(top + i * row_h), int(top + (i + 1) * row_h)))
            label = ellipsize(bar.label, self.font, label_w - 12, middle=True)
            self.create_text(x0 - 10, y_mid, text=label, anchor="e", fill=p["text"], font=self.font)
            length = plot_w * bar.value / vmax
            color = p["bar_hover"] if i == self._hover else p["bar"]
            self._bar(x0, y_mid - thick / 2, x0 + length, y_mid + thick / 2, color, f"bar{i}")
            self.create_text(
                x0 + length + 6, y_mid, text=vtext, anchor="w", fill=p["text2"], font=self.font
            )
        # Hairline baseline the bars grow from.
        self.create_line(x0, top, x0, top + n * row_h, fill=p["axis"], width=1)
        if self._hover is not None:
            self._draw_tooltip(self._hover)

    def _bar(self, x0: float, y0: float, x1: float, y1: float, color: str, tag: str) -> None:
        """Bar with a rounded data-end and a square baseline end."""
        kw = {"fill": color, "outline": "", "tags": (tag,)}
        r = min(RADIUS, (y1 - y0) / 2)
        if x1 - x0 <= r + 1:
            self.create_rectangle(x0, y0, max(x1, x0 + 2), y1, **kw)
            return
        self.create_polygon(rounded_end_points(x0, y0, x1, y1, r), **kw)

    # -- interaction --------------------------------------------------------- #

    def _row_at(self, y: int) -> int | None:
        for i, (a, b) in enumerate(self._rows):
            if a <= y < b:
                return i
        return None

    def _set_hover(self, index: int | None) -> None:
        if index != self._hover:
            self._hover = index
            clickable = index is not None and self.on_click is not None and self.bars[index].key
            self.configure(cursor="hand2" if clickable else "")
            self.redraw()

    def _on_motion(self, event: tk.Event) -> None:
        self._mouse = (event.x, event.y)
        index = self._row_at(event.y)
        if index != self._hover:
            self._set_hover(index)  # full redraw, includes the tooltip
        elif index is not None:
            self.delete("tooltip")  # just follow the pointer
            self._draw_tooltip(index)

    def _on_click(self, event: tk.Event) -> None:
        i = self._row_at(event.y)
        if i is not None and self.on_click and self.bars[i].key:
            self.on_click(self.bars[i].key)  # type: ignore[arg-type]

    def _draw_tooltip(self, index: int) -> None:
        p = palette()
        bar = self.bars[index]
        lines = [bar.label, *([bar.tooltip] if bar.tooltip else [])]
        mx, my = getattr(self, "_mouse", (self.winfo_width() / 2, self._rows[index][1]))
        pad = 8
        width = (
            max(self.font_bold.measure(lines[0]), *(self.font.measure(x) for x in lines[1:]), 0)
            + 2 * pad
        )
        width = min(width, self.winfo_width() - 8)
        line_h = self.font.metrics("linespace")
        height = line_h * len(lines) + 2 * pad
        x = mx + 14
        y = my + 14
        if x + width > self.winfo_width() - 4:
            x = mx - 14 - width
        if y + height > self.winfo_height() - 4:
            y = my - 14 - height
        x, y = max(4, x), max(4, y)
        self.create_rectangle(
            x,
            y,
            x + width,
            y + height,
            fill=p["tip_bg"],
            outline=p["tip_border"],
            tags=("tooltip",),
        )
        for j, text in enumerate(lines):
            font = self.font_bold if j == 0 else self.font
            shown = ellipsize(text, font, int(width - 2 * pad), middle=j == 0)
            self.create_text(
                x + pad,
                y + pad + j * line_h,
                text=shown,
                anchor="nw",
                fill=p["text"] if j == 0 else p["text2"],
                font=font,
                tags=("tooltip",),
            )
