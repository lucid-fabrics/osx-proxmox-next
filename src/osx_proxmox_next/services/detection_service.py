from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from ..defaults import DEFAULT_STORAGE
from ..domain import DEFAULT_VMID, MIN_VMID, MAX_VMID
from ..infrastructure import ProxmoxAdapter
from .proxmox_service import get_proxmox_adapter

log = logging.getLogger(__name__)

__all__ = ["detect_storage_targets", "detect_next_vmid", "list_macos_vms", "VmInfo", "fetch_vm_info"]


@dataclass
class VmInfo:
    vmid: int
    name: str
    status: str  # "running" | "stopped"
    config_raw: str


def fetch_vm_info(vmid: int, adapter: ProxmoxAdapter | None = None) -> VmInfo | None:
    if adapter is None:
        adapter = get_proxmox_adapter()
    runtime = adapter
    status_result = runtime.run(["qm", "status", str(vmid)])
    if not status_result.ok:
        return None
    # Parse status line like "status: running" or "status: stopped"
    status = "stopped"
    for line in status_result.output.splitlines():
        if "running" in line.lower():
            status = "running"
            break
    config_result = runtime.run(["qm", "config", str(vmid)])
    config_raw = config_result.output if config_result.ok else ""
    # Parse name from config
    name = ""
    for line in config_raw.splitlines():
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
            break
    return VmInfo(vmid=vmid, name=name, status=status, config_raw=config_raw)


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
            return DEFAULT_VMID
        return next_vmid
    log.debug("Failed to detect next VMID via qm list: %s", res.output)
    return DEFAULT_VMID


def list_macos_vms(adapter: ProxmoxAdapter | None = None) -> list[str]:
    """Return qm list lines for macOS VMs only (those with isa-applesmc in config).

    Returns an empty list when pvesh/qm is unavailable.  The first element of
    a non-empty result is the header line from ``qm list``.

    Config checks run in parallel (up to 10 workers) to avoid O(n) sequential
    latency on nodes with many VMs.
    """
    pve = adapter or get_proxmox_adapter()
    res = pve.qm("list")
    if not res.ok:
        log.debug("Failed to list VMs: %s", res.output)
        return []
    all_lines = res.output.strip().splitlines()
    if not all_lines:
        return []

    vm_lines = [line for line in all_lines[1:] if line.split()]

    def _is_macos(line: str) -> bool:
        vmid = line.split()[0]
        cfg_res = pve.qm("config", vmid)
        return cfg_res.ok and "isa-applesmc" in cfg_res.output

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_is_macos, line): line for line in vm_lines}
        macos_lines = [futures[f] for f in as_completed(futures) if f.result()]

    if not macos_lines:
        return []
    # Restore original qm list order (ThreadPoolExecutor completes out of order)
    order = {line: idx for idx, line in enumerate(vm_lines)}
    macos_lines.sort(key=lambda l: order.get(l, 0))
    return [all_lines[0]] + macos_lines
