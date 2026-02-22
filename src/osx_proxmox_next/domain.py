from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_MACOS = {
    "ventura": {"label": "macOS Ventura 13", "major": 13, "channel": "stable"},
    "sonoma": {"label": "macOS Sonoma 14", "major": 14, "channel": "stable"},
    "sequoia": {"label": "macOS Sequoia 15", "major": 15, "channel": "stable"},
    "tahoe": {"label": "macOS Tahoe 26", "major": 26, "channel": "stable"},
}


@dataclass
class VmConfig:
    vmid: int
    name: str
    macos: str
    cores: int
    memory_mb: int
    disk_gb: int
    bridge: str
    storage: str
    installer_path: str = ""
    smbios_serial: str = ""
    smbios_uuid: str = ""
    smbios_mlb: str = ""
    smbios_rom: str = ""
    smbios_model: str = ""
    no_smbios: bool = False
    apple_services: bool = False
    vmgenid: str = ""
    static_mac: str = ""
    verbose_boot: bool = False
    iso_dir: str = ""


def validate_config(config: VmConfig) -> list[str]:
    issues: list[str] = []
    if config.vmid < 100 or config.vmid > 999999:
        issues.append("VMID must be between 100 and 999999.")
    if not config.name or len(config.name) < 3:
        issues.append("VM name must be at least 3 characters.")
    if config.macos not in SUPPORTED_MACOS:
        issues.append(f"macOS version must be one of: {', '.join(SUPPORTED_MACOS)}.")
    if config.cores < 2:
        issues.append("At least 2 CPU cores are required.")
    if config.memory_mb < 4096:
        issues.append("At least 4096 MB RAM is required.")
    if config.disk_gb < 64:
        issues.append("At least 64 GB disk is required.")
    if not config.bridge.startswith("vmbr"):
        issues.append("Bridge should look like vmbr0.")
    if not config.storage:
        issues.append("Storage target is required.")
    return issues
