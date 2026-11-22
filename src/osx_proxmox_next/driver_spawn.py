"""Start `osx-next-cli driver-update` as a detached process that outlives the terminal; the screen reads its progress
from the update state file and the lock, so a reopened TUI shows the same steps."""

from __future__ import annotations

import re
import subprocess
import sys

from .driver_targets import Choice

__all__ = ["log_path", "spawn_update"]

TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
SNAPSHOT_NAME = re.compile(r"^osx-next-driver-\d{8}-\d{4}(?:\d{2})?\Z")


def log_path(vmid: int) -> str:
    return f"/var/log/osx-next-driver-{int(vmid)}.log"


def spawn_update(vmid: int, choice: Choice, finish: bool = False, accept_risk: bool = False,
                 no_snapshot: bool = False, snapshot: str = "", popen=subprocess.Popen) -> str:
    """Run the update with every question already answered in the TUI. Returns the log path. Release tags are checked
    before they become arguments. *snapshot* is the name the screen showed: the run uses exactly that one."""
    argv = [sys.executable, "-m", "osx_proxmox_next.cli", "driver-update", "--vmid", str(int(vmid)), "--yes",
            "--ask-first-boot"]
    if snapshot:
        if not SNAPSHOT_NAME.match(snapshot):
            raise ValueError(f"{snapshot!r} is not a snapshot name of this tool")
        argv += ["--snapshot-name", snapshot]
    for flag, value in (("--version", choice.nullmoth), ("--wex-version", choice.wex),
                        ("--graphics-version", choice.graphics), ("--macos", choice.macos)):
        if value:
            if not TAG.match(value):
                raise ValueError(f"{value!r} is not a release tag")
            argv += [flag, value]
    argv += [f for f, on in (("--finish", finish), ("--accept-nullmoth-risk", accept_risk),
                             ("--no-snapshot", no_snapshot), ("--reinstall", True)) if on]
    path = log_path(vmid)
    with open(path, "wb") as log_file:
        popen(argv, stdout=log_file, stderr=log_file, stdin=subprocess.DEVNULL, start_new_session=True)
    return path
