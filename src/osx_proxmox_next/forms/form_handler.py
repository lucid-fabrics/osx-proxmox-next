from __future__ import annotations

import re
from dataclasses import dataclass

from ..defaults import DEFAULT_BRIDGE, DEFAULT_ISO_DIR, DEFAULT_MEMORY_MB, DEFAULT_STORAGE
from ..domain import MIN_DISK_GB, MIN_MEMORY_MB, MIN_VMID, MAX_VMID, VmConfig
from ..smbios import SmbiosIdentity

__all__ = ["FormValues", "validate_form_values", "build_vm_config_from_values"]


@dataclass
class FormValues:
    """Raw string values read from the Textual form widgets."""

    vmid: str = ""
    name: str = ""
    cores: str = "8"
    memory: str = str(DEFAULT_MEMORY_MB)
    disk: str = "128"
    bridge: str = DEFAULT_BRIDGE
    storage: str = DEFAULT_STORAGE
    iso_dir: str = DEFAULT_ISO_DIR
    installer_path: str = ""
    existing_uuid: str = ""
    custom_vmgenid: str = ""
    custom_mac: str = ""
    # State that comes from WizardState, not directly from Input widgets
    selected_os: str = "sequoia"
    apple_services: bool = False
    use_penryn: bool = False
    net_model: str = "vmxnet3"
    smbios: SmbiosIdentity | None = None


def validate_form_values(values: FormValues) -> dict[str, str]:
    """Return a dict of field_id → error message for invalid fields.

    Returns an empty dict when all fields are valid.
    """
    errors: dict[str, str] = {}

    try:
        vmid_val = int(values.vmid)
        if vmid_val < MIN_VMID or vmid_val > MAX_VMID:
            raise ValueError
    except ValueError:
        errors["vmid"] = f"VMID must be {MIN_VMID}-{MAX_VMID}."

    if len(values.name) < 3:
        errors["name"] = "VM Name must be at least 3 chars."

    try:
        mem_val = int(values.memory)
        if mem_val < MIN_MEMORY_MB:
            raise ValueError
    except ValueError:
        errors["memory"] = f"Memory must be >= {MIN_MEMORY_MB} MB."

    try:
        disk_val = int(values.disk)
        if disk_val < MIN_DISK_GB:
            raise ValueError
    except ValueError:
        errors["disk"] = f"Disk must be >= {MIN_DISK_GB} GB."

    if not re.fullmatch(r"vmbr[0-9]+", values.bridge):
        errors["bridge"] = "Bridge must match vmbr<N> (e.g. vmbr0)."

    if not values.storage:
        errors["storage_input"] = "Storage target is required."

    return errors


def build_vm_config_from_values(values: FormValues) -> VmConfig | None:
    """Assemble a VmConfig from raw FormValues.

    Returns None if numeric fields cannot be parsed.
    """
    try:
        vmid = int(values.vmid)
        cores = int(values.cores or "8")
        memory_mb = int(values.memory or DEFAULT_MEMORY_MB)
        disk_gb = int(values.disk or "128")
    except ValueError:
        return None

    smbios = values.smbios
    return VmConfig(
        vmid=vmid,
        name=values.name,
        macos=values.selected_os or "sequoia",
        cores=cores,
        memory_mb=memory_mb,
        disk_gb=disk_gb,
        bridge=values.bridge or DEFAULT_BRIDGE,
        storage=values.storage or DEFAULT_STORAGE,
        installer_path=values.installer_path,
        iso_dir=values.iso_dir or DEFAULT_ISO_DIR,
        smbios_serial=smbios.serial if smbios else "",
        smbios_uuid=smbios.uuid if smbios else "",
        smbios_mlb=smbios.mlb if smbios else "",
        smbios_rom=smbios.rom if smbios else "",
        smbios_model=smbios.model if smbios else "",
        apple_services=values.apple_services,
        cpu_model="Penryn" if values.use_penryn else "",
        net_model=values.net_model,
        vmgenid=values.custom_vmgenid.strip().upper() if values.apple_services else "",
        static_mac=values.custom_mac.strip().upper() if values.apple_services else "",
    )
