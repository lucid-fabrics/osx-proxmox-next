"""Build screen of the macOS 27 arm64 (beta) target: the runner VM's steps, one line each, then where to look next."""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import RichLog

from ..arm64 import Arm64Config
from ..arm64_cloudinit import LOG_PATH, STATUS_PATH
from ..arm64_planner import build_arm64_plan, host_key_path
from ..domain import PlanStep
from ..executor import StepResult, apply_plan
from .base import OsxScreen

log = logging.getLogger(__name__)

__all__ = ["Arm64BuildScreen"]


class Arm64BuildScreen(OsxScreen):
    CRUMB = "Building"
    BINDINGS = [Binding("escape", "back", "Back to your Macs")]

    def __init__(self, config: Arm64Config) -> None:
        super().__init__()
        self.config = config
        self.finished = False
        self.ok = False

    def crumb(self) -> str:
        return self.config.name

    def compose_body(self) -> ComposeResult:
        yield RichLog(id="arm64-log", wrap=True, highlight=False, markup=False)

    def footer_items(self) -> list[tuple]:
        return [("esc", "Back to your Macs", "screen.back"), ("?", "Help", "app.help")]

    def on_mount(self) -> None:
        self._write(f"Creating the runner VM {self.config.vmid} ({self.config.name}) for macOS 27 arm64 (beta)")
        self.run_worker(self._run, thread=True, exit_on_error=False, group="arm64")

    def _write(self, line: str) -> None:
        self.query_one("#arm64-log", RichLog).write(line)

    def _say(self, line: str) -> None:
        self.app.call_from_thread(self._write, line)

    def _on_step(self, idx: int, total: int, step: PlanStep, result: StepResult | None) -> None:
        if result is None:
            self._say(f"[{idx}/{total}] {step.title}")
        elif not result.ok:
            self._say(f"FAILED: {step.title}\n{result.output}")

    def _run(self) -> None:
        try:
            steps = build_arm64_plan(self.config)
            self.ok = apply_plan(steps, execute=True, on_step=self._on_step).ok
        except Exception as exc:  # noqa: BLE001 - shown to the user, not swallowed
            log.exception("arm64 runner create crashed")
            self._say(f"Unexpected error: {exc}")
        self.finished = True
        if self.ok:
            self._say(f"The runner VM {self.config.vmid} is starting. On its first boot it builds the beta, restores macOS,\n"
                      f"runs the offline firstboot pass and boots the desktop (the restore is the long part).\n"
                      f"Progress: {STATUS_PATH} inside the runner, log: {LOG_PATH}.\n"
                      f"Log in from this host: ssh -i {host_key_path(self.config.vmid)} snapintosh@<runner address> "
                      f"(the address is in the VM's Summary page).\n"
                      "The firstboot VNC tunnel and password are printed in the log when the desktop is ready.")
        else:
            self._say("Stopped. Nothing else was changed; fix the cause and run again, or remove the VM with: "
                      f"osx-next-cli arm64 uninstall --vmid {self.config.vmid} --execute")
