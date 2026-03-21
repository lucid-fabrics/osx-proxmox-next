from __future__ import annotations

import json
import logging

from ..defaults import DEFAULT_STORAGE
from ..domain import DEFAULT_VMID, MIN_VMID, MAX_VMID
from ..infrastructure import ProxmoxAdapter
from .proxmox_service import get_proxmox_adapter

log = logging.getLogger(__name__)

__all__ = ["detect_storage_targets", "detect_next_vmid", "list_macos_vms"]


def detect_storage_targets(adapter: ProxmoxAdapter | None = None) -> list[str]:
    """Return active storage targets that support disk images.

    Falls back to ``[DEFAULT_STORAGE, "local"]`` when pvesm is unavailable.
    """
    pve = adapter or get_proxmox_adapter()
    res = pve.pvesm("status", "-content", "images")
    if not res.ok:
        log.debug("Failed to detect storage targets: %s", res.output)
        return [DEFAULT_STORAGE, "local"]
    targets: list[str] = []
    for line in res.output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "active":
            name = parts[0]
            if name not in targets:
                targets.append(name)
    if DEFAULT_STORAGE not in targets:
        targets.insert(0, DEFAULT_STORAGE)
    return targets[:5]


def detect_next_vmid(adapter: ProxmoxAdapter | None = None) -> int:
    """Return the next available VMID from the Proxmox cluster.

    Tries ``pvesh get /cluster/nextid`` first, then falls back to
    ``qm list`` + max+1.  Returns ``DEFAULT_VMID`` when both fail.
    """
    pve = adapter or get_proxmox_adapter()
    res = pve.pvesh("get", "/cluster/nextid")
    if res.ok:
        output = res.output.strip()
        if output.isdigit():
            vmid = int(output)
            if MIN_VMID <= vmid <= MAX_VMID:
                return vmid
        try:
            parsed = json.loads(output)
            if isinstance(parsed, int) and MIN_VMID <= parsed <= MAX_VMID:
                return parsed  # pragma: no cover
        except (json.JSONDecodeError, ValueError):
            log.debug("pvesh returned non-JSON/non-int: %s", output)
    else:
        log.debug("Failed to get next VMID via pvesh: %s", res.output)

    res = pve.qm("list")
    if res.ok:
        vmids: list[int] = []
        for line in res.output.splitlines()[1:]:
            parts = line.split()
            if parts and parts[0].isdigit():
                vmids.append(int(parts[0]))
        next_vmid = (max(vmids) + 1) if vmids else DEFAULT_VMID
        if next_vmid < MIN_VMID:
            return MIN_VMID
        if next_vmid > MAX_VMID:
            return MAX_VMID
        return next_vmid
    log.debug("Failed to detect next VMID via qm list: %s", res.output)
    return DEFAULT_VMID


def list_macos_vms(adapter: ProxmoxAdapter | None = None) -> list[str]:
    """Return qm list lines for macOS VMs only (those with isa-applesmc in config).

    Returns an empty list when pvesh/qm is unavailable.  The first element of
    a non-empty result is the header line from ``qm list``.
    """
    pve = adapter or get_proxmox_adapter()
    res = pve.qm("list")
    if not res.ok:
        log.debug("Failed to list VMs: %s", res.output)
        return []
    all_lines = res.output.strip().splitlines()
    if not all_lines:
        return []
    macos_lines: list[str] = []
    for line in all_lines[1:]:
        parts = line.split()
        if not parts:
            continue
        vmid = parts[0]
        cfg_res = pve.qm("config", vmid)
        if cfg_res.ok and "isa-applesmc" in cfg_res.output:
            macos_lines.append(line)
    if not macos_lines:
        return []
    return [all_lines[0]] + macos_lines
