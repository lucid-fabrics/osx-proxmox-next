from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .infrastructure import ProxmoxAdapter


class Severity(str, Enum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class DoctorCheck:
    name: str
    severity: Severity
    message: str
    fix: str = ""


def _parse_qm_config(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line and not line.startswith("#"):
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def _is_power_of_two(n: int) -> bool:
    return n > 0 and math.log2(n) == int(math.log2(n))


def _net_model_from_value(net_value: str) -> str:
    # Proxmox net0 format: "vmxnet3=AA:BB:CC:DD:EE:FF,bridge=vmbr0,..."
    # The NIC model is always the first segment (before any '=').
    first = net_value.split(",")[0].strip()
    return first.split("=")[0].strip()


def _check_balloon(cfg: dict[str, str], vmid: int) -> DoctorCheck:
    val = cfg.get("balloon", "")
    if val == "0":
        return DoctorCheck("balloon", Severity.OK, "balloon=0 — macOS has no balloon driver")
    return DoctorCheck(
        "balloon",
        Severity.FAIL,
        f"balloon={val or 'not set'} — macOS will crash with balloon driver enabled",
        fix=f"qm set {vmid} --balloon 0",
    )


def _check_machine(cfg: dict[str, str], vmid: int) -> DoctorCheck:
    val = cfg.get("machine", "")
    if "q35" in val:
        return DoctorCheck("machine", Severity.OK, f"machine={val}")
    return DoctorCheck(
        "machine",
        Severity.FAIL,
        f"machine={val or 'not set'} — macOS requires q35",
        fix=f"qm set {vmid} --machine q35",
    )


def _check_cores(cfg: dict[str, str], vmid: int) -> DoctorCheck:
    raw = cfg.get("cores", "")
    try:
        n = int(raw)
    except (ValueError, TypeError):
        return DoctorCheck("cores", Severity.WARN, "cores not set — using Proxmox default")
    if _is_power_of_two(n):
        return DoctorCheck("cores", Severity.OK, f"cores={n} — power-of-2, safe for macOS")
    nearest = 2 ** round(math.log2(n))
    return DoctorCheck(
        "cores",
        Severity.FAIL,
        f"cores={n} — non-power-of-2 value hangs macOS at Apple logo",
        fix=f"qm set {vmid} --cores {nearest}",
    )


def _check_memory(cfg: dict[str, str], vmid: int) -> DoctorCheck:
    raw = cfg.get("memory", "")
    try:
        mb = int(raw)
    except (ValueError, TypeError):
        return DoctorCheck("memory", Severity.WARN, "memory not set")
    if mb < 4096:
        return DoctorCheck(
            "memory",
            Severity.WARN,
            f"memory={mb} MB — macOS installer needs at least 4096 MB",
            fix=f"qm set {vmid} --memory 4096",
        )
    return DoctorCheck("memory", Severity.OK, f"memory={mb} MB")


def _check_cpu(cfg: dict[str, str]) -> DoctorCheck:
    val = cfg.get("cpu", "")
    if val.lower() in ("kvm64", "kvm32", ""):
        return DoctorCheck(
            "cpu",
            Severity.WARN,
            f"cpu={val or 'not set'} — kvm64/kvm32 may cause boot failures; expected host or Cascadelake-Server",
        )
    return DoctorCheck("cpu", Severity.OK, f"cpu={val}")


def _check_net(cfg: dict[str, str], vmid: int) -> DoctorCheck:
    net_val = cfg.get("net0", "")
    if not net_val:
        return DoctorCheck("net0", Severity.WARN, "net0 not configured")
    model = _net_model_from_value(net_val)
    good = ("vmxnet3", "e1000", "e1000-82545em")
    if any(model.startswith(m) for m in good):
        return DoctorCheck("net0", Severity.OK, f"net0 model={model} — native macOS driver")
    return DoctorCheck(
        "net0",
        Severity.FAIL,
        f"net0 model={model} — macOS has no driver for this NIC; use vmxnet3 or e1000",
        fix=f"qm set {vmid} --net0 vmxnet3,bridge=vmbr0,firewall=0",
    )


def _check_agent(cfg: dict[str, str], vmid: int) -> DoctorCheck:
    val = cfg.get("agent", "")
    if "enabled=1" in val:
        return DoctorCheck("agent", Severity.OK, "agent=enabled — graceful shutdown works")
    return DoctorCheck(
        "agent",
        Severity.WARN,
        "agent not enabled — graceful shutdown won't work without qemu-guest-agent in macOS",
        fix=f"qm set {vmid} --agent enabled=1",
    )


def _check_smbios(cfg: dict[str, str]) -> DoctorCheck:
    val = cfg.get("smbios1", "")
    if val and "uuid=" in val:
        return DoctorCheck("smbios1", Severity.OK, "smbios1 set — identity chain configured")
    return DoctorCheck(
        "smbios1",
        Severity.WARN,
        "smbios1 not set — Apple services (iMessage, FaceTime, iCloud) won't work",
    )


def _check_disk(cfg: dict[str, str], key: str, label: str) -> DoctorCheck:
    if key in cfg:
        return DoctorCheck(key, Severity.OK, f"{key} present — {label}")
    return DoctorCheck(key, Severity.WARN, f"{key} not found — {label} may be missing")


def _check_boot_order(cfg: dict[str, str], vmid: int) -> DoctorCheck:
    boot_val = cfg.get("boot", "")
    if "ide3" in boot_val:
        return DoctorCheck(
            "boot",
            Severity.FAIL,
            "boot order references ide3 — that device doesn't exist and blocks boot",
            fix=f"qm set {vmid} --boot order=ide2;virtio0;ide0",
        )
    if boot_val:
        return DoctorCheck("boot", Severity.OK, f"boot={boot_val}")
    return DoctorCheck("boot", Severity.WARN, "boot order not set — Proxmox using firmware default")


def run_doctor(vmid: int, adapter: ProxmoxAdapter | None = None) -> list[DoctorCheck]:
    if adapter is None:
        from .services import get_proxmox_adapter
        adapter = get_proxmox_adapter()

    result = adapter.qm("config", str(vmid))
    if not result.ok:
        return [DoctorCheck("vm", Severity.FAIL, f"VM {vmid} not found or inaccessible: {result.output}")]

    cfg = _parse_qm_config(result.output)

    return [
        _check_balloon(cfg, vmid),
        _check_machine(cfg, vmid),
        _check_cores(cfg, vmid),
        _check_memory(cfg, vmid),
        _check_cpu(cfg),
        _check_net(cfg, vmid),
        _check_agent(cfg, vmid),
        _check_smbios(cfg),
        _check_boot_order(cfg, vmid),
        _check_disk(cfg, "virtio0", "main macOS disk"),
        _check_disk(cfg, "ide0", "OpenCore bootloader"),
        _check_disk(cfg, "ide2", "recovery/installer image"),
    ]
