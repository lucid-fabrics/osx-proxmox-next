"""The driver snapshot of a Mac before a driver update: save, list, put back, remove. Constant argv lists only."""

from __future__ import annotations

import signal

from . import driver_disks, driver_engine as engine
from .gpu import parse_config
from .infrastructure import ProxmoxAdapter

__all__ = ["SnapshotError", "check_support", "create", "list_tool", "prune_older", "release_disks", "remove", "rollback"]


class SnapshotError(Exception):
    """A snapshot call failed."""


def check_support(pve: ProxmoxAdapter, cfg: dict[str, str]) -> tuple[bool, str]:
    """(ok, why not): can the storage of every disk of this Mac take a snapshot (asked from `pvesm status`)."""
    res = pve.run(["pvesm", "status"])
    return engine.snapshot_support(cfg, engine.parse_storage_types(res.output if res.ok else ""))


def create(pve: ProxmoxAdapter, vmid: int, version: str, name: str = "") -> str:
    name = name or engine.snapshot_name()
    res = _uninterruptible(lambda: pve.run(engine.snapshot_argv(vmid, name, version)))
    if not res.ok:
        raise SnapshotError("Could not save a snapshot of VM %d: %s" % (vmid, res.output.strip()[-300:]))
    return name


def list_tool(pve: ProxmoxAdapter, vmid: int) -> list[str]:
    """The snapshots this tool made for the Mac, oldest first. Snapshots of anyone else are never listed."""
    res = pve.run(["qm", "listsnapshot", str(vmid)])
    return engine.tool_snapshots(res.output) if res.ok else []


def _uninterruptible(call):
    """Run *call* with INT and TERM held back: `qm rollback`, `qm snapshot` and `qm delsnapshot` killed half way leave the
    Mac locked. The signal is raised once the call is done."""
    held: list[int] = []
    old = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            old[sig] = signal.signal(sig, lambda n, _f: held.append(n))
    except ValueError:      # not the main thread: nothing to hold back
        return call()
    try:
        return call()
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
        if held:
            raise KeyboardInterrupt


def release_disks(pve: ProxmoxAdapter, vmid: int) -> None:
    """Before Proxmox removes or rolls back a Mac's volumes: this tool's leftover loop devices and partition maps on them
    go, and none may remain (else the volume is "in use" and a rollback stops half way with its lock set)."""
    cfg = parse_config(pve.qm("config", str(vmid)).output)
    try:
        driver_disks.release_maps(pve, vmid, driver_disks.volume_paths(pve, driver_disks.disk_refs(cfg)))
    except driver_disks.MapsHeld as exc:
        raise SnapshotError(str(exc)) from exc


def rollback(pve: ProxmoxAdapter, vmid: int, name: str) -> None:
    release_disks(pve, vmid)
    res = _uninterruptible(lambda: pve.run(engine.rollback_argv(vmid, name)))
    if not res.ok:
        text = "Could not put VM %d back to %s: %s" % (vmid, name, res.output.strip()[-300:])
        if "lock: rollback" in pve.qm("config", str(vmid)).output:    # Proxmox leaves it set: say so, never silently
            text += " The rollback lock is still set, so the Mac stays stopped. Check its disks, then run: qm unlock %d" % vmid
        raise SnapshotError(text)


def remove(pve: ProxmoxAdapter, vmid: int, name: str) -> None:
    release_disks(pve, vmid)
    res = _uninterruptible(lambda: pve.run(engine.delete_snapshot_argv(vmid, name)))
    if not res.ok:
        raise SnapshotError("Could not remove the snapshot %s: %s" % (name, res.output.strip()[-300:]))


def prune_older(pve: ProxmoxAdapter, vmid: int, keep: str) -> tuple[list[str], list[str]]:
    """Remove every tool snapshot of the Mac except *keep*. Returns (removed, failed)."""
    removed: list[str] = []
    failed: list[str] = []
    for name in list_tool(pve, vmid):
        if name == keep:
            continue
        try:
            remove(pve, vmid, name)
            removed.append(name)
        except SnapshotError:
            failed.append(name)
    return removed, failed
