"""Unit tests for form_handler: validate_form_values and build_vm_config_from_values."""
from __future__ import annotations

import pytest

from osx_proxmox_next.domain import MIN_VMID, MAX_VMID, MIN_MEMORY_MB, MIN_DISK_GB
from osx_proxmox_next.forms.form_handler import (
    FormValues,
    build_vm_config_from_values,
    validate_form_values,
)
from osx_proxmox_next.smbios import SmbiosIdentity


def _valid_values(**kwargs) -> FormValues:
    defaults = dict(
        vmid="901",
        name="macos-vm",
        cores="8",
        memory="16384",
        disk="128",
        bridge="vmbr0",
        storage="local-lvm",
        iso_dir="/var/lib/vz/template/iso",
        selected_os="sequoia",
    )
    defaults.update(kwargs)
    return FormValues(**defaults)


# ---------------------------------------------------------------------------
# validate_form_values — valid inputs
# ---------------------------------------------------------------------------


def test_validate_form_values_valid_returns_empty_dict() -> None:
    errors = validate_form_values(_valid_values())
    assert errors == {}


def test_validate_form_values_returns_dict() -> None:
    result = validate_form_values(_valid_values())
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# validate_form_values — VMID
# ---------------------------------------------------------------------------


def test_validate_form_values_invalid_vmid_not_a_number() -> None:
    errors = validate_form_values(_valid_values(vmid="abc"))
    assert "vmid" in errors


def test_validate_form_values_vmid_too_low() -> None:
    errors = validate_form_values(_valid_values(vmid=str(MIN_VMID - 1)))
    assert "vmid" in errors


def test_validate_form_values_vmid_too_high() -> None:
    errors = validate_form_values(_valid_values(vmid=str(MAX_VMID + 1)))
    assert "vmid" in errors


def test_validate_form_values_vmid_at_min_boundary() -> None:
    errors = validate_form_values(_valid_values(vmid=str(MIN_VMID)))
    assert "vmid" not in errors


def test_validate_form_values_vmid_at_max_boundary() -> None:
    errors = validate_form_values(_valid_values(vmid=str(MAX_VMID)))
    assert "vmid" not in errors


# ---------------------------------------------------------------------------
# validate_form_values — name
# ---------------------------------------------------------------------------


def test_validate_form_values_name_too_short() -> None:
    errors = validate_form_values(_valid_values(name="ab"))
    assert "name" in errors


def test_validate_form_values_name_exactly_3_chars() -> None:
    errors = validate_form_values(_valid_values(name="abc"))
    assert "name" not in errors


def test_validate_form_values_name_empty() -> None:
    errors = validate_form_values(_valid_values(name=""))
    assert "name" in errors


# ---------------------------------------------------------------------------
# validate_form_values — memory
# ---------------------------------------------------------------------------


def test_validate_form_values_memory_too_low() -> None:
    errors = validate_form_values(_valid_values(memory=str(MIN_MEMORY_MB - 1)))
    assert "memory" in errors


def test_validate_form_values_memory_not_a_number() -> None:
    errors = validate_form_values(_valid_values(memory="abc"))
    assert "memory" in errors


def test_validate_form_values_memory_at_min() -> None:
    errors = validate_form_values(_valid_values(memory=str(MIN_MEMORY_MB)))
    assert "memory" not in errors


# ---------------------------------------------------------------------------
# validate_form_values — disk
# ---------------------------------------------------------------------------


def test_validate_form_values_disk_too_low() -> None:
    errors = validate_form_values(_valid_values(disk=str(MIN_DISK_GB - 1)))
    assert "disk" in errors


def test_validate_form_values_disk_not_a_number() -> None:
    errors = validate_form_values(_valid_values(disk="big"))
    assert "disk" in errors


def test_validate_form_values_disk_at_min() -> None:
    errors = validate_form_values(_valid_values(disk=str(MIN_DISK_GB)))
    assert "disk" not in errors


