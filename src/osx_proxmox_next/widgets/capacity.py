"""A labelled capacity bar: what a new Mac takes of the host's CPU, memory or disk."""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget

__all__ = ["CapacityBar", "bar_state"]

AMBER_AT = 0.75


def bar_state(used: float, total: float) -> str:
    """"unknown" without a total, "over" when it does not fit, "warn" above 75 percent, else "ok"."""
    if total <= 0:
        return "unknown"
    if used > total:
        return "over"
    if used / total > AMBER_AT:
        return "warn"
    return "ok"


class CapacityBar(Widget):
    DEFAULT_CSS = """
    CapacityBar { height: 2; }
    """

    def __init__(self, label: str, unit: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.unit = unit
        self.used = 0.0
        self.total = 0.0
        self.suffix = "free"  # "of 32 cores" / "of 412 GB free"

    def set_values(self, used: float, total: float, suffix: str = "") -> None:
        self.used, self.total, self.suffix = used, total, suffix
        self.refresh()

    @property
    def state(self) -> str:
        return bar_state(self.used, self.total)

    def summary(self, room: int = 0) -> str:
        """The right-hand text, with a glyph so colour is never the only signal. It is shortened, never cut,
        when *room* columns do not hold it: first "does not fit" becomes "no fit", then the glyph alone."""
        used = f"{self.used:g}"
        if self.state == "unknown":
            return f"{used} {self.unit}"
        total = f"{self.total:g}"
        base = f"{used} of {total} {self.suffix or self.unit}"
        if self.state == "warn":
            return base + " !"
        if self.state != "over":
            return base
        for tail in (" \u2717 does not fit", " \u2717 too big", " \u2717"):
            if not room or len(base + tail) <= room:
                return base + tail
        return base + " \u2717"

    def render(self) -> Text:
        pal = self.app.get_css_variables()
        width = max(1, self.size.width)
        state = self.state
        tone = {"ok": pal["accent"], "warn": pal["warning"], "over": pal["error"], "unknown": pal["muted"]}[state]
        head = Text(self.label)
        right = self.summary(width - len(self.label) - 1)
        head.append(" " * max(1, width - len(self.label) - len(right)))
        head.append(right, style=pal["muted"] if state in ("ok", "unknown") else tone)
        ratio = 0.0 if state == "unknown" else min(1.0, self.used / self.total)
        filled = int(round(ratio * width))
        if state == "over":
            filled = width
        if 0 < ratio and filled == 0:
            filled = 1
        bar = Text("━" * filled, style=tone)
        bar.append("━" * (width - filled), style=pal["raised"])
        head.append("\n")
        head.append_text(bar)
        return head
