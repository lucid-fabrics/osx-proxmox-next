from __future__ import annotations

import pytest

from osx_proxmox_next.domain import (
    EditChanges,
    MIN_VMID,
    MAX_VMID,
    MIN_CORES,
    MIN_MEMORY_MB,
    validate_edit_changes,
)
from osx_proxmox_next.planner import build_edit_plan


# ── validate_edit_changes ────────────────────────────────────────────


def test_validate_requires_at_least_one_change():
    issues = validate_edit_changes(900, EditChanges())
    assert any("At least one" in i for i in issues)


def test_validate_rejects_out_of_range_vmid():
    issues = validate_edit_changes(5, EditChanges(cores=4))
    assert any("VMID" in i for i in issues)


def test_validate_rejects_bad_name():
    issues = validate_edit_changes(900, EditChanges(name="ab"))  # too short
    assert any("name" in i.lower() for i in issues)

    issues = validate_edit_changes(900, EditChanges(name="has spaces"))
    assert any("name" in i.lower() for i in issues)


def test_validate_rejects_low_cores():
    issues = validate_edit_changes(900, EditChanges(cores=MIN_CORES - 1))
    assert any("cores" in i.lower() for i in issues)


def test_validate_rejects_low_memory():
    issues = validate_edit_changes(900, EditChanges(memory_mb=MIN_MEMORY_MB - 1))
    assert any("RAM" in i for i in issues)


def test_validate_rejects_bad_bridge():
    issues = validate_edit_changes(900, EditChanges(bridge="eth0"))
    assert any("Bridge" in i for i in issues)


def test_validate_rejects_non_positive_disk_add():
    issues = validate_edit_changes(900, EditChanges(disk_gb_add=0))
    assert any("Disk" in i for i in issues)
    issues = validate_edit_changes(900, EditChanges(disk_gb_add=-10))
    assert any("Disk" in i for i in issues)


def test_validate_passes_valid_changes():
    issues = validate_edit_changes(900, EditChanges(
        name="macos-fast",
        cores=8,
        memory_mb=16384,
        bridge="vmbr1",
        disk_gb_add=64,
    ))
    assert issues == []


# ── build_edit_plan ──────────────────────────────────────────────────


def test_build_edit_plan_always_starts_with_stop():
    steps = build_edit_plan(900, EditChanges(cores=4))
    assert steps[0].title == "Stop VM (if running)"
    assert "qm stop" in steps[0].command
    assert "900" in steps[0].command


def test_build_edit_plan_name_change():
    steps = build_edit_plan(900, EditChanges(name="new-name"))
    cmds = [" ".join(s.argv) for s in steps]
    assert any("--name new-name" in c for c in cmds)


def test_build_edit_plan_cores():
    steps = build_edit_plan(900, EditChanges(cores=6))
    cmds = [" ".join(s.argv) for s in steps]
    assert any("--cores 6" in c for c in cmds)


def test_build_edit_plan_memory():
    steps = build_edit_plan(900, EditChanges(memory_mb=32768))
    cmds = [" ".join(s.argv) for s in steps]
    assert any("--memory 32768" in c for c in cmds)


def test_build_edit_plan_bridge():
    steps = build_edit_plan(900, EditChanges(bridge="vmbr2"))
    cmds = [" ".join(s.argv) for s in steps]
    assert any("--net0" in c and "vmbr2" in c for c in cmds)
    # no current_net0 → falls back to vmxnet3 default
    assert any("vmxnet3" in c for c in cmds)


def test_build_edit_plan_bridge_preserves_mac_from_current_net0():
    config_raw = "name: macos-test\nnet0: vmxnet3=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=0\n"
    steps = build_edit_plan(900, EditChanges(bridge="vmbr1"), current_net0=config_raw)
    net_step = next(s for s in steps if "--net0" in " ".join(s.argv))
    net0_val = net_step.argv[-1]
    assert "vmbr1" in net0_val
    assert "AA:BB:CC:DD:EE:FF" in net0_val  # MAC preserved


def test_build_edit_plan_bridge_preserves_existing_nic_model():
    config_raw = "net0: e1000=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=0\n"
    steps = build_edit_plan(900, EditChanges(bridge="vmbr1"), current_net0=config_raw)
    net_step = next(s for s in steps if "--net0" in " ".join(s.argv))
    net0_val = net_step.argv[-1]
    assert "e1000" in net0_val   # original model preserved
    assert "vmxnet3" not in net0_val


