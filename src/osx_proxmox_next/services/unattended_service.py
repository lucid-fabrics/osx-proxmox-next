"""Start the hands-off install driver and read its progress back from its log.

The driver (`osx-next-cli install-unattended`) is a detached process that
outlives the TUI. Everything the Build screen shows about the Install macOS
phase comes from its log file, so a reopened TUI can rebuild the same view."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..unattended import PHASE_MARK, PHASES

log = logging.getLogger(__name__)

__all__ = [
    "LOG_DIR",
    "SUBSTEPS",
    "DriverState",
    "driver_log_path",
    "spawn_unattended_driver",
    "driver_alive",
    "read_driver_state",
    "parse_driver_log",
]

LOG_DIR = "/var/log"

# (phase id, label while running, label once done)
SUBSTEPS: tuple[tuple[str, str, str], ...] = (
    ("recovery", "Booting the recovery", "Booted the recovery"),
    ("erase", "Erasing the new disk", "Erased the new disk"),
    ("installer", "Starting the installer", "Started the installer"),
    ("restarts", "Installing, restarting", "Installed, restarted"),
    ("first_start", "First start", "First start done"),
    ("ready", "Ready at Setup Assistant", "Ready at Setup Assistant"),
)
assert tuple(s[0] for s in SUBSTEPS) == PHASES

RUN_BANNER = "BETA: unattended install for VM"
_EVENT_RE = re.compile(r"^\s+\[(\d\d:\d\d:\d\d)\]\s+(.*)$")


@dataclass
class DriverState:
    """What a driver log says right now."""
    seen: list[str] = field(default_factory=list)      # phase ids in order of appearance
    started_at: float = 0.0                            # epoch of the first marker, 0 = none yet
    events: list[tuple[str, str]] = field(default_factory=list)  # (HH:MM:SS, message), oldest first
    error: str = ""                                    # reason, when the driver reported a failure
    finished: bool = False                             # the install reached Setup Assistant
    alive: bool = False                                # the driver process is still running
    exists: bool = False                               # there is a log at all

    @property
    def current(self) -> str:
        return self.seen[-1] if self.seen else ""

    @property
    def installing(self) -> bool:
        """The driver is working on this VM right now."""
        return self.exists and self.alive and not self.finished and not self.error


def driver_log_path(vmid: int) -> str:
    return f"{LOG_DIR}/osx-next-unattended-{vmid}.log"


def spawn_unattended_driver(vmid: int, disk_gb: int, popen=subprocess.Popen) -> str:
    """Start install-unattended as a detached process that survives the TUI. Returns its log path."""
    log_path = driver_log_path(vmid)
    with open(log_path, "ab") as log_file:
        popen(
            [sys.executable, "-m", "osx_proxmox_next.cli", "install-unattended",
             "--vmid", str(vmid), "--disk-gb", str(disk_gb)],
            stdout=log_file, stderr=log_file,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    return log_path


def driver_alive(vmid: int, proc_root: str = "/proc") -> bool:
    """True when an install-unattended process for *vmid* is running (Linux /proc scan)."""
    needle = ["install-unattended", "--vmid", str(vmid)]
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return False
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            raw = Path(proc_root, entry, "cmdline").read_bytes()
        except OSError:
            continue
        args = raw.decode(errors="replace").split("\0")
        for idx in range(len(args) - len(needle) + 1):
            if args[idx:idx + len(needle)] == needle:
                return True
    return False


def parse_driver_log(text: str) -> DriverState:
    """Rebuild the driver state from the text of its log file."""
    state = DriverState(exists=True)
    lines = text.splitlines()
    # The log is opened for append: only the newest run counts.
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].startswith(RUN_BANNER):
            lines = lines[idx:]
            break
    for line in lines:
        if line.startswith(PHASE_MARK):
            parts = line[len(PHASE_MARK):].split()
            if not parts or parts[0] not in PHASES:
                continue
            phase = parts[0]
            if phase not in state.seen:
                state.seen.append(phase)
            if len(parts) > 1 and parts[1].isdigit() and not state.started_at:
                state.started_at = float(parts[1])
            if phase == "ready":
                state.finished = True
            continue
        if line.startswith("ERROR:"):
            state.error = line[len("ERROR:"):].strip()
            continue
        match = _EVENT_RE.match(line)
        if match:
            state.events.append((match.group(1), match.group(2)))
    return state


def read_driver_state(vmid: int, now: float | None = None) -> DriverState:
    """The driver state of *vmid*: log content plus whether the process is alive."""
    path = Path(driver_log_path(vmid))
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return DriverState()
    state = parse_driver_log(text)
    state.alive = driver_alive(vmid)
    if state.started_at == 0.0 and state.seen:
        try:
            state.started_at = path.stat().st_mtime
        except OSError:  # pragma: no cover - the file was just read
            state.started_at = now or time.time()
    return state
