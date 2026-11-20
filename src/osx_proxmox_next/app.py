from __future__ import annotations

import logging
import socket
import sys
from pathlib import Path
from threading import Thread

from rich.text import Text
from textual.app import App
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.screen import ModalScreen

from . import __version__
from .preflight import PreflightCheck, run_preflight
from .screens.base import ConfirmDialog, HelpScreen
from .services import VmRecord
from .services.host_facts import HostFacts, load_host_facts
from .theme import OSX_NEXT, THEME_NAME

log = logging.getLogger(__name__)

__all__ = ["NextApp", "run"]


class OsxCommands(Provider):
    """The command palette: every place the app can go, and every Mac by name."""

    def _commands(self):
        app = self.app
        yield "New Mac", "Create a Mac VM", app.action_new_mac
        yield "Host check", "See what this host needs", app.action_host_check
        yield "GPU passthrough", "Give a Mac a graphics card", app.action_gpu
        for record in app.vm_records:
            yield (f"Open {record.name}", f"VM {record.vmid}",
                   lambda vmid=record.vmid: app.open_vm(vmid))

    async def discover(self) -> Hits:
        for name, help_text, callback in self._commands():
            yield DiscoveryHit(name, callback, help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, help_text, callback in self._commands():
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), callback, help=help_text)


class NextApp(App):
    CSS_PATH = Path(__file__).with_suffix(".tcss")
    TITLE = "osx-next"
    COMMANDS = App.COMMANDS | {OsxCommands}
    ENABLE_COMMAND_PALETTE = True

    BINDINGS = [
        Binding("q", "request_quit", "Quit", show=False),
        Binding("f1", "help", "Help", show=False, priority=True),
        Binding("question_mark", "help", "Help", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.host_checks: list[PreflightCheck] | None = None
        self.host_label = ""
        self.vm_records: list[VmRecord] = []
        self._facts: HostFacts | None = None
        # The stylesheet reads the theme's variables while it is parsed, which is before on_mount.
        self.register_theme(OSX_NEXT)
        self.theme = THEME_NAME

    # ── host state ───────────────────────────────────────────────────

    def on_mount(self) -> None:
        from .screens.home import HomeScreen
        self.push_screen(HomeScreen())
        self.rerun_preflight()
        Thread(target=self._warm_facts, daemon=True).start()

    def _warm_facts(self) -> None:
        try:
            self.facts()
        except Exception:  # noqa: BLE001 - a later facts() call reports it where it matters
            log.debug("host facts failed", exc_info=True)

    def facts(self) -> HostFacts:
        """The host facts, read once (the first caller pays for the pvesm calls)."""
        if self._facts is None:
            self._facts = load_host_facts()
        return self._facts

    def rerun_preflight(self) -> None:
        self.host_checks = None
        self._refresh_bars()
        Thread(target=self._preflight_worker, daemon=True).start()

    def _preflight_worker(self) -> None:
        checks = run_preflight()
        label = self._host_label()
        try:
            self.call_from_thread(self.set_host_checks, checks, label)
        except Exception:  # noqa: BLE001 - the app is closing
            log.debug("preflight result dropped", exc_info=True)

    @staticmethod
    def _host_label() -> str:
        try:
            return socket.gethostname()
        except OSError:  # pragma: no cover
            return ""

    def set_host_checks(self, checks: list[PreflightCheck], label: str = "") -> None:
        self.host_checks = checks
        if label:
            self.host_label = label
        self._refresh_bars()

    @property
    def failing_checks(self) -> list[PreflightCheck]:
        return [c for c in (self.host_checks or []) if not c.ok]

    def host_chip(self, pal: dict) -> Text:
        if self.host_checks is None:
            return Text("◌ checking host", style=pal["muted"])
        bad = self.failing_checks
        chip = Text()
        if bad:
            chip.append("● ", style=pal["error"])
            chip.append(f"{len(bad)} to fix", style=pal["error"])
        else:
            chip.append("● ", style=pal["success"])
            chip.append("host ready", style=pal["success"])
        if self.host_label:
            chip.append(" · " + self.host_label, style=pal["muted"])
        return chip

    def _refresh_bars(self) -> None:
        for screen in self.screen_stack:
            refresh = getattr(screen, "refresh_topbar", None)
            if refresh:
                try:
                    refresh()
                except Exception:  # noqa: BLE001 - screen not composed yet
                    pass

    # ── actions reachable from anywhere (and from the palette) ───────

    def action_help(self) -> None:
        screen = self.screen
        if isinstance(screen, HelpScreen):
            screen.dismiss()  # help is a toggle: it never stacks
            return
        entries = screen.help_entries() if hasattr(screen, "help_entries") else []
        title = getattr(screen, "HELP_TITLE", "") or getattr(screen, "CRUMB", "") or "this screen"
        self.push_screen(HelpScreen(title, entries))

    def action_request_quit(self) -> None:
        screen = self.screen
        if isinstance(screen, ModalScreen):
            return
        busy = getattr(screen, "busy", None)
        if busy and busy():
            def answer(result: str | None) -> None:
                if result == "yes":
                    self.exit()
            self.push_screen(ConfirmDialog(
                "Quit while this is running?",
                ["Stopping now can leave a half-done change on this host."],
                confirm="Quit anyway", danger=True), answer)
            return
        self.exit()

    def _on_home(self) -> bool:
        return not isinstance(self.screen, ModalScreen)

    def action_new_mac(self) -> None:
        if not self._on_home():
            return
        from .screens.new_mac import NewMacScreen
        self.push_screen(NewMacScreen())

    def action_host_check(self) -> None:
        if not self._on_home():
            return
        from .screens.host_check import HostCheckScreen
        self.push_screen(HostCheckScreen())

    def action_gpu(self, vmid: int | None = None) -> None:
        if not self._on_home():
            return
        from .screens.gpu import GpuScreen
        self.push_screen(GpuScreen(vmid=vmid))

    def open_vm(self, vmid: int) -> None:
        if not self._on_home():
            return
        from .screens.vm_view import open_vm_screen
        open_vm_screen(self, vmid)


def run() -> None:
    if "--version" in sys.argv[1:]:
        print(f"osx-next {__version__}")
        return
    NextApp().run()
