"""The "Before you continue" dialog: the one approval of warnings, the notice and the typed phrases."""

from __future__ import annotations

import textwrap

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from ..gpu_flow import Before
from ..gpu_setup import CONFIRM_PHRASE
from ..nullmoth import ACK_LABEL, ACK_PHRASE
from ..widgets.chrome import KeyBar

__all__ = ["BeforeDialog"]


class NoticeScroll(VerticalScroll):
    """The notice text. It tells the dialog when it moved, so the "N more lines" hint stays right."""

    class Moved(Message):
        pass

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        self.post_message(self.Moved())


class BeforeDialog(ModalScreen):
    """Hard warnings first, then the notice in a scrolling area, then the phrases. Dismisses with
    {"typed": bool, "ack": bool} on "Set it up" and with None on cancel."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "read(-1)", "Read on", show=False),
        Binding("down", "read(1)", "Read on", show=False),
        Binding("pageup", "read(-5)", "Read on", show=False),
        Binding("pagedown", "read(5)", "Read on", show=False),
    ]

    def __init__(self, before: Before) -> None:
        super().__init__()
        self.before = before

    def compose(self) -> ComposeResult:
        pal = self.app.get_css_variables()
        b = self.before
        with Vertical(id="before-box", classes="dialog warn"):
            with Horizontal(id="before-head"):
                yield Static("Before you continue", classes="dialog-title")
                yield Static("asked once, never again", classes="muted before-once")
            yield Static("", classes="blank")
            if b.warnings:
                yield Static(Text("\n".join(self._bang(line, self._inner()) for line in b.warnings),
                                  style=pal["warning"]), id="before-warnings", classes="before-block")
            for note in b.notes:
                yield Static(Text(note, style=pal["muted"]), classes="before-block before-note")
            if b.notice_body:
                yield Static(Text(b.notice_title, style="bold"), id="before-notice-title")
                lead = b.notice_lead + "\n\n" if b.notice_lead else ""
                yield NoticeScroll(Static(Text(lead + b.notice_body, style=pal["muted"]), id="before-notice-text"),
                                   id="before-notice")
                yield Static("", id="before-more")
            if b.phrase:
                yield self._phrase_row("g-confirm", f"Type {CONFIRM_PHRASE} to continue")
            if b.nvidia:
                yield Static(Text(ACK_LABEL, style=pal["muted"]), id="before-ack-label")
                yield self._phrase_row("g-ack", f"Type {ACK_PHRASE} to confirm")
            yield Static("", classes="blank")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="before-cancel", classes="secondary")
                yield Button("Set it up", id="before-go", classes="primary", disabled=self._locked())
        yield KeyBar([("↑↓", "Read on", None), ("tab", "Next field", None), ("enter", "Set it up", None),
                      ("esc", "Cancel", "screen.cancel")])

    def _inner(self) -> int:
        """Columns of text inside the dialog (it is 76 wide, or 96 percent of a narrow terminal)."""
        return max(30, min(76, int(self.app.size.width * 0.96)) - 8)

    @staticmethod
    def _bang(line: str, width: int) -> str:
        """A warning with a bang in front, wrapped with a hanging indent. "Warning: " is what the bang says."""
        text = line[len("Warning: "):] if line.startswith("Warning: ") else line
        return "\n".join(textwrap.wrap(text, width, initial_indent="! ", subsequent_indent="  "))

    @staticmethod
    def _phrase_row(input_id: str, label: str) -> Horizontal:
        return Horizontal(Static(label, classes="before-label"), Input(id=input_id, select_on_focus=False),
                          classes="before-phrase")

    def _locked(self) -> bool:
        """"Set it up" waits for every phrase this dialog asks for."""
        return self.before.phrase or self.before.nvidia

    def _typed(self) -> bool:
        return not self.before.phrase or self.query_one("#g-confirm", Input).value.strip() == CONFIRM_PHRASE

    def _acked(self) -> bool:
        return not self.before.nvidia or self.query_one("#g-ack", Input).value.strip() == ACK_PHRASE

    def on_mount(self) -> None:
        self.query_one("#before-box").set_class(self.app.size.height < 28, "tight")   # no blank rows when short
        self.call_after_refresh(self._fit_notice)
        self.call_after_refresh(self._more)
        field = self.query("Input")
        (field.first() if field else self.query_one("#before-go", Button)).focus()

    def _fit_notice(self) -> None:
        """The notice scrolls in whatever height is left, so the buttons never go off the bottom of the screen."""
        if not self.before.notice_body:
            return
        box = self.query_one("#before-box")
        notice = self.query_one("#before-notice")
        room = int((self.app.size.height - 1) * 0.9) - 4                # less the key bar, the border and the padding
        others = sum(c.outer_size.height + c.styles.margin.height for c in box.children if c is not notice)
        notice.styles.height = max(3, min(12, room - others))

    def _more(self) -> None:
        """"down arrow N more lines" while the notice has text below the visible part."""
        try:
            scroll = self.query_one("#before-notice", NoticeScroll)
        except Exception:  # noqa: BLE001 - no notice in this dialog
            return
        hidden = max(0, int(scroll.virtual_size.height - scroll.size.height - scroll.scroll_y))
        pal = self.app.get_css_variables()
        text = Text(f"↓ {hidden} more line{'s' if hidden != 1 else ''}", style=pal["accent"]) if hidden else Text("")
        self.query_one("#before-more", Static).update(text)

    def on_notice_scroll_moved(self, event: NoticeScroll.Moved) -> None:
        self._more()

    def on_resize(self) -> None:
        self.query_one("#before-box").set_class(self.app.size.height < 28, "tight")
        self.call_after_refresh(self._fit_notice)
        self.call_after_refresh(self._more)

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#before-go", Button).disabled = not (self._typed() and self._acked())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_go()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_go() if event.button.id == "before-go" else self.action_cancel()

    def action_read(self, delta: int) -> None:
        if self.before.notice_body:
            self.query_one("#before-notice", NoticeScroll).scroll_relative(y=delta, animate=False)

    def action_go(self) -> None:
        if self._typed() and self._acked():
            self.dismiss({"typed": self.before.phrase, "ack": self.before.nvidia})

    def action_cancel(self) -> None:
        self.dismiss(None)
