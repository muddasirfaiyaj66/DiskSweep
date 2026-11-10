"""Design tokens for the GUI: colors (light, dark), typography and spacing.

Every widget takes its colors from here so the app reads as one system. Colors
are ``(light, dark)`` tuples, which CustomTkinter resolves per appearance mode.
The accent and chart blue were validated (>= 3:1) against the card surfaces.
"""

from __future__ import annotations

import sys

import customtkinter as ctk

# -- surfaces ---------------------------------------------------------------- #
APP_BG = ("#f3f3f1", "#121212")
SIDEBAR = ("#ffffff", "#1a1a1a")
CARD = ("#ffffff", "#1f1f1f")
CARD_ALT = ("#f7f7f5", "#262626")  # rows / insets inside a card
BORDER = ("#e3e2dc", "#2e2e2e")
HOVER = ("#eeeeeb", "#2a2a2a")

# -- ink --------------------------------------------------------------------- #
TEXT = ("#0b0b0b", "#f0efec")
TEXT_2 = ("#52514e", "#c3c2b7")
TEXT_3 = ("#6f6e69", "#8f8e88")

# -- accent & actions -------------------------------------------------------- #
ACCENT = ("#2a78d6", "#3987e5")  # marks: chart bars, rings, checkboxes
ACCENT_BTN = ("#1f6bc4", "#2a6bc2")  # filled buttons (white text >= 4.5:1)
ACCENT_HOVER = ("#17559e", "#1f5aa8")
ACCENT_TEXT = ("#1c5fae", "#8ab8f2")  # accent-colored text on cards / soft fills
ACCENT_SOFT = ("#e3eefb", "#1b2a3d")  # selected nav item / track
DANGER = ("#c6342c", "#d03b33")
DANGER_HOVER = ("#a12620", "#b02e27")
NEUTRAL_BTN = ("#e9e9e6", "#2c2c2c")
NEUTRAL_BTN_HOVER = ("#dcdcd8", "#363636")

# -- status (risk) - always paired with a text label, never color alone ------ #
RISK = {
    "safe": {"fg": ("#e5f4e5", "#15301a"), "text": ("#13651a", "#7fd88a")},
    "moderate": {"fg": ("#fdf0d8", "#3a2a0c"), "text": ("#8a5300", "#f5b94f")},
    "advanced": {"fg": ("#fbe3e1", "#3d1714"), "text": ("#a3211a", "#ff8a80")},
}
USAGE = {"ok": ACCENT, "warn": ("#d98a00", "#f5a524"), "full": ("#c6342c", "#e5534b")}

# -- spacing (8px grid) ------------------------------------------------------ #
S1, S2, S3, S4 = 4, 8, 16, 24
RADIUS = 12

_FAMILY = "Segoe UI" if sys.platform == "win32" else None


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    """App font (Segoe UI on Windows)."""
    if _FAMILY:
        return ctk.CTkFont(family=_FAMILY, size=size, weight=weight)  # type: ignore[arg-type]
    return ctk.CTkFont(size=size, weight=weight)  # type: ignore[arg-type]


def resolve(color: tuple[str, str]) -> str:
    """Pick the light or dark value for plain-Tk widgets (canvas, ttk)."""
    return color[1] if ctk.get_appearance_mode() == "Dark" else color[0]
