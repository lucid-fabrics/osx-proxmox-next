from __future__ import annotations

import re
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
    cpu_model: str = ""


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
    if not re.fullmatch(r"vmbr[0-9]+", config.bridge):
        issues.append("Bridge must match vmbr<N> (e.g. vmbr0).")
    if config.name and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.\-]*", config.name):
        issues.append("VM name must start with alphanumeric and contain only [a-zA-Z0-9.-].")
    if config.installer_path and not re.fullmatch(r"[a-zA-Z0-9/._\-]+", config.installer_path):
        issues.append("Installer path contains invalid characters.")
    if not config.storage:
        issues.append("Storage target is required.")
    # SMBIOS fields are embedded in shell commands — restrict to safe charset
    if config.smbios_serial and not re.fullmatch(r"[A-Z0-9]{12}", config.smbios_serial):
        issues.append("SMBIOS serial must be exactly 12 chars [A-Z0-9].")
    if config.smbios_mlb and not re.fullmatch(r"[A-Z0-9]{17}", config.smbios_mlb):
        issues.append("SMBIOS MLB must be exactly 17 chars [A-Z0-9].")
    if config.smbios_rom and not re.fullmatch(r"[A-F0-9]{12}", config.smbios_rom):
        issues.append("SMBIOS ROM must be exactly 12 hex chars [A-F0-9].")
    if config.smbios_uuid and not re.fullmatch(
        r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}",
        config.smbios_uuid,
    ):
        issues.append("SMBIOS UUID must be a valid uppercase UUID.")
    if config.smbios_model and not re.fullmatch(r"[A-Za-z0-9,]{1,20}", config.smbios_model):
        issues.append("SMBIOS model must be alphanumeric (e.g., MacPro7,1).")
    if config.cpu_model and not re.fullmatch(r"[A-Za-z0-9\-]+", config.cpu_model):
        issues.append("CPU model must be alphanumeric/hyphens only (e.g., Skylake-Server-IBRS).")
    if config.storage and not re.fullmatch(r"[a-zA-Z0-9_\-]+", config.storage):
        issues.append("Storage target must be alphanumeric, hyphens, underscores only.")
    if config.static_mac and not re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", config.static_mac):
        issues.append("Static MAC must be XX:XX:XX:XX:XX:XX format (uppercase hex).")
    if config.vmgenid and not re.fullmatch(
        r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}",
        config.vmgenid,
    ):
        issues.append("vmgenid must be a valid uppercase UUID.")
    return issues
