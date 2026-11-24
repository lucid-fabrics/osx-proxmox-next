"""Steps that the iGPU finish (igpu_finish.py) and the NVIDIA finish (nullmoth_finish.py) both run the same way: reading the VM,
the storage of its disks, the config snapshot, the recovery image, starting the VM, waiting for it to stop and dropping the payload
disk. What only looks alike stays in the two files."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

from . import driver_disks
from .assets import resolve_recovery_or_installer_path
from .gpu import parse_config
from .infrastructure import ProxmoxAdapter
from .rollback import create_snapshot
from .services import fetch_vm_info

__all__ = ["FinishError", "note_picker_restore", "recovery_image", "save_snapshot", "start_vm", "vm_config", "wait_stopped"]

POLL = 10


class FinishError(Exception):
    """A phase of a graphics finish (iGPU or NVIDIA) did not reach its expected state."""


def vm_config(vmid: int, pve: ProxmoxAdapter):
    """(VM info, its parsed config). Raises FinishError when the VM does not exist."""
    info = fetch_vm_info(vmid, adapter=pve)
    if info is None:
        raise FinishError(f"VM {vmid} not found.")
    return info, parse_config(info.config_raw)


def require_stopped(vmid: int, info) -> None:
    if info.status != "stopped":
        raise FinishError(f"VM {vmid} is running. Stop it first: qm stop {vmid}")


def _storage_of(cfg: dict[str, str]) -> str:
    for key in ("virtio0", "efidisk0", "ide0"):
        m = re.match(r"([^:,]+):", cfg.get(key, ""))
        if m:
            return m.group(1)
    raise FinishError("Could not tell which storage the VM disks are on.")


def _drop_disk(pve: ProxmoxAdapter, vmid: int, slot: str, strict: bool = False) -> None:
    """Detach *slot* and delete the volume it held (the unused entry it leaves behind). The partition maps and loop
    devices that hold the volume go first; when one remains the volume could not be removed: *strict* raises
    FinishError with the command, else it is left as an unused disk (this runs while another error is being handled)."""
    cfg = parse_config(pve.qm("config", str(vmid)).output)
    ref = cfg.get(slot, "").split(",", 1)[0]
    if not ref:
        return
    try:
        driver_disks.release_maps(pve, vmid, driver_disks.volume_paths(pve, [ref]))
    except driver_disks.MapsHeld as exc:
        if strict:
            raise FinishError(str(exc)) from exc
    pve.qm("set", str(vmid), "--delete", slot)
    cfg = parse_config(pve.qm("config", str(vmid)).output)
    for key, value in cfg.items():
        if re.fullmatch(r"unused\d+", key) and value.split(",", 1)[0] == ref:
            pve.qm("set", str(vmid), "--delete", key)


def wait_stopped(pve: ProxmoxAdapter, vmid: int, timeout: float, clock: Callable[[], float],
                  sleep: Callable[[float], None]) -> bool:
    deadline = clock() + timeout
    while clock() < deadline:
        if "stopped" in pve.qm("status", str(vmid)).output:
            return True
        sleep(POLL)
    return False


def save_snapshot(vmid: int, pve: ProxmoxAdapter, on_event: Callable[[str], None]) -> None:
    snapshot = create_snapshot(vmid, adapter=pve)
    on_event(f"Saved the VM config to {snapshot.path}")


def recovery_image(version: str, iso: Path, on_event: Callable[[str], None]) -> Path:
    """The macOS recovery image for *version*: the one on disk, else downloaded."""
    recovery = resolve_recovery_or_installer_path(SimpleNamespace(installer_path="", macos=version))  # type: ignore[arg-type]
    if not recovery.exists():
        from .downloader import download_recovery
        on_event(f"Downloading the {version} recovery image")
        recovery = download_recovery(version, iso)
    return recovery


def note_picker_restore(console, notes: list[str]) -> None:
    """Put the picker's auto-boot back after the staging; a failure is a note, not an error."""
    err = console.restore_picker_timeout()
    if err:
        notes.append(f"Could not restore the picker's auto-boot ({err}); run post-install afterwards.")


def start_vm(pve: ProxmoxAdapter, vmid: int) -> None:
    res = pve.qm("start", str(vmid))
    if not res.ok:
        raise FinishError(f"Could not start VM {vmid}: {res.output.strip()[-300:]}")
