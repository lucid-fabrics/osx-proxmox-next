"""Start, stop, edit and remove: the actions Home and the VM view share."""

from __future__ import annotations

import logging

from ..services import VmRecord, get_proxmox_adapter
from .base import OsxScreen
from .dialogs import EditDialog, RemoveDialog, StopDialog

log = logging.getLogger(__name__)

__all__ = ["VmActionScreen"]


class VmActionScreen(OsxScreen):
    """A screen with one Mac in focus and the keys that act on it."""

    def current_record(self) -> VmRecord | None:
        raise NotImplementedError

    def after_change(self, removed: bool = False) -> None:
        """Called when an action finished: reload what the screen shows."""

    # ── start / stop ─────────────────────────────────────────────────

    def action_toggle_power(self) -> None:
        record = self.current_record()
        if record is None:
            return
        if record.status != "running":
            self._qm(record, "start", f"Starting {record.name}")
            return

        def chosen(choice: str | None) -> None:
            if choice in ("shutdown", "stop"):
                self._qm(record, choice, f"{'Shutting down' if choice == 'shutdown' else 'Stopping'} {record.name}")

        self.app.push_screen(StopDialog(record), chosen)

    def _qm(self, record: VmRecord, command: str, message: str) -> None:
        self.app.notify(message + "...")

        def work() -> None:
            result = get_proxmox_adapter().qm(command, str(record.vmid))
            self.app.call_from_thread(self._qm_done, record, command, result.ok, result.output)

        self.run_worker(work, thread=True, exit_on_error=False)

    def _qm_done(self, record: VmRecord, command: str, ok: bool, output: str) -> None:
        if ok:
            self.app.notify(f"{record.name}: {command} done")
        else:
            self.app.notify(f"{record.name}: {command} failed. {output[:120]}", severity="error")
        self.after_change()

    # ── edit / remove / gpu ──────────────────────────────────────────

    def action_edit(self) -> None:
        record = self.current_record()
        if record is not None:
            self.app.push_screen(EditDialog(record), lambda _result: self.after_change())

    def action_remove(self) -> None:
        record = self.current_record()
        if record is not None:
            self.app.push_screen(RemoveDialog(record), lambda done: self.after_change(removed=bool(done)))

    def action_gpu(self) -> None:
        record = self.current_record()
        self.app.action_gpu(record.vmid if record else None)
