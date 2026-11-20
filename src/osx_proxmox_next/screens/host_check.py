"""Host check: what is wrong with this host, why it matters, and a fix the app can run."""

from __future__ import annotations

import logging

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, RichLog, Static

from ..preflight import PreflightCheck, _is_root, run_preflight
from ..services.fix_service import collect_fixes, run_fix_commands
from .base import ConfirmDialog, OsxScreen

log = logging.getLogger(__name__)

__all__ = ["HostCheckScreen", "PassedSummary", "check_title", "short_name", "fix_label"]

NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def check_title(check: PreflightCheck) -> str:
    """The headline of a failing check."""
    return check.problem


def short_name(check: PreflightCheck) -> str:
    """The short form used in the one-line list of passed checks."""
    name = check.name
    for suffix in (" available", " present", " enabled"):
        name = name.removesuffix(suffix) if hasattr(name, "removesuffix") else (
            name[: -len(suffix)] if name.endswith(suffix) else name)
    return "root" if name == "Root privileges" else name


def fix_label(checks: list[PreflightCheck]) -> str:
    """`Fix it`, `Fix both` or `Fix N`: how many problems one press repairs."""
    count = sum(1 for c in checks if not c.ok and c.fix)
    if count == 1:
        return "Fix it"
    if count == 2:
        return "Fix both"
    return f"Fix {count}"


class PassedSummary(Widget, can_focus=True):
    """`✓ 12 passed  qm · pvesm ...`: enter or a click lists every passed check with its detail."""

    BINDINGS = [Binding("enter,space", "toggle", "Show", show=False)]
    DEFAULT_CSS = "PassedSummary { height: auto; min-height: 1; }"

    def __init__(self, checks: list[PreflightCheck]) -> None:
        super().__init__(id="passed")
        self.checks = checks
        self.open = False

    def action_toggle(self) -> None:
        self.open = not self.open
        self.refresh(layout=True)

    def on_click(self) -> None:
        self.focus()
        self.action_toggle()

    def _name_lines(self, width: int) -> list[str]:
        """The short names of the passed checks, wrapped to *width* columns."""
        lines: list[str] = [""]
        for check in self.checks:
            name = short_name(check)
            piece = name if lines[-1] == "" else " \u00b7 " + name
            if lines[-1] and len(lines[-1]) + len(piece) > width:
                lines.append(name)
            else:
                lines[-1] += piece
        return lines

    def _head_width(self) -> int:
        return len(f"\u2713 {len(self.checks)} passed  ")

    def render(self) -> Text:
        pal = self.app.get_css_variables()
        text = Text()
        text.append("\u2713 ", style=pal["success"])
        text.append(f"{len(self.checks)} passed", style="bold")
        if not self.open:
            head = self._head_width()
            lines = self._name_lines(max(10, (self.size.width or 80) - head))
            text.append("  " + lines[0], style=pal["muted"])
            for line in lines[1:]:
                text.append("\n" + " " * head + line, style=pal["muted"])
            return text
        for check in self.checks:
            text.append(f"\n  \u2713 {check.name}", style=pal["muted"])
            text.append(f"  {check.details}", style=pal["muted"])
        return text

    def get_content_height(self, container, viewport, width) -> int:
        if self.open:
            return 1 + len(self.checks)
        return len(self._name_lines(max(10, width - self._head_width())))


