"""Start, stop, edit and remove: the actions Home and the VM view share."""

from __future__ import annotations

import logging

from ..driver_plan import card_setup_waiting
from ..services import VmRecord, get_proxmox_adapter
from .base import OsxScreen
from .dialogs import EditDialog, RemoveDialog, StopDialog
from .shutdown import shutdown_timed_out

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
            if choice == "shutdown":
                if record.vmid in self.app.shutdowns.asked:    # a request is pending: Force stop is the only offer
                    self.app.push_screen(StopDialog(record, force_first=True), chosen)
                    return
                self.app.shutdowns.ask(record.vmid)        # recorded now: the card says "shutting down" at once
                self._qm(record, "shutdown", f"Shutting down {record.name}", owns_request=True)
                self.after_change()
            elif choice == "stop":
                self._force_stop(record)

        stuck = self.app.shutdowns.state(record) == "stuck"   # it ignored the request: Force stop is the offer
        self.app.push_screen(StopDialog(record, force_first=stuck), chosen)

    def _qm(self, record: VmRecord, command: str, message: str, owns_request: bool = False) -> None:
        self.app.notify(message + "...")

        def work() -> None:
            result = get_proxmox_adapter().qm(command, str(record.vmid))
            self.app.call_from_thread(self._qm_done, record, command, result.ok, result.output, owns_request)

        self.run_worker(work, thread=True, exit_on_error=False)

    def _qm_done(self, record: VmRecord, command: str, ok: bool, output: str, owns_request: bool = False) -> None:
        tracker = self.app.shutdowns
        if command == "shutdown" and not ok:
            if record.vmid not in tracker.asked or record.vmid in tracker.forcing:
                self.after_change()                        # a Force stop took over and ended the request
                return
            if shutdown_timed_out(output):                 # macOS ignored the request: the hint takes over
                self.app.notify(f"{record.name}: shutdown timed out", severity="warning")
                self.after_change()
                return
            if owns_request:                               # only the call that made the record may forget it
                tracker.forget(record.vmid)
        if ok:
            self.app.notify(f"{record.name}: {command} done")
        else:
            self.app.notify(f"{record.name}: {command} failed. {output[:120]}", severity="error")
        self.after_change()

    def _force_stop(self, record: VmRecord) -> None:
        """Cut the power, even while a `qm shutdown` that macOS ignores still holds the VM lock."""
        tracker = self.app.shutdowns
        pending = record.vmid in tracker.asked
        tracker.forcing.add(record.vmid)
        self.app.notify(f"Stopping {record.name}...")

        def work() -> None:
            vmid = str(record.vmid)
            ok, output = False, ""
            try:
                # the lock is taken: --overrule-shutdown goes first; an older Proxmox lacks the option, so plain stop follows
                tries = [("--overrule-shutdown", "1"), ()] if pending else [(), ("--overrule-shutdown", "1")]
                result = get_proxmox_adapter().qm("stop", vmid, *tries[0])
                if not result.ok:
                    result = get_proxmox_adapter().qm("stop", vmid, *tries[1])
                ok, output = result.ok, result.output
            except Exception as exc:  # noqa: BLE001 - reported below, never left marked as being forced
                log.debug("force stop raised", exc_info=True)
                output = f"{type(exc).__name__}: {exc}"
            finally:
                self.app.call_from_thread(self._force_stop_done, record, ok, output)

        self.run_worker(work, thread=True, exit_on_error=False)

    def _force_stop_done(self, record: VmRecord, ok: bool, output: str) -> None:
        self.app.shutdowns.forcing.discard(record.vmid)
        if ok:
            self.app.shutdowns.forget(record.vmid)
            self.app.notify(f"{record.name}: stop done")
        else:                                              # still running: the hint stays
            self.app.notify(f"{record.name}: stop failed. {output[:120]}", severity="error")
        self.after_change()

    # ── edit / remove / gpu ──────────────────────────────────────────

    def action_edit(self) -> None:
        record = self.current_record()
        if record is not None:
            self.app.push_screen(EditDialog(record), lambda _result: self.after_change())

    def action_remove(self) -> None:
        record = self.current_record()
        if record is not None:
            self.app.push_screen(RemoveDialog(record), lambda done: self._removed(record, bool(done)))

    def _removed(self, record: VmRecord, done: bool) -> None:
        if done:
            self.app.shutdowns.forget(record.vmid)         # the next Mac with this VM ID starts clean
            self.app.shutdowns.forcing.discard(record.vmid)
        self.after_change(removed=done)

    def action_gpu(self) -> None:
        record = self.current_record()
        if record is not None and record.facts.has_gpu and not card_setup_waiting(record.vmid):
            from .driver import DriverScreen     # a Mac with a card: its driver, not the choice of a card
            self.app.push_screen(DriverScreen(record.vmid, record.name))
            return
        self.app.action_gpu(record.vmid if record else None)
