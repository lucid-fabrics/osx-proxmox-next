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
    assert steps[0].argv[:2] == ["qm", "stop"]
    assert steps[0].argv[2] == "900"


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
    # must preserve vmxnet3 NIC model
    assert any("vmxnet3" in c for c in cmds)


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
    assert steps[0].title == "Stop VM"


def test_build_edit_plan_only_stop_when_no_changes_requested():
    # Empty EditChanges produces only the stop step — caller should validate first.
    steps = build_edit_plan(900, EditChanges())
    assert len(steps) == 1
    assert steps[0].argv[:2] == ["qm", "stop"]
