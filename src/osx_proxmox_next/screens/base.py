"""Shared building blocks of every screen: the screen base class, the help overlay and the dialogs."""

from __future__ import annotations

import textwrap

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Static

from ..widgets.chrome import KeyBar, TopBar
from .reading import READ_BINDINGS, ReadScroll, Readable, box_room

__all__ = ["OsxScreen", "HelpScreen", "ChoiceDialog", "ConfirmDialog", "Choice"]

KeyItem = tuple  # (key label, description, action or None)

COMMON_HELP: list[tuple[str, str]] = [
    ("?  f1", "This help"),
    ("q", "Quit"),
    ("ctrl+p", "Command palette"),
    ("esc", "Go back or cancel"),
    ("tab  shift+tab", "Next or previous field"),
]


class OsxScreen(Screen):
    """A screen with the top bar, a body and the one-row key footer."""

    CRUMB = ""
    HELP_EXTRA: list[tuple[str, str]] = []   # keys that belong to a widget, not to a screen binding
    BINDINGS = [
        Binding("escape", "back", "Back", show=False),
    ]

    # ── layout ───────────────────────────────────────────────────────

    def crumb(self) -> str:
        return self.CRUMB

    def footer_items(self) -> list[KeyItem]:
        return []

    def compose_body(self) -> ComposeResult:
        yield from ()

    def compose(self) -> ComposeResult:
        yield TopBar(self.crumb(), id="topbar")
        yield from self.compose_body()
        yield KeyBar(self.footer_items(), id="keybar")

    def refresh_footer(self) -> None:
        try:
            self.query_one("#keybar", KeyBar).set_items(self.footer_items())
        except Exception:  # noqa: BLE001 - not composed yet
            pass

    def refresh_topbar(self) -> None:
        for bar in self.query(TopBar):
            bar.refresh()

    # ── behaviour every screen shares ────────────────────────────────

    def busy(self) -> bool:
        """True while an in-process job runs: quitting then needs a confirmation."""
        return False

    def action_back(self) -> None:
        if len(self.app.screen_stack) > 2:
            self.app.pop_screen()

    def help_entries(self) -> list[tuple[str, str]]:
        """(keys, what it does) for every key this screen defines, then the keys every screen has.

        Only the app's own bindings are listed (not Textual's focus and copy keys), and keys that do the
        same thing share one line."""
        grouped: list[list] = []
        for cls in type(self).__mro__:
            if not (isinstance(cls, type) and issubclass(cls, OsxScreen)):
                continue
            for entry in cls.__dict__.get("BINDINGS", []):
                keys, desc = _binding_keys(entry)
                if not desc:
                    continue
                known = next((g for g in grouped if g[1] == desc), None)
                if known is not None:
                    known[0] += [k for k in keys if k not in known[0]]
                elif not any(k in g[0] for g in grouped for k in keys):
                    grouped.append([list(keys), desc])
        entries = [("  ".join(_KEY_NAMES.get(k, k) for k in keys), desc) for keys, desc in grouped]
        entries += list(self.HELP_EXTRA)
        shown = {k for k, _d in entries}
        return entries + [(k, d) for k, d in COMMON_HELP if k not in shown]


_KEY_NAMES = {"left": "\u2190", "right": "\u2192", "up": "\u2191", "down": "\u2193", "escape": "esc",
              "question_mark": "?"}


def _binding_keys(entry) -> tuple[list[str], str]:
    """(keys, description) of a BINDINGS entry, which is a Binding or a (key, action, description) tuple."""
    if isinstance(entry, Binding):
        return entry.key.split(","), entry.description
    keys = entry[0].split(",")
    return keys, entry[2] if len(entry) > 2 else ""


MOVE_KEYS = {"\u2190", "\u2192", "\u2191", "\u2193", "h", "j", "k", "l", "tab", "shift+tab"}
KEY_COLUMN = 22          # width of the key column of the help overlay; a description wraps inside its own column
COMMON_KEYS = {k for k, _d in COMMON_HELP}


def help_groups(entries: list[tuple[str, str]]) -> list[tuple[str, list[tuple[str, str]]]]:
    """The keys of a screen in three groups: how to move, what this screen does, what works everywhere."""
    groups: dict[str, list[tuple[str, str]]] = {"Move": [], "This screen": [], "Everywhere": []}
    for keys, what in entries:
        tokens = keys.split()
        if keys in COMMON_KEYS:
            groups["Everywhere"].append((keys, what))
        elif tokens and all(t in MOVE_KEYS for t in tokens) and (len(tokens) > 1 or tokens[0] not in {"h", "j", "k", "l"}):
            groups["Move"].append((keys, what))
        else:
            groups["This screen"].append((keys, what))
    return [(name, rows) for name, rows in groups.items() if rows]


