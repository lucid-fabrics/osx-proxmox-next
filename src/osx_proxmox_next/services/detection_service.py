from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from ..defaults import DEFAULT_STORAGE
from ..domain import DEFAULT_VMID, MIN_VMID, MAX_VMID
from ..infrastructure import ProxmoxAdapter, VmInfo, fetch_vm_info, get_proxmox_adapter

log = logging.getLogger(__name__)

__all__ = [
    "detect_storage_targets",
    "detect_storage_free_gb",
    "detect_next_vmid",
    "list_macos_vms",
    "list_macos_vm_records",
    "VmInfo",
    "VmRecord",
    "VmFacts",
    "DiskFact",
    "parse_vm_config",
    "fetch_vm_info",
]


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


def detect_storage_free_gb(adapter: ProxmoxAdapter | None = None) -> dict[str, int]:
    """Free space in GB per active image storage (empty when pvesm is unavailable)."""
    pve = adapter or get_proxmox_adapter()
    res = pve.pvesm("status", "-content", "images")
    free: dict[str, int] = {}
    if not res.ok:
        return free
    for line in res.output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 6 and parts[2] == "active" and parts[5].isdigit():
            free[parts[0]] = int(parts[5]) // (1024 * 1024)  # pvesm prints KiB
    return free


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


def _macos_rows(pve: ProxmoxAdapter) -> tuple[str, list[tuple[str, str]]]:
    """(qm list header, [(qm list line, qm config output)]) for macOS VMs only.

    A macOS VM is one whose config carries isa-applesmc. Config reads run in
    parallel (up to 10 workers) to avoid O(n) sequential latency on nodes with
    many VMs. Rows keep the qm list order. Empty when qm is unavailable."""
    res = pve.qm("list")
    if not res.ok:
        log.debug("Failed to list VMs: %s", res.output)
        return "", []
    all_lines = res.output.strip().splitlines()
    if not all_lines:
        return "", []

    vm_lines = [line for line in all_lines[1:] if line.split()]

    def _config(line: str) -> str | None:
        vmid = line.split()[0]
        cfg_res = pve.qm("config", vmid)
        if cfg_res.ok and "isa-applesmc" in cfg_res.output:
            return cfg_res.output
        return None

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_config, line): line for line in vm_lines}
        found = {futures[f]: f.result() for f in as_completed(futures)}

    rows = [(line, found[line]) for line in vm_lines if found.get(line) is not None]
    return all_lines[0], rows


def list_macos_vms(adapter: ProxmoxAdapter | None = None) -> list[str]:
    """Return qm list lines for macOS VMs only (those with isa-applesmc in config).

    Returns an empty list when pvesh/qm is unavailable.  The first element of
    a non-empty result is the header line from ``qm list``."""
    header, rows = _macos_rows(adapter or get_proxmox_adapter())
    if not rows:
        return []
    return [header] + [line for line, _cfg in rows]


@dataclass
class DiskFact:
    slot: str       # virtio0, sata0, ...
    storage: str    # local-lvm
    size_gb: int    # 0 when the config does not say


@dataclass
class VmFacts:
    """What `qm config` says about a VM, in the units the TUI shows."""
    name: str = ""
    cores: int = 0
    memory_mb: int = 0
    bridge: str = ""
    nic_model: str = ""
    vlan: str = ""
    disks: list[DiskFact] = field(default_factory=list)
    has_gpu: bool = False       # a PCI card is passed through
    display_on_gpu: bool = False  # ... and the emulated display is off


_DISK_RE = re.compile(r"^(virtio|sata|scsi|ide)(\d+):\s*([^:,\s]+):[^,\s]*(?:.*?\bsize=(\d+)([GMT]))?", re.M)
_NIC_RE = re.compile(r"^net0:\s*([a-z0-9\-]+)(?:=[0-9A-Fa-f:]+)?(.*)$", re.M)


def parse_vm_config(config_raw: str) -> VmFacts:
    """Read the facts the TUI shows out of a raw `qm config` dump."""
    facts = VmFacts()

    def field_of(key: str) -> str:
        match = re.search(rf"^{key}:\s*(.+)$", config_raw, re.M)
        return match.group(1).strip() if match else ""

    facts.name = field_of("name")
    for key, attr in (("cores", "cores"), ("memory", "memory_mb")):
        value = field_of(key)
        if value.isdigit():
            setattr(facts, attr, int(value))
    nic = _NIC_RE.search(config_raw)
    if nic:
        facts.nic_model = nic.group(1)
        rest = nic.group(2)
        bridge = re.search(r"bridge=([^,\s]+)", rest)
        vlan = re.search(r"tag=(\d+)", rest)
        facts.bridge = bridge.group(1) if bridge else ""
        facts.vlan = vlan.group(1) if vlan else ""
    for kind, num, storage, size, unit in _DISK_RE.findall(config_raw):
        if storage in ("none", "cdrom") or "media=cdrom" in config_raw.split(f"{kind}{num}:", 1)[-1].split("\n", 1)[0]:
            continue
        gb = 0
        if size:
            gb = int(size) * {"G": 1, "M": 0, "T": 1024}[unit]
        facts.disks.append(DiskFact(slot=f"{kind}{num}", storage=storage, size_gb=gb))
    facts.has_gpu = bool(re.search(r"^hostpci\d+:", config_raw, re.M))
    facts.display_on_gpu = facts.has_gpu and field_of("vga").startswith("none")
    return facts


@dataclass
class VmRecord:
    """One macOS VM as the Home screen lists it."""
    vmid: int
    name: str
    status: str     # "running" | "stopped" | whatever qm reports
    cores: int
    memory_mb: int
    config_raw: str = ""

    @property
    def memory_gb(self) -> int:
        return round(self.memory_mb / 1024)

    @property
    def facts(self) -> VmFacts:
        return parse_vm_config(self.config_raw)


def list_macos_vm_records(adapter: ProxmoxAdapter | None = None) -> list[VmRecord]:
    """Structured twin of list_macos_vms: one VmRecord per macOS VM, in qm list order."""
    _header, rows = _macos_rows(adapter or get_proxmox_adapter())
    records: list[VmRecord] = []
    for line, config in rows:
        parts = line.split()
        if not parts or not parts[0].isdigit():
            continue  # pragma: no cover - _macos_rows keeps only numeric ids
        facts = parse_vm_config(config)
        records.append(VmRecord(
            vmid=int(parts[0]),
            name=facts.name or (parts[1] if len(parts) > 1 else ""),
            status=parts[2] if len(parts) > 2 else "unknown",
            cores=facts.cores,
            memory_mb=facts.memory_mb,
            config_raw=config,
        ))
    return records
