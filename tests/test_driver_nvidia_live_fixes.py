"""Regression tests for the NVIDIA driver update bugs found live on v0.38.1 (RTX 3060 Ti, VM 984, NullMoth v2.8.1 to v3.0)."""
from __future__ import annotations

import json

import pytest

from driver_fakes import DPve, comps, mac
from osx_proxmox_next import driver_engine as engine, driver_plan, driver_state, driver_targets
from osx_proxmox_next.data.gpu import oc_kext
from osx_proxmox_next.driver_targets import Target


@pytest.fixture
def target(monkeypatch):
    def resolve(kind, choice, get=None):
        if kind == "nvidia":
            tag = choice.nullmoth or "v3.0"
            return Target(kind, {"NullMoth": {"version": tag, "tag": tag, "sha256": "d" * 64, "source": "latest"}})
        return driver_targets.resolve(kind, choice)
    monkeypatch.setattr(driver_plan, "resolve", resolve)


def write(**versions):
    engine.write_record(9, "nvidia", comps(**versions))


def test_an_up_to_date_mac_of_every_card_has_nothing_to_do_on_its_first_plan(target):
    """The first plan of the screen is not a reinstall: no panel, no button (live: NVIDIA up to date showed both)."""
    write(NullMoth="v3.0")
    first = driver_plan.prepare(9, pve=DPve(), reinstall=False)
    assert first.nothing_to_do and first.lines()[-1] == engine.UP_TO_DATE
    assert not driver_plan.prepare(9, pve=DPve(), reinstall=True).nothing_to_do      # only a version the user picked again


def test_a_plan_that_changes_nothing_says_kept_not_reinstalled(target):
    """Live: `driver-update --version v2.8.1` on a Mac at v2.8.1 printed "reinstalled" then "Up to date: nothing to do"."""
    write(NullMoth="v2.8.1")
    plan = driver_plan.prepare(9, driver_targets.Choice(nullmoth="v2.8.1"), pve=DPve())
    assert plan.lines()[1] == "  NullMoth: already v2.8.1, kept" and plan.lines()[-1] == engine.UP_TO_DATE
    assert "Steps:" not in plan.lines()
    wanted = driver_plan.prepare(9, driver_targets.Choice(nullmoth="v2.8.1"), pve=DPve(), reinstall=True)
    assert wanted.lines()[1] == "  NullMoth: v2.8.1, reinstalled" and "Steps:" in wanted.lines()
    assert engine.change_lines("amd", {"Lilu": "1", "WhateverGreen": "2"}, {"Lilu": "1", "WhateverGreen": "2"}, False) == [
        "Lilu: already 1, kept", "WhateverGreen: already 2, kept"]
    assert engine.change_lines("intel", {"WhateverXe": "1", "WhateverXe Graphics": "2"}, {"WhateverXe": "1", "WhateverXe Graphics": "2"},
                               False) == ["WhateverXe: already 1, kept", "WhateverXe Graphics: already 2, kept"]


def test_the_plan_lists_the_steps_the_run_lists_for_a_stopped_and_a_running_mac(target):
    """Live: the plan of a stopped Mac listed 6 steps and the run screen 7 ("Stop the Mac" was added)."""
    for status, count in (("stopped", 6), ("running", 7)):
        write(NullMoth="v2.8.1")
        plan = driver_plan.prepare(9, pve=DPve(status=status))
        listed = [l for l in plan.lines() if l[:3].strip().rstrip(".").isdigit()]
        state = driver_state.new_update("nvidia", "m", "v3.0", plan.step_name(), {}, [], "", plan.running)
        assert len(listed) == len(state["steps"]) == count
        assert [l.split(". ", 1)[1] for l in listed] == [r["title"] for r in state["steps"]]


def test_a_run_without_a_stop_row_skips_it_and_ends_it_quietly():
    state = driver_state.new_update("nvidia", "m", "v3", "NullMoth v3", {}, [], "", running=False)
    assert [r["id"] for r in state["steps"]][0] == "snapshot"
    assert driver_state.begin_step(state, "stop") is False
    driver_state.end_step(state, "stop")        # nothing to end, nothing raised
    assert all(r["state"] == "pending" for r in state["steps"])


def test_error_text_and_ours_are_joined_with_a_full_stop():
    """Live: "... already in use by VMID '479' The driver is staged ..." ran two sentences together."""
    join = engine.join_text
    assert join("PCI device '0000:0a:00.0' already in use by VMID '478'", "The driver is staged.") == (
        "PCI device '0000:0a:00.0' already in use by VMID '478'. The driver is staged.")
    assert join("It failed.", "The Mac is stopped.") == "It failed. The Mac is stopped."
    assert join("", "Only this.", None) == "Only this." and join("Really?", "Yes.") == "Really? Yes."
    assert join("Could not read the disk:", "The Mac is stopped.") == "Could not read the disk. The Mac is stopped."


@pytest.fixture
def rolled_back(monkeypatch):
    """A Mac whose disks are not the ones its record was made for (a thin LVM rollback makes new volumes)."""
    monkeypatch.setattr(driver_plan.driver_disks, "fingerprint", lambda pve, cfg: "after-the-rollback")


def test_the_installed_version_after_a_manual_rollback_comes_from_the_opencore_disk(target, rolled_back):
    """Live: after `qm rollback` the status said "installed version unknown" instead of v2.8.1: the record (v3.0) belongs to
    the disks before the rollback, and the OpenCore disk of the rolled back Mac says what it has."""
    engine.write_record(9, "nvidia", comps(NullMoth="v3.0"), fingerprint="before-the-rollback")
    pve = DPve(bash_output=json.dumps({"NullMoth": "v2.8.1"}))
    plan = driver_plan.prepare(9, pve=pve)
    assert plan.installed == {"NullMoth": "v2.8.1"} and plan.installed_known and plan.verdict == "update"
    assert plan.lines()[1] == "  NullMoth: v2.8.1 to v3.0" and not plan.unknown_why
    assert "marker NullMoth" in [r for r in pve.runs if r[0] == "bash"][0][2]


def test_a_rolled_back_mac_that_left_no_marker_says_why_its_version_is_unknown(target, rolled_back):
    engine.write_record(9, "nvidia", comps(NullMoth="v3.0"), fingerprint="before-the-rollback")
    plan = driver_plan.prepare(9, pve=DPve(bash_output=json.dumps({"NullMoth": ""})))
    assert not plan.installed_known and plan.unknown_why == engine.STALE_RECORD
    running = driver_plan.prepare(9, pve=DPve(status="running"))        # a running Mac's disk is not read
    assert not running.installed_known and running.unknown_why == engine.STALE_RECORD


def test_the_marker_is_written_and_read_on_the_opencore_disk(tmp_path):
    oc_kext.mark(str(tmp_path), "NullMoth", "v2.8.1")
    out = []
    import io
    import sys
    old, sys.stdout = sys.stdout, io.StringIO()
    try:
        oc_kext.marker(str(tmp_path), "NullMoth")
        out = json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old
    assert out == {"NullMoth": "v2.8.1"}
    (tmp_path / "osx-next-NullMoth.version").write_text("not a version!\n")      # never believes what is not a version
    sys.stdout = io.StringIO()
    try:
        oc_kext.marker(str(tmp_path), "NullMoth")
        assert json.loads(sys.stdout.getvalue()) == {"NullMoth": ""}
    finally:
        sys.stdout = old
    for bad in (("../x", "v1"), ("NullMoth", "v1; rm")):
        with pytest.raises(SystemExit):
            oc_kext.mark(str(tmp_path), *bad)