class HelpScreen(Readable, ModalScreen):
    """Every key of the screen underneath. It scrolls when the keys are taller than the terminal."""

    read_scroll = "help-scroll"
    read_more = "help-more"
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("question_mark", "close", "Close"),
        Binding("f1", "close", "Close"),
        Binding("enter", "close", "Close"),
        *READ_BINDINGS,
    ]

    def __init__(self, title: str, entries: list[tuple[str, str]]) -> None:
        super().__init__()
        self.title_text = title
        self.entries = entries

    def _keys_text(self, width: int) -> Text:
        """Group headings, then one row per key: the key in a 22 column, its description wrapped in its own."""
        pal = self.app.get_css_variables()
        text = Text()
        for idx, (name, rows) in enumerate(help_groups(self.entries)):
            text.append(("\n\n" if idx else "") + name, style="bold " + pal["muted"])
            for keys, what in rows:
                lines = textwrap.wrap(what, max(10, width - KEY_COLUMN)) or [""]
                for n, line in enumerate(lines):
                    text.append("\n")
                    text.append(keys.ljust(KEY_COLUMN) if n == 0 else " " * KEY_COLUMN,
                                style="bold " + pal["accent"])
                    text.append(line)
        return text

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box", classes="dialog"):
            yield Static(f"Keys on {self.title_text}", classes="dialog-title")
            yield Static("", classes="blank")
            with ReadScroll(id="help-scroll"):
                yield Static(self._keys_text(self._inner()), id="help-keys")
            yield Static("", id="help-more", classes="dialog-more")
            yield Static("", classes="blank")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="close", classes="secondary")
        yield KeyBar([("↑↓", "Read on", None), ("esc", "Close", "screen.close")])

    def read_room(self) -> int:
        return box_room(self.app.size.height, self.query_one("#help-box"))

    def _inner(self) -> int:
        return max(40, min(66, int(self.app.size.width * 0.96)) - 8)

    def on_mount(self) -> None:
        self.query_one("#close", Button).focus()
        self.read_refit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()

    def action_close(self) -> None:
        self.dismiss()


class Choice:
    """One button of a ChoiceDialog."""

    def __init__(self, value: str, label: str, kind: str = "secondary") -> None:
        self.value = value
        self.label = label
        self.kind = kind  # "primary" | "danger" | "secondary"


class ChoiceDialog(Readable, ModalScreen[str]):
    """A title, a few lines and a row of buttons. Dismisses with the chosen value, "" on esc. Lines taller than
    the terminal scroll."""

    read_scroll = "choice-scroll"
    read_more = "choice-more"
    BINDINGS = [Binding("escape", "cancel", "Cancel"), *READ_BINDINGS]

    def __init__(self, title: str, lines: list[str], choices: list[Choice], focus: str = "",
                 danger: bool = False, mono: bool = False) -> None:
        super().__init__()
        self.title_text = title
        self.lines = lines
        self.choices = choices
        self.focus_value = focus or choices[0].value
        self.danger = danger
        self.mono = mono

    def compose(self) -> ComposeResult:
        with Vertical(id="choice-box", classes="dialog danger" if self.danger else "dialog"):
            yield Static(self.title_text, classes="dialog-title")
            yield Static("", classes="blank")
            with ReadScroll(id="choice-scroll"):
                for line in self.lines:
                    yield Static(Text(line), classes="dialog-line mono" if self.mono else "dialog-line")
            yield Static("", id="choice-more", classes="dialog-more")
            yield Static("", classes="blank")
            with Horizontal(classes="dialog-buttons"):
                for choice in self.choices:
                    yield Button(choice.label, id=f"choice-{choice.value}", classes=choice.kind, name=choice.value)
        yield KeyBar([("tab", "Next", None), ("enter", "Choose", None), ("esc", "Cancel", "screen.cancel")])

    def read_room(self) -> int:
        return box_room(self.app.size.height, self.query_one("#choice-box"))

    def on_mount(self) -> None:
        self.query_one(f"#choice-{self.focus_value}", Button).focus()
        self.read_refit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.name or "")

    def action_cancel(self) -> None:
        self.dismiss("")


def ConfirmDialog(title: str, lines: list[str], confirm: str = "Confirm", danger: bool = False,
                  mono: bool = False) -> ChoiceDialog:
    """Cancel (focused) and a confirm button. Dismisses with "yes" or "".

    Cancel has the initial focus so a stray enter never confirms."""
    kind = "danger" if danger else "primary"
    return ChoiceDialog(title, lines, [Choice("no", "Cancel"), Choice("yes", confirm, kind)],
                        focus="no", danger=danger, mono=mono)
