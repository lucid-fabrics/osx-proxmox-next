"""The one Textual theme of the app, `osx-next`. Every colour in app.tcss is a variable of this theme."""

from __future__ import annotations

from textual.theme import Theme

__all__ = ["THEME_NAME", "OSX_NEXT"]

THEME_NAME = "osx-next"

BACKGROUND = "#0b0f14"
SURFACE = "#121821"
RAISED = "#1a2330"
EDGE = "#2a3645"
TEXT = "#e6edf3"
MUTED = "#8b98a8"
ACCENT = "#4da3ff"
ON_ACCENT = "#06121f"
SUCCESS = "#3fb950"
WARNING = "#d29922"
ERROR = "#f85149"

OSX_NEXT = Theme(
    name=THEME_NAME,
    primary=ACCENT,
    secondary=ACCENT,
    accent=ACCENT,
    warning=WARNING,
    error=ERROR,
    success=SUCCESS,
    foreground=TEXT,
    background=BACKGROUND,
    surface=SURFACE,
    panel=RAISED,
    dark=True,
    variables={
        "background": BACKGROUND,
        "surface": SURFACE,
        "panel": RAISED,
        "foreground": TEXT,
        "text": TEXT,
        "primary": ACCENT,
        "accent": ACCENT,
        "success": SUCCESS,
        "warning": WARNING,
        "error": ERROR,
        "raised": RAISED,
        "edge": EDGE,
        "muted": MUTED,
        "on-accent": ON_ACCENT,
        "block-cursor-background": ACCENT,
        "block-cursor-foreground": ON_ACCENT,
        "input-cursor-background": ACCENT,
        "input-cursor-foreground": ON_ACCENT,
        "input-selection-background": f"{ACCENT} 35%",
        "scrollbar": RAISED,
        "scrollbar-background": BACKGROUND,
        "scrollbar-hover": EDGE,
        "scrollbar-active": ACCENT,
        "border": EDGE,
        "border-blurred": EDGE,
    },
)
