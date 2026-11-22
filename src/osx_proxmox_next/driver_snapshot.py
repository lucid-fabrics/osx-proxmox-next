"""The driver snapshot of a Mac before a driver update: save, list, put back, remove. Constant argv lists only."""

from __future__ import annotations

from . import driver_engine as engine
from .infrastructure import ProxmoxAdapter

__all__ = ["SnapshotError", "check_support", "create", "list_tool", "prune_older", "remove", "rollback"]


class SnapshotError(Exception):
    """A snapshot call failed."""


def check_support(pve: ProxmoxAdapter, cfg: dict[str, str]) -> tuple[bool, str]:
    """(ok, why not): can the storage of every disk of this Mac take a snapshot (asked from `pvesm status`)."""
    res = pve.run(["pvesm", "status"])
    return engine.snapshot_support(cfg, engine.parse_storage_types(res.output if res.ok else ""))


def create(pve: ProxmoxAdapter, vmid: int, version: str, name: str = "") -> str:
    name = name or engine.snapshot_name()
    res = pve.run(engine.snapshot_argv(vmid, name, version))
    if not res.ok:
        raise SnapshotError("Could not save a snapshot of VM %d: %s" % (vmid, res.output.strip()[-300:]))
    return name


def list_tool(pve: ProxmoxAdapter, vmid: int) -> list[str]:
    """The snapshots this tool made for the Mac, oldest first. Snapshots of anyone else are never listed."""
    res = pve.run(["qm", "listsnapshot", str(vmid)])
    return engine.tool_snapshots(res.output) if res.ok else []


def rollback(pve: ProxmoxAdapter, vmid: int, name: str) -> None:
    res = pve.run(engine.rollback_argv(vmid, name))
    if not res.ok:
        raise SnapshotError("Could not put VM %d back to %s: %s" % (vmid, name, res.output.strip()[-300:]))


def remove(pve: ProxmoxAdapter, vmid: int, name: str) -> None:
    res = pve.run(engine.delete_snapshot_argv(vmid, name))
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