# ---------------------------------------------------------------------------
# validate_form_values — bridge
# ---------------------------------------------------------------------------


def test_validate_form_values_invalid_bridge() -> None:
    errors = validate_form_values(_valid_values(bridge="eth0"))
    assert "bridge" in errors


def test_validate_form_values_valid_bridge_vmbr0() -> None:
    errors = validate_form_values(_valid_values(bridge="vmbr0"))
    assert "bridge" not in errors


def test_validate_form_values_valid_bridge_vmbr99() -> None:
    errors = validate_form_values(_valid_values(bridge="vmbr99"))
    assert "bridge" not in errors


# ---------------------------------------------------------------------------
# validate_form_values — storage
# ---------------------------------------------------------------------------


def test_validate_form_values_empty_storage() -> None:
    errors = validate_form_values(_valid_values(storage=""))
    assert "storage_input" in errors


# ---------------------------------------------------------------------------
# build_vm_config_from_values
# ---------------------------------------------------------------------------


def test_build_vm_config_from_values_returns_vmconfig() -> None:
    from osx_proxmox_next.domain import VmConfig
    result = build_vm_config_from_values(_valid_values())
    assert isinstance(result, VmConfig)


def test_build_vm_config_from_values_maps_fields_correctly() -> None:
    values = _valid_values(vmid="500", name="my-vm", cores="4", memory="8192", disk="200")
    result = build_vm_config_from_values(values)
    assert isinstance(result, type(result))
    assert result.vmid == 500
    assert result.name == "my-vm"
    assert result.cores == 4
    assert result.memory_mb == 8192
    assert result.disk_gb == 200


def test_build_vm_config_from_values_invalid_vmid_returns_none() -> None:
    values = _valid_values(vmid="not-a-number")
    result = build_vm_config_from_values(values)
    assert result is None


def test_build_vm_config_from_values_invalid_memory_returns_none() -> None:
    values = _valid_values(memory="bad")
    result = build_vm_config_from_values(values)
    assert result is None


def test_build_vm_config_from_values_invalid_disk_returns_none() -> None:
    values = _valid_values(disk="bad")
    result = build_vm_config_from_values(values)
    assert result is None


def test_build_vm_config_from_values_smbios_populated_when_present() -> None:
    smbios = SmbiosIdentity(
        serial="ABCDEFG12345",
        uuid="A1B2C3D4-E5F6-7890-ABCD-EF1234567890",
        mlb="MLBABCDEFG123456",
        rom="AABBCCDDEEFF",
        model="MacPro7,1",
    )
    values = _valid_values(smbios=smbios)
    result = build_vm_config_from_values(values)
    assert result is not None and result.smbios_serial == smbios.serial
    assert result.smbios_model == smbios.model


def test_build_vm_config_from_values_no_smbios_empty_strings() -> None:
    values = _valid_values(smbios=None)
    result = build_vm_config_from_values(values)
    assert result is not None and result.smbios_serial == ""
    assert result.smbios_model == ""


def test_build_vm_config_from_values_penryn_sets_cpu_model() -> None:
    values = _valid_values(use_penryn=True)
    result = build_vm_config_from_values(values)
    assert result is not None and result.cpu_model == "Penryn"


def test_build_vm_config_from_values_no_penryn_empty_cpu_model() -> None:
    values = _valid_values(use_penryn=False)
    result = build_vm_config_from_values(values)
    assert result is not None and result.cpu_model == ""


def test_build_vm_config_from_values_apple_services_vmgenid_uppercased() -> None:
    values = _valid_values(apple_services=True, custom_vmgenid="a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    result = build_vm_config_from_values(values)
    assert result is not None and result.vmgenid == "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"


def test_build_vm_config_from_values_no_apple_services_vmgenid_empty() -> None:
    values = _valid_values(apple_services=False, custom_vmgenid="some-id")
    result = build_vm_config_from_values(values)
    assert result is not None and result.vmgenid == ""
