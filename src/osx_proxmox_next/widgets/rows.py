"""Form rows of the one-screen forms (New Mac, Graphics card): choice rows, field rows, expander rows."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Static

__all__ = ["Option", "ChoiceRow", "FieldRow", "ExpandRow", "LABEL_WIDTH", "wrap_text"]

LABEL_WIDTH = 12


@dataclass
class Option:
    value: str
    label: str
    enabled: bool = True


def wrap_text(text: Text, width: int, console) -> list[Text]:
    """*text* wrapped at word breaks to *width* columns, styles kept. Nothing is ever cut off."""
    out: list[Text] = []
    for line in text.split("\n"):
        out += list(line.wrap(console, max(1, width))) if line.plain else [Text("")]
    return out


class ChoiceRow(Widget, can_focus=True):
    """`Label  < value >` with a muted detail after it and muted hint lines below.

    Left and right (or h and l) change the value and skip options that are not enabled. What does not fit
    on a line wraps onto the next one: a row grows instead of cutting its words."""

    BINDINGS = [
        Binding("left,h", "step(-1)", "Change", show=False),
        Binding("right,l", "step(1)", "Change", show=False),
    ]

    class Changed(Message):
        def __init__(self, row: "ChoiceRow", value: str) -> None:
            super().__init__()
            self.row = row
            self.value = value

    DEFAULT_CSS = """
    ChoiceRow { height: auto; }
    """

    def __init__(self, label: str, options: list[Option], value: str = "", *, hint_lines: int = 1,
                 id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self.label = label
        self.options = options
        self.detail = ""
        self.hint: "str | Text" = ""
        self.value = value or (options[0].value if options else "")

    # ── value ────────────────────────────────────────────────────────

    def set_options(self, options: list[Option], value: str | None = None) -> None:
        self.options = options
        if value is not None:
            self.value = value
        if self.value not in [o.value for o in options if o.enabled]:
            first = next((o.value for o in options if o.enabled), options[0].value if options else "")
            self.value = first
        self.refresh()

    def set_value(self, value: str, notify: bool = False) -> None:
        if value != self.value:
            self.value = value
            if notify:
                self.post_message(self.Changed(self, value))
        self.refresh()

    def set_text(self, detail: str = "", hint: "str | Text" = "") -> None:
        self.detail, self.hint = detail, hint
        self.refresh(layout=True)

    @property
    def current(self) -> Option | None:
        return next((o for o in self.options if o.value == self.value), None)

    def action_step(self, delta: int) -> None:
        usable = [o for o in self.options if o.enabled]
        if len(usable) < 2:
            return
        values = [o.value for o in usable]
        idx = values.index(self.value) if self.value in values else 0
        self.set_value(values[(idx + delta) % len(values)], notify=True)

    def on_click(self, event) -> None:
        self.focus()
        width = self.size.width
        if event.x < LABEL_WIDTH + 2:
            return
        self.action_step(-1 if event.x < (width + LABEL_WIDTH) // 2 else 1)

    # ── drawing ──────────────────────────────────────────────────────

    def _lines(self, width: int) -> list[Text]:
        pal = self.app.get_css_variables()
        line = Text(" " + self.label.ljust(LABEL_WIDTH), style=pal["muted"])
        cur = self.current
        usable = sum(1 for o in self.options if o.enabled) > 1
        edge = pal["accent"] if usable else pal["muted"]
        if usable:
            line.append("\u2039 ", style=edge)
        line.append(cur.label if cur else self.value, style="bold")
        if usable:
            line.append(" \u203a", style=edge)
        pad = " " * (LABEL_WIDTH + 1)
        under: list[Text] = []
        if self.detail:
            tail = " \u00b7 " + self.detail
            if line.cell_len + len(tail) <= width:
                line.append(tail, style=pal["muted"])
            else:  # no room beside the value: the detail goes under it, whole
                under.append(Text(self.detail, style=pal["muted"]))
        hint = self.hint if isinstance(self.hint, Text) else Text(self.hint, style=pal["muted"])
        if hint.plain:
            under.append(hint)
        lines = [line]
        console = self.app.console
        for chunk in under:
            lines += [Text(pad).append_text(row) for row in wrap_text(chunk, max(8, width - len(pad)), console)]
        return lines

    def get_content_height(self, container, viewport, width: int) -> int:
        return len(self._lines(width or 80))

    def render(self) -> Text:
        out = Text()
        for idx, one in enumerate(self._lines(self.size.width or 80)):
            out.append("\n" if idx else "")
            out.append_text(one)
        return out


class FieldRow(Widget):
    """`Label  [one-row input]` and an optional muted suffix."""

    DEFAULT_CSS = """
    FieldRow { height: 1; layout: horizontal; }
    FieldRow > .field-label { width: 13; color: $muted; padding-left: 1; }
    FieldRow > Input { width: 1fr; max-width: 40; }
    FieldRow > .field-suffix { width: auto; color: $muted; padding-left: 1; }
    """

    def __init__(self, label: str, value: str = "", *, placeholder: str = "", suffix: str = "",
                 id: str | None = None, input_id: str | None = None, classes: str | None = None,
                 max_width: int | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self.label = label
        self.initial = value
        self.placeholder = placeholder
        self.suffix = suffix
        self.input_id = input_id or (f"{id}-input" if id else None)
        self.max_width = max_width

    def compose(self) -> ComposeResult:
        yield Static(self.label, classes="field-label")
        field = Input(value=self.initial, placeholder=self.placeholder, id=self.input_id, select_on_focus=False)
        if self.max_width:
            field.styles.max_width = self.max_width
        yield field
        yield Static(self.suffix, classes="field-suffix")

    @property
    def input(self) -> Input:
        return self.query_one(Input)

    @property
    def value(self) -> str:
        return self.input.value.strip()

    def set_suffix(self, text: str) -> None:
        self.query_one(".field-suffix", Static).update(text)

    def set_invalid(self, bad: bool) -> None:
        self.input.set_class(bad, "invalid")


class ExpandRow(Widget, can_focus=True):
    """`> More  hint`: enter, space or a click opens or closes a group of rows."""

    BINDINGS = [
        Binding("enter,space", "toggle", "Open", show=False),
        Binding("right,l", "open", "Open", show=False),
        Binding("left,h", "close", "Close", show=False),
    ]

    class Toggled(Message):
        def __init__(self, row: "ExpandRow", open_: bool) -> None:
            super().__init__()
            self.row = row
            self.open = open_

    DEFAULT_CSS = "ExpandRow { height: 1; }"

    def __init__(self, label: str, hint: str, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.label = label
        self.hint = hint
        self.open = False

    def action_toggle(self) -> None:
        self.set_open(not self.open)

    def action_open(self) -> None:
        self.set_open(True)

    def action_close(self) -> None:
        self.set_open(False)

    def set_open(self, value: bool) -> None:
        if value != self.open:
            self.open = value
            self.post_message(self.Toggled(self, value))
        self.refresh()

    def on_click(self) -> None:
        self.focus()
        self.action_toggle()

    def render(self) -> Text:
        pal = self.app.get_css_variables()
        line = Text(" ")
        line.append("▾" if self.open else "▸", style=pal["accent"])
        line.append(" " + self.label.ljust(LABEL_WIDTH - 2))
        line.append(self.hint, style=pal["muted"])
        return line