class HostCheckScreen(OsxScreen):
    CRUMB = "Host check"
    BINDINGS = [
        Binding("f", "fix", "Fix"),
        Binding("r", "recheck", "Check again"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.running = False
        self.fix_ok: bool | None = None

    # ── layout ───────────────────────────────────────────────────────

    def compose_body(self) -> ComposeResult:
        with Vertical(id="hc"):
            with Horizontal(id="hc-head"):
                yield Static("", id="hc-title")
                yield Static("", id="hc-count")
            yield VerticalScroll(id="hc-body")
            yield RichLog(id="fix-log", wrap=True, highlight=False, markup=False, classes="hidden")

    def on_mount(self) -> None:
        checks = self.app.host_checks  # type: ignore[attr-defined]
        if checks is None:
            self.action_recheck()
        else:
            self.run_worker(self._show(checks), exclusive=True, group="hc-show")

    def checks(self) -> list[PreflightCheck]:
        return self.app.host_checks or []  # type: ignore[attr-defined]

    async def _show(self, checks: list[PreflightCheck]) -> None:
        pal = self.app.get_css_variables()
        bad = [c for c in checks if not c.ok]
        good = [c for c in checks if c.ok]
        title = self.query_one("#hc-title", Static)
        if bad:
            fixable = sum(1 for c in bad if c.fix)
            if len(bad) == fixable and len(bad) in NUMBER_WORDS:
                what = f"This host needs {NUMBER_WORDS[len(bad)]} small fix{'es' if len(bad) > 1 else ''}"
            else:
                what = f"This host has {len(bad)} problem{'s' if len(bad) != 1 else ''}"
            title.update(Text(what, style="bold"))
        else:
            title.update(Text("This host is ready", style="bold"))
        count = f"{len(good)} {'other ' if bad else ''}checks passed"
        self.query_one("#hc-count", Static).update(Text(count, style=pal["muted"]))
        body = self.query_one("#hc-body", VerticalScroll)
        await body.remove_children()
        widgets: list[Widget] = []
        for check in bad:
            head = Text()
            head.append("✗ ", style=pal["error"])
            head.append(check_title(check), style="bold")
            widgets.append(Static(head, classes="hc-item"))
            widgets.append(Static(Text(check.why or check.details, style=pal["muted"]), classes="hc-why"))
            if check.fix:
                widgets.append(Static(Text("\n".join(c.display for c in check.fix)), classes="hc-code"))
            elif check.why and check.details and check.details != check.why:
                widgets.append(Static(Text(check.details, style=pal["muted"]), classes="hc-why"))
            widgets.append(Static("", classes="blank"))
        fixes = collect_fixes(checks)
        if fixes:
            widgets.append(self._fix_row(checks, len(fixes)))
            widgets.append(Static("", classes="blank"))
        if good:
            widgets.append(PassedSummary(good))
        await body.mount(*widgets)
        self.refresh_footer()
        self.refresh_topbar()
        if fixes:
            try:
                self.query_one("#hc-fix", Button).focus()
            except Exception:  # noqa: BLE001
                pass

    def _fix_row(self, checks: list[PreflightCheck], commands: int) -> Widget:
        pal = self.app.get_css_variables()
        note = (f"These {commands} commands run as root, exactly as shown." if commands != 1
                else "This command runs as root, exactly as shown.")
        return Horizontal(
            Static(Text(note, style=pal["muted"]), classes="hc-fixnote"),
            Button("Not now", id="hc-notnow", classes="secondary"),
            Button(fix_label(checks), id="hc-fix", classes="primary"),
            classes="hc-fixrow")

    # ── actions ──────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "hc-fix":
            self.action_fix()
        else:
            self.action_back()

    def busy(self) -> bool:
        return self.running

    def action_back(self) -> None:
        if self.running:
            return
        super().action_back()

    def action_recheck(self) -> None:
        if self.running:
            return
        self.app.rerun_preflight()  # type: ignore[attr-defined]
        self.run_worker(self._recheck_work, thread=True, exclusive=True, group="hc-recheck", exit_on_error=False)

    def _recheck_work(self) -> None:
        checks = run_preflight()
        self.app.call_from_thread(self._rechecked, checks)

    async def _rechecked(self, checks: list[PreflightCheck]) -> None:
        self.app.set_host_checks(checks)  # type: ignore[attr-defined]
        await self._show(checks)

    def action_fix(self) -> None:
        checks = self.checks()
        fixes = collect_fixes(checks)
        if self.running or not fixes:
            return
        if not _is_root():
            self.app.notify("Fixing needs root. Start osx-next as root.", severity="warning")
            return
        count = len(fixes)
        label = fix_label(checks)

        def answer(result: str | None) -> None:
            if result == "yes":
                self._start_fix(fixes)

        self.app.push_screen(ConfirmDialog(
            f"Run {count} command{'s' if count != 1 else ''} as root?",
            [c.display for c in fixes], confirm=label, mono=True), answer)

    def _start_fix(self, fixes) -> None:
        self.running = True
        log_widget = self.query_one("#fix-log", RichLog)
        log_widget.remove_class("hidden")
        log_widget.clear()
        self.run_worker(lambda: self._fix_work(fixes), thread=True, exit_on_error=False, group="hc-fix")

    def _line(self, text: str) -> None:
        self.query_one("#fix-log", RichLog).write(text)

    def _fix_work(self, fixes) -> None:
        ok = run_fix_commands(fixes, lambda line: self.app.call_from_thread(self._line, line))
        checks = run_preflight()
        self.app.call_from_thread(self._fix_done, ok, checks)

    async def _fix_done(self, ok: bool, checks: list[PreflightCheck]) -> None:
        self.running = False
        self.fix_ok = ok
        self.app.set_host_checks(checks)  # type: ignore[attr-defined]
        self._line("Done. Checking the host again." if ok else "Stopped. Checking the host again.")
        await self._show(checks)
        remaining = [c for c in checks if not c.ok]
        self.app.notify("The host is ready." if not remaining else f"{len(remaining)} problem(s) remain.",
                        severity="information" if not remaining else "warning")

    def footer_items(self) -> list[tuple]:
        checks = self.checks()
        items: list[tuple] = []
        if collect_fixes(checks):
            items.append(("f", fix_label(checks), "screen.fix"))
        items += [("r", "Check again", "screen.recheck"), ("esc", "Back", "screen.back"), ("?", "Help", "app.help")]
        return items
