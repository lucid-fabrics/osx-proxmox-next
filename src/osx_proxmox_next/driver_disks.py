"""What a driver update asks of a Mac's disks, read from the host: the device maps that must be gone before a volume is
rolled back or removed, and the identity of the disks that tells a record from a rolled back Mac."""

from __future__ import annotations

import re

from . import driver_engine as engine
from .gpu import parse_config
from .infrastructure import ProxmoxAdapter

__all__ = ["MapsHeld", "disk_refs", "drop_unused", "fingerprint", "own_disks", "record_is_true", "release_maps", "volume_paths"]

_DISK_KEY = re.compile(r"^(virtio|sata|scsi|ide|efidisk|tpmstate|unused)\d*$")


class MapsHeld(Exception):
    """Partition maps (or loop devices) still hold a Mac's disks, so Proxmox could not remove or roll them back."""


def disk_refs(cfg: dict[str, str]) -> list[str]:
    """The volume ids of the disks in a VM config (CD images and empty drives are no disks)."""
    out = []
    for key, value in cfg.items():
        ref = value.split(",", 1)[0]
        if _DISK_KEY.match(key) and "media=cdrom" not in value and ref != "none" and ":" in ref:
            out.append(ref)
    return out


def boot_refs(cfg: dict[str, str]) -> list[str]:
    """The volumes a driver update changes the meaning of: the OpenCore disk and the main disk. Only these tell a rolled
    back Mac from the one its record was made for (an added data disk or the staging disks must not)."""
    return [r for r in disk_refs({k: v for k, v in cfg.items() if k in ("ide0", "virtio0", "scsi0", "sata0")})]


def volume_paths(pve: ProxmoxAdapter, refs: list[str]) -> list[str]:
    """The device or file of each volume that has one, without activating anything."""
    out = []
    for ref in refs:
        path = pve.pvesm("path", ref).output.strip()
        if path.startswith("/"):
            out.append(path)
    return out


def release_maps(pve: ProxmoxAdapter, vmid: int, paths: list[str]) -> None:
    """Take this tool's leftovers off the volumes at *paths*: loop devices that were attached to them and the partition
    maps udev or kpartx made for them. Raises MapsHeld, naming the map and the exact command, when one remains."""
    if not paths:
        return
    for path in paths:
        pve.run(["kpartx", "-d", path])
        listing = pve.run(["losetup", "-j", path, "-O", "NAME", "--noheadings"])
        for loop in (listing.output.split() if listing.ok else []):
            pve.run(["losetup", "-d", loop])
    left = engine.stale_maps(pve.run(["dmsetup", "ls"]).output, paths)
    for name in left:
        pve.run(["dmsetup", "remove", name])
    left = engine.stale_maps(pve.run(["dmsetup", "ls"]).output, paths) if left else []
    if left:
        raise MapsHeld(engine.maps_held_text(vmid, left))


def fingerprint(pve: ProxmoxAdapter, cfg: dict[str, str]) -> str:
    """The identity of the Mac's disks: the LV uuids of the ones on logical volumes ('' when there are none). A thin LVM
    rollback makes new volumes, so the fingerprint of a rolled back Mac differs from the one stored with its record."""
    ids = []
    for path in volume_paths(pve, boot_refs(cfg)):
        res = pve.run(["lvs", "--noheadings", "-o", "lv_uuid", path])
        if res.ok and res.output.strip():
            ids.append(res.output.strip().split()[0])
    return engine.volume_fingerprint(ids)


def record_is_true(pve: ProxmoxAdapter, vmid: int, record: dict, cfg: dict[str, str]) -> bool:
    """False when the record was made for other disks than the Mac has now."""
    if not record.get("fingerprint"):
        return True
    return engine.record_trusted(record, fingerprint(pve, cfg))


def own_disks(state: dict) -> list[str]:
    """The volumes a run created, from its own record (the recovery and payload disks): nothing else is ever matched."""
    return [r for r in dict.fromkeys([*state.get("disks", []), state.get("payload_ref", "")]) if r]


def drop_unused(pve: ProxmoxAdapter, vmid: int, refs: list[str]) -> list[str]:
    """Remove the Mac's `unusedN` disks that are exactly one of *refs*: what a manual `qm rollback` leaves of the disks
    this tool added after the snapshot. Any other unused disk is the Mac's own and stays. Returns the volumes removed;
    raises MapsHeld when a device map still holds one."""
    if not refs:
        return []
    cfg = parse_config(pve.qm("config", str(vmid)).output)
    removed = []
    for key, value in cfg.items():
        ref = value.split(",", 1)[0]
        if re.fullmatch(r"unused\d+", key) and ref in refs:
            release_maps(pve, vmid, volume_paths(pve, [ref]))
            if pve.qm("set", str(vmid), "--delete", key).ok:
                removed.append(ref)
    return removed
