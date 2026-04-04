"""Unit tests for WizardState dataclass."""
from __future__ import annotations

from pathlib import Path

from osx_proxmox_next.models.wizard_state import WizardState
from osx_proxmox_next.domain import DEFAULT_VMID


def test_wizard_state_instantiates_with_defaults() -> None:
    state = WizardState()
    assert state.selected_os == ""
    assert state.vmid == DEFAULT_VMID
    assert state.cores == 8
    assert state.memory_mb == 16384
    assert state.disk_gb == 128
    assert state.bridge == "vmbr0"
    assert state.storage == "local-lvm"
    assert state.net_model == "vmxnet3"


def test_wizard_state_boolean_defaults() -> None:
    state = WizardState()
    assert state.apple_services is False
    assert state.use_penryn is False
    assert state.preflight_done is False
    assert state.preflight_ok is False
    assert state.download_running is False
    assert state.downloads_complete is False
    assert state.dry_run_done is False
    assert state.dry_run_ok is False
    assert state.apply_running is False
    assert state.live_done is False
    assert state.live_ok is False
    assert state.manage_mode is False
    assert state.uninstall_running is False
    assert state.uninstall_done is False
    assert state.uninstall_ok is False
    assert state.uninstall_purge is True


def test_wizard_state_optional_fields_default_to_none() -> None:
    state = WizardState()
    assert state.smbios is None
    assert state.config is None
    assert state.live_log is None
    assert state.snapshot is None


def test_wizard_state_mutable_list_defaults_are_not_shared() -> None:
    """Each instance must have its own list — no shared mutable default."""
    a = WizardState()
    b = WizardState()
    a.storage_targets.append("extra")
    assert "extra" not in b.storage_targets


def test_wizard_state_mutable_dict_defaults_are_not_shared() -> None:
    a = WizardState()
    b = WizardState()
    a.form_errors["vmid"] = "bad"
    assert "vmid" not in b.form_errors


def test_wizard_state_list_fields_are_lists() -> None:
    state = WizardState()
    assert isinstance(state.storage_targets, list)
    assert isinstance(state.iso_dirs, list)
    assert isinstance(state.preflight_checks, list)
    assert isinstance(state.download_errors, list)
    assert isinstance(state.plan_steps, list)
    assert isinstance(state.assets_missing, list)
    assert isinstance(state.apply_log, list)
    assert isinstance(state.dry_log, list)
    assert isinstance(state.live_log_lines, list)
    assert isinstance(state.uninstall_vm_list, list)
    assert isinstance(state.uninstall_log, list)


def test_wizard_state_field_assignment() -> None:
    state = WizardState()
    state.selected_os = "sequoia"
    state.vmid = 200
    state.name = "my-vm"
    assert state.selected_os == "sequoia"
    assert state.vmid == 200
    assert state.name == "my-vm"


def test_wizard_state_live_log_accepts_path() -> None:
    state = WizardState()
    state.live_log = Path("/tmp/install.log")
    assert isinstance(state.live_log, Path)


def test_wizard_state_download_pct_default() -> None:
    state = WizardState()
    assert state.download_pct == 0


def test_wizard_state_download_phase_default() -> None:
    state = WizardState()
    assert state.download_phase == ""
