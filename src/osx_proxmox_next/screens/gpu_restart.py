"""Stage 5 of the graphics card flow: the one restart. The restart box is the confirmation, nothing opens after it."""

from __future__ import annotations

import time
from typing import Any

from ..services.unattended_service import read_driver_state
from .gpu_contract import GpuHost

ARM_SECONDS = 1.0   # enter typed ahead of the box must never restart the host: it is ignored this long


def _now() -> float:
    return time.monotonic()


class RestartMixin(GpuHost):
    """The restart box of GpuScreen: what a restart does right now, Later and Restart now."""

    def step_restart(self) -> None:
        s = self.s
        if not s.state.get("reboot_needed"):
            s.complete(5, skipped=True)
            self.drive()
            return

        def work() -> Any:
            if not s.reboot_pending():
                return None
            s.state.setdefault("restart_at", time.time())
            s.pause()
            running = s.running_vms()
            installing = []
            for vmid, name in running:
                state = read_driver_state(vmid)
                if state.installing:
                    minutes = int(max(0, time.time() - state.started_at) // 60) if state.started_at else 0
                    installing.append((vmid, name, minutes))
            return running, installing, self._starters(), s.running_containers()

        self.job(work, self._restart_ready)

    def _starters(self) -> list[tuple[int, str]]:
        """Other VMs that hold the card and start with the host: after the restart one of them takes it first."""
        try:
            return [(h.vmid, h.name) for h in self.s.card_holders() if h.onboot]
        except Exception:  # noqa: BLE001 - the warning is extra: never stop the restart for it
            return []

    @staticmethod
    def restart_lines(running: list, installing: list, starters: list, containers: list = ()) -> list[str]:
        """The box text: what the restart does to every running VM, and which VM will take the card."""
        stuck = {vmid for vmid, _n, _m in installing}
        # two lines, so the line break never falls inside "3 minutes"
        lines = ["The host needs it to hand the card over.", "About 3 minutes.", "", "What a restart means right now"]
        lines += [f"● {name} (VM {vmid}) is shut down cleanly first" for vmid, name in running if vmid not in stuck]
        for vmid, name, minutes in installing:
            lines += [f"! {name} (VM {vmid}) is still installing, {minutes} min in",
                      "  A restart now interrupts that install."]
        lines += [f"● {name} (CT {ctid}) is shut down cleanly first" for ctid, name in containers]
        if not running and not containers:
            lines.append("No VM is running.")
        lines += [f"! {name} (VM {vmid}) starts by itself afterwards and takes the card. "
                  "Shut it down, then open osx-next." for vmid, name in starters]
        return lines

    def _restart_ready(self, result: Any) -> None:
        if result is None:
            self.drive()
            return
        running, installing, starters, containers = result
        self.waiting_for_restart = True
        self._restarting = False
        self._restart_armed = float("inf")      # nothing restarts until the box is on screen
        self.set_actions(None, None)
        lines = self.restart_lines(running, installing, starters, containers)
        self.run_worker(self._show_restart(lines), exclusive=True, group="gpu-mode")

    async def _show_restart(self, lines: list[str]) -> None:
        await self.show_run("Time for the one restart", lines,
                            [("g-later", "Later", "secondary"), ("g-restart", "Restart now", "primary")])
        self.call_after_refresh(self._arm_restart)

    def _arm_restart(self) -> None:
        """The box is mounted and drawn: only now can the user see it, so the guard and the keys start here."""
        self._restart_armed = _now() + ARM_SECONDS
        self.set_actions(self._restart_now, self._restart_later)

    def _restart_later(self) -> None:
        self.s.pause()
        self.app.notify("Saved. Your Macs page shows a reminder to finish.")
        self.app.pop_screen()

    def _restart_now(self) -> None:
        """The box is the confirmation: this restarts the host. Not in the first second, and only once."""
        if _now() < self._restart_armed or self._restarting:
            return
        self._restarting = True
        ok, out = self.s.reboot_host()
        self._restarting = ok
        self.say("Restarting..." if ok else "Could not start the restart: " + out, fail=not ok)
