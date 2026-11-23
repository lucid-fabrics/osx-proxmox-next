"""Build screen of the macOS 27 arm64 via Stela (beta) target: the whole install, one progress line at a time."""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import RichLog

from ..stela import STELA_LABEL, StelaConfig
from ..stela_cli import install_vm
from .base import OsxScreen

log = logging.getLogger(__name__)

__all__ = ["StelaBuildScreen"]


class StelaBuildScreen(OsxScreen):
    CRUMB = "Building"
    BINDINGS = [Binding("escape", "back", "Back to your Macs")]

    def __init__(self, config: StelaConfig) -> None:
        super().__init__()
        self.config = config
        self.finished = False
        self.ok = False

    def crumb(self) -> str:
        return self.config.name

    def compose_body(self) -> ComposeResult:
        yield RichLog(id="stela-log", wrap=True, highlight=False, markup=False)

    def footer_items(self) -> list[tuple]:
        return [("esc", "Back to your Macs", "screen.back"), ("?", "Help", "app.help")]

    def on_mount(self) -> None:
        self._write(f"Installing {STELA_LABEL} in VM {self.config.vmid} ({self.config.name}). It takes about an hour "
                    "and needs no input; the Mac's screen is in the Proxmox console.")
        self.run_worker(self._run, thread=True, exit_on_error=False, group="stela")

    def _write(self, line: str) -> None:
        self.query_one("#stela-log", RichLog).write(line)

    def _say(self, line: str) -> None:
        self.app.call_from_thread(self._write, line)

    def _run(self) -> None:
        try:
            self.ok = install_vm(self.config, self._say)
        except Exception as exc:  # noqa: BLE001 - shown to the user, not swallowed
            log.exception("stela install crashed")
            self._say(f"Unexpected error: {exc}")
        self.finished = True
        if self.ok:
            self._say(f"VM {self.config.vmid} is at Setup Assistant. Finish it in the Proxmox console.")
        else:
            self._say(f"Stopped. Fix the cause and run again, or remove the VM with: "
                      f"osx-next-cli stela uninstall --vmid {self.config.vmid} --execute")
