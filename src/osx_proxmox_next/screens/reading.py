"""Text that can be taller than the terminal: a scrolling area that fits what is left of the screen, keys that
move it (a line, a page, the ends) and the "N more lines" hint under it."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

__all__ = ["ReadScroll", "Readable", "READ_BINDINGS", "fit_scroll", "more_text", "box_room"]

READ_BINDINGS = [
    Binding("up", "read_line(-1)", "Read on", show=False),
    Binding("down", "read_line(1)", "Read on", show=False),
    Binding("pageup", "read_page(-1)", "Read on", show=False),
    Binding("pagedown", "read_page(1)", "Read on", show=False),
    Binding("home", "read_edge(0)", "Read on", show=False),
    Binding("end", "read_edge(1)", "Read on", show=False),
]


class ReadScroll(VerticalScroll):
    """A scrolling area that moves at once (never animated) and tells its screen when it moved."""

    can_focus = False       # the keys belong to the screen, so tab goes straight to the buttons

    class Moved(Message):
        pass

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        self.post_message(self.Moved())

    def on_resize(self) -> None:
        self.post_message(self.Moved())          # the room changed, so the hint did too

    def read_to(self, y: float) -> None:
        self.scroll_to(y=max(0, min(self.max_scroll_y, y)), animate=False)

    def hidden_below(self) -> int:
        return max(0, int(self.virtual_size.height - self.size.height - self.scroll_y))


def more_text(scroll: ReadScroll, accent: str) -> Text:
    hidden = scroll.hidden_below()
    return Text(f"↓ {hidden} more line{'s' if hidden != 1 else ''}", style=accent) if hidden else Text("")


def fit_scroll(scroll: ReadScroll, more: Static, room: int, accent: str) -> None:
    """Give the scroll the *room* rows left for it and its hint. A text that fits needs neither the hint
    nor a scrollbar, so the hint row only exists while the text is taller than the room."""
    more.display = False
    others = 0
    parent = scroll.parent
    for child in (parent.children if parent is not None else []):
        if child is not scroll and child.display:
            others += child.outer_size.height + child.styles.margin.height
    left = room - others
    content = int(scroll.virtual_size.height)
    if content > left:
        more.display = True
        left -= 1
    scroll.styles.max_height = max(3, left)
    more.update(more_text(scroll, accent))


class Readable:
    """The keys and the hint of a screen with one ReadScroll. A subclass names the ids and the room."""

    read_scroll = ""     # id of the ReadScroll
    read_more = ""       # id of the Static under it that holds the hint

    def read_room(self) -> int:
        """Rows available to the scroll, its siblings and the hint."""
        raise NotImplementedError

    def _reader(self) -> ReadScroll:
        return self.query_one(f"#{self.read_scroll}", ReadScroll)       # type: ignore[attr-defined]

    def read_refit(self) -> None:
        """Fit the scroll to the screen once the layout is known, and again after the fit changed it."""
        def fit() -> None:
            try:
                fit_scroll(self._reader(), self.query_one(f"#{self.read_more}", Static),     # type: ignore[attr-defined]
                           self.read_room(), self.app.get_css_variables()["accent"])         # type: ignore[attr-defined]
                self.call_after_refresh(self.show_more)                 # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - not composed yet, or the screen is closing
                pass
        self.call_after_refresh(fit)                                    # type: ignore[attr-defined]
        self.call_after_refresh(lambda: self.call_after_refresh(fit))   # type: ignore[attr-defined]

    def show_more(self) -> None:
        try:
            self.query_one(f"#{self.read_more}", Static).update(     # type: ignore[attr-defined]
                more_text(self._reader(), self.app.get_css_variables()["accent"]))   # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def on_read_scroll_moved(self, event: ReadScroll.Moved) -> None:
        event.stop()
        self.show_more()

    def on_resize(self) -> None:
        self.read_refit()

    def action_read_line(self, delta: int) -> None:
        box = self._reader()
        box.read_to(box.scroll_y + delta)

    def action_read_page(self, direction: int) -> None:
        box = self._reader()
        box.read_to(box.scroll_y + direction * max(1, box.size.height - 1))

    def action_read_edge(self, end: int) -> None:
        box = self._reader()
        box.read_to(box.max_scroll_y if end else 0)


def box_room(screen_height: int, box: Widget) -> int:
    """Rows inside a centred dialog (90 percent of the screen less the key bar), less its border and padding."""
    return int((screen_height - 1) * 0.9) - 2 - box.styles.padding.height
