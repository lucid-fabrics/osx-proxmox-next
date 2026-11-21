"""The state of the New Mac screen and the pure rules that turn it into a VmConfig."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..defaults import HOST_RAM_RESERVE_MB
from ..domain import MIN_DISK_GB, MIN_MEMORY_MB, VmConfig, validate_config
from ..forms import FormValues, build_vm_config_from_values, validate_form_values
from ..presets import SizePreset
from ..smbios import SmbiosIdentity, resolve_smbios

__all__ = ["NewMacState", "ROW_ORDER", "default_name", "effective_size", "validate_new_mac"]

# Top to bottom: the order the screen focuses the first bad row in.
ROW_ORDER = (
    "macos", "name", "size", "cores", "memory", "disk", "storage", "install", "apple",
    "vmid", "bridge", "vlan", "iso_dir", "installer_path", "existing_uuid", "penryn", "nic",
    "vmgenid", "mac", "force",
)


def default_name(macos: str) -> str:
    return f"macos-{macos}"


@dataclass
class NewMacState:
    macos: str = "sequoia"
    name: str = ""
    name_edited: bool = False
    size: str = "balanced"           # light | balanced | heavy | custom
    custom_cores: str = "8"
    custom_memory_gb: str = "16"
    custom_disk_gb: str = "128"
    storage: str = ""
    handsoff: bool = True
    apple_services: bool = False
    vmid: str = ""
    bridge: str = "vmbr0"
    vlan: str = ""
    iso_dir: str = ""
    installer_path: str = ""
    existing_uuid: str = ""
    use_penryn: bool = False
    e1000: bool = False
    vmgenid: str = ""
    mac: str = ""
    force_download: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            self.name = default_name(self.macos)

    def set_macos(self, macos: str) -> None:
        """Switch version; a name the user did not edit follows it."""
        if not self.name_edited:
            self.name = default_name(macos)
        self.macos = macos


def effective_size(state: NewMacState, presets: dict[str, SizePreset]) -> tuple[str, str, str]:
    """(cores, memory in MB, disk in GB) as strings, from the preset or the custom fields."""
    if state.size == "custom":
        try:
            memory = str(int(state.custom_memory_gb) * 1024)
        except ValueError:
            memory = "x"
        return state.custom_cores.strip(), memory, state.custom_disk_gb.strip()
    preset = presets[state.size]
    return str(preset.cores), str(preset.memory_mb), str(preset.disk_gb)


_ISSUE_ROWS = (
    (r"^VMID", "vmid"), (r"^VM name", "name"), (r"macOS version", "macos"), (r"CPU cores", "cores"),
    (r"RAM", "memory"), (r"disk is required|GB disk", "disk"), (r"^Bridge", "bridge"), (r"^VLAN", "vlan"),
    (r"^Installer", "installer_path"), (r"^Storage", "storage"), (r"SMBIOS UUID", "existing_uuid"),
    (r"vmgenid", "vmgenid"), (r"Static MAC", "mac"), (r"CPU model", "penryn"), (r"net_model", "nic"),
)


def _row_of_issue(issue: str) -> str:
    for pattern, row in _ISSUE_ROWS:
        if re.search(pattern, issue):
            return row
    return "name"


def validate_new_mac(
    state: NewMacState,
    presets: dict[str, SizePreset],
    host_memory_limit_mb: int = 0,
    smbios: SmbiosIdentity | None = None,
) -> tuple[dict[str, str], VmConfig | None]:
    """(errors by row key in screen order, the VmConfig when there are none).

    Memory is typed in GB, so the memory messages are worded in GB."""
    cores, memory_mb, disk = effective_size(state, presets)
    values = FormValues(
        vmid=state.vmid.strip(), name=state.name.strip(), cores=cores, memory=memory_mb, disk=disk,
        bridge=state.bridge.strip(), vlan=state.vlan.strip(), storage=state.storage.strip(),
        iso_dir=state.iso_dir.strip(), installer_path=state.installer_path.strip(),
        existing_uuid=state.existing_uuid.strip().upper(),
        custom_vmgenid=state.vmgenid.strip() if state.apple_services else "",
        custom_mac=state.mac.strip() if state.apple_services else "",
        selected_os=state.macos, apple_services=state.apple_services, use_penryn=state.use_penryn,
        net_model="e1000-82545em" if state.e1000 else "vmxnet3",
        smbios=smbios or resolve_smbios(state.macos, state.apple_services, state.existing_uuid.strip().upper()),
    )
    errors: dict[str, str] = {}
    raw = validate_form_values(values, host_memory_limit_mb=host_memory_limit_mb)
    key_map = {"storage_input": "storage"}
    for key, message in raw.items():
        errors[key_map.get(key, key)] = message
    if "memory" in errors:
        if host_memory_limit_mb and memory_mb.isdigit() and int(memory_mb) > host_memory_limit_mb:
            errors["memory"] = (f"Memory: this host has only {host_memory_limit_mb // 1024} GB free for a VM "
                                f"(it keeps {HOST_RAM_RESERVE_MB // 1024} GB for itself). Lower it.")
        else:
            errors["memory"] = f"Memory must be a whole number of GB, at least {MIN_MEMORY_MB // 1024}."
    if "disk" in errors:
        errors["disk"] = f"Disk must be a whole number of GB, at least {MIN_DISK_GB}."
    if "name" in errors:
        errors["name"] = "Name must be at least 3 characters."
    config = build_vm_config_from_values(values)
    if config is None:
        errors.setdefault("cores", "Cores must be a whole number.")
        if "memory" not in errors and not memory_mb.isdigit():
            errors["memory"] = "Memory must be a whole number of GB."
    else:
        for issue in validate_config(config):
            row = _row_of_issue(issue)
            if row in ("memory", "disk"):
                continue  # already worded in GB above
            errors.setdefault(row, issue)
    ordered = {key: errors[key] for key in ROW_ORDER if key in errors}
    ordered.update({k: v for k, v in errors.items() if k not in ordered})
    return ordered, (config if not ordered else None)
