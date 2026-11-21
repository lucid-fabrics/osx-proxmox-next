"""The bars every screen shares: the top bar and the one-row key footer."""

from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.widget import Widget

from .. import __version__

__all__ = ["TopBar", "KeyBar", "colors"]

KeyItem = tuple  # (key label, description, action or None)


def colors(widget: Widget) -> dict[str, str]:
    """The theme colours as CSS strings (accent, muted, success, ...)."""
    return widget.app.get_css_variables()


class TopBar(Widget):
    """`osx-next <version> > crumb` on the left, the host chip on the right."""

    DEFAULT_CSS = """
    TopBar { height: 1; dock: top; background: $surface; color: $foreground; }
    """

    def __init__(self, crumb: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self.crumb = crumb

    def set_crumb(self, crumb: str) -> None:
        self.crumb = crumb
        self.refresh()

    def render(self) -> Text:
        pal = colors(self)
        left = Text(" ")
        left.append("osx-next", style="bold")
        left.append(" " + __version__, style=pal["muted"])
        if self.crumb:
            left.append(" › ", style=pal["muted"])
            left.append(self.crumb)
        chip = getattr(self.app, "host_chip", None)
        right = chip(pal) if chip else Text("")
        right.append(" ")
        gap = max(1, self.size.width - left.cell_len - right.cell_len)
        left.append(" " * gap)
        left.append_text(right)
        left.no_wrap = True
        left.overflow = "ellipsis"
        return left


class KeyBar(Widget):
    """One row listing the keys of the screen: accent key, plain label. Clicking a key runs it."""

    DEFAULT_CSS = """
    KeyBar { height: 1; dock: bottom; background: $surface; color: $foreground; }
    """

    def __init__(self, items: list[KeyItem] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.items: list[KeyItem] = items or []
        self._spans: list[tuple[int, int, str | None]] = []

    def set_items(self, items: list[KeyItem]) -> None:
        self.items = items
        self.refresh()

    @staticmethod
    def _width(items: list[KeyItem], gap: int) -> int:
        return 1 + sum(len(k) + 1 + len(label) for k, label, _a in items) + gap * max(0, len(items) - 1)

    def fitted(self, width: int) -> tuple[list[KeyItem], int]:
        """(items, gap) that fit in *width* columns: a tighter gap first, then the middle keys go.

        The first key and the last two (help and quit) always stay; the help overlay lists the rest."""
        items = list(self.items)
        if not width or self._width(items, 3) <= width:
            return items, 3
        if self._width(items, 1) <= width:
            return items, 1
        while len(items) > 3 and self._width(items, 1) > width:
            del items[len(items) - 3]
        return items, 1

    def render(self) -> Text:
        """The accent key in bold, its label in plain text, nothing underlined (clicks are mapped by position)."""
        pal = colors(self)
        items, gap = self.fitted(self.size.width)
        text = Text(" ")
        self._spans = []
        for idx, (key, label, action) in enumerate(items):
            if idx:
                text.append(" " * gap)
            start = text.cell_len
            text.append(key, style=Style(color=pal["accent"], bold=True))
            text.append(" " + label)
            self._spans.append((start, text.cell_len, action))
        text.no_wrap = True
        text.overflow = "ellipsis"
        return text

    async def on_click(self, event) -> None:
        for start, end, action in self._spans:
            if action and start <= event.x < end:
                await self.app.run_action(action, default_namespace=self)
                return