def test_build_edit_plan_disk_extend():
    steps = build_edit_plan(900, EditChanges(disk_gb_add=128))
    resize = next(s for s in steps if "resize" in " ".join(s.argv))
    assert "virtio0" in resize.argv
    assert "+128G" in resize.argv
    assert resize.risk == "action"


def test_build_edit_plan_no_start_by_default():
    steps = build_edit_plan(900, EditChanges(cores=4))
    cmds = [" ".join(s.argv) for s in steps]
    assert not any(c.startswith("qm start") for c in cmds)


def test_build_edit_plan_start_after():
    steps = build_edit_plan(900, EditChanges(cores=4), start_after=True)
    assert steps[-1].argv[:2] == ["qm", "start"]
    assert steps[-1].risk == "action"


def test_build_edit_plan_multiple_changes():
    steps = build_edit_plan(900, EditChanges(cores=4, memory_mb=8192, name="trimmed"))
    titles = [s.title for s in steps]
    assert any("cores" in t.lower() for t in titles)
    assert any("memory" in t.lower() for t in titles)
    assert any("Rename" in t for t in titles)
    # stop is always first
    assert steps[0].title == "Stop VM (if running)"


def test_build_edit_plan_returns_empty_for_no_changes():
    # Empty EditChanges returns no steps — planner is a no-op without changes.
    steps = build_edit_plan(900, EditChanges())
    assert steps == []


def test_validate_rejects_bad_nic_model():
    # nic_model is only validated when bridge is also being changed and nic_model is not None
    issues = validate_edit_changes(900, EditChanges(bridge="vmbr0", nic_model="bad model!"))
    assert any("NIC model" in i for i in issues)


def test_validate_ignores_nic_model_none():
    # nic_model=None (default) is never validated — means "preserve existing"
    issues = validate_edit_changes(900, EditChanges(bridge="vmbr0", nic_model=None))
    assert not any("NIC model" in i for i in issues)


def test_validate_ignores_nic_model_when_no_bridge_change():
    # nic_model is not checked when bridge is not being changed
    issues = validate_edit_changes(900, EditChanges(cores=4, nic_model="bad model!"))
    assert not any("NIC model" in i for i in issues)


def test_validate_rejects_bad_disk_name():
    # disk_name is only validated when disk_gb_add is also being changed
    issues = validate_edit_changes(900, EditChanges(disk_gb_add=64, disk_name="xvda1"))
    assert any("Disk name" in i for i in issues)


def test_validate_ignores_disk_name_when_no_disk_change():
    # disk_name is not checked when disk_gb_add is not being changed
    issues = validate_edit_changes(900, EditChanges(cores=4, disk_name="xvda1"))
    assert not any("Disk name" in i for i in issues)


def test_build_edit_plan_custom_nic_model():
    # Explicit nic_model overrides the fallback
    steps = build_edit_plan(900, EditChanges(bridge="vmbr1", nic_model="e1000"))
    net_step = next(s for s in steps if "--net0" in " ".join(s.argv))
    assert "e1000" in net_step.argv[-1]
    assert "vmxnet3" not in net_step.argv[-1]


def test_build_edit_plan_explicit_nic_model_overrides_existing():
    # If user explicitly sets nic_model, it wins over the preserved model
    config_raw = "net0: vmxnet3=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=0\n"
    steps = build_edit_plan(900, EditChanges(bridge="vmbr1", nic_model="e1000"), current_net0=config_raw)
    net_step = next(s for s in steps if "--net0" in " ".join(s.argv))
    net0_val = net_step.argv[-1]
    assert "e1000" in net0_val
    assert "vmxnet3" not in net0_val
    assert "AA:BB:CC:DD:EE:FF" in net0_val  # MAC still preserved


def test_build_edit_plan_custom_disk_name():
    steps = build_edit_plan(900, EditChanges(disk_gb_add=32, disk_name="sata0"))
    resize = next(s for s in steps if "resize" in " ".join(s.argv))
    assert "sata0" in resize.argv
    assert "virtio0" not in resize.argv
    assert steps[0].title == "Stop VM (if running)"
