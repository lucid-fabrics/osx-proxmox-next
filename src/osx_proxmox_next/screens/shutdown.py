"""Remembering which Macs were asked to shut down, so a Mac that ignores the request is not left unexplained."""

from __future__ import annotations

import time
from collections.abc import Callable

from ..services import VmRecord

__all__ = ["STILL_RUNNING_SECONDS", "ShutdownTracker", "shutdown_timed_out", "still_running_line"]

STILL_RUNNING_SECONDS = 60


def still_running_line(name: str) -> str:
    return f"{name} is still running. macOS may be waiting on a dialog. s offers Force stop."


def shutdown_timed_out(output: str) -> bool:
    """True when `qm shutdown` gave up waiting for the guest ("VM quit/powerdown failed - got timeout"), as opposed
    to failing for a real reason. A lock timeout means nothing was requested, so it is not this."""
    text = output.lower()
    return ("timeout" in text or "timed out" in text) and "lock" not in text


class ShutdownTracker:
    """When each Mac was asked to shut down. macOS often waits on a dialog, or ignores the request without the
    guest agent, so after 60 seconds the screens say so and offer Force stop. Nothing is forced by itself."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.asked: dict[int, float] = {}
        self.forcing: set[int] = set()      # Macs with a Force stop under way

    def ask(self, vmid: int) -> None:
        self.asked.setdefault(vmid, self.clock())   # asking again does not restart the 60 seconds

    def forget(self, vmid: int) -> None:
        self.asked.pop(vmid, None)

    def state(self, record: VmRecord) -> str:
        """"" (not asked, or already off), "shutting" (asked a moment ago) or "stuck" (still running after 60 s)."""
        asked = self.asked.get(record.vmid)
        if asked is None:
            return ""
        if record.status != "running":
            self.forget(record.vmid)
            return ""
        return "stuck" if self.clock() - asked >= STILL_RUNNING_SECONDS else "shutting"
