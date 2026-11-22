"""The dangerous sequences of "update the driver": a run that failed or was cut off is finished from the step it stopped
at, with the Mac looked at again; the lock, the failure text, the AMD pair and the undo checks."""
from __future__ import annotations

import os

import pytest

from driver_fakes import DPve, comps, foreign_lock, mac
from osx_proxmox_next import driver_core, driver_engine as engine, driver_nvidia, driver_plan, driver_run
from osx_proxmox_next.driver_core import DriverError
from osx_proxmox_next.driver_targets import Choice, resolve as real_resolve
from test_driver_run import (  # noqa: F401 - fixtures and helpers
    NEW_SNAP, OLD_SNAP, VERIFIED, amd_target, amd_zips, env_for, nv_target, plan_for, rec, small_vm_ids, stubs,
)

AMFI = '{"AMFIPass.kext": "%s"}' % driver_nvidia.AMFIPASS.version
ORIGINAL = {"hostpci0": "0000:01:00,pcie=0", "vga": "none", "args": mac()["args"], "boot": "order=ide0;virtio0"}
NV_STEPS = ("stop", "snapshot", "takeoff", "stage", "putback", "start", "verify")


def cut_off(states, phase="failed", staged=False, **extra):
    """The state file a run that stopped left behind: one row state per step of an NVIDIA update."""
    steps = [{"id": i, "title": t, "done": d, "state": st} for (i, t), (_i, d), st in zip(
        engine.step_titles("nvidia", "NullMoth v2.9"), engine.step_titles("nvidia", "NullMoth v2.9", done=True), states)]
    state = {"kind": "nvidia", "phase": phase, "snapshot": NEW_SNAP, "original": ORIGINAL, "steps": steps, "card": ORIGINAL["hostpci0"],
             "choice": {"NullMoth": "v2.9"}, "previous": [{"name": "NullMoth", "version": "v2.8.1"}], "staged": staged,
             "payload_ref": "local-lvm:vm-9-disk-11" if staged else ""}
    engine.write_update(9, dict(state, **extra))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(driver_plan, "resolve", lambda kind, choice, get=None: nv_target() if kind == "nvidia" else real_resolve(kind, choice))


def nv_pve(**kw):
    kw.setdefault("bash_output", [AMFI])
    kw.setdefault("snapshots", [NEW_SNAP])
    return DPve(**kw)


def first_run(stubs, rec, tmp_path, pve):
    plan = plan_for("nvidia", pve)
    plan.target = nv_target()
    return driver_run.run_update(plan, env_for(pve, rec, tmp_path))


def bash_runs(pve):
    return [r for r in pve.runs if r[0] == "bash"]


def finish(pve, rec, tmp_path):
    return driver_run.finish_interrupted(9, env_for(pve, rec, tmp_path, ask_first_boot=True))


# ── B1: Finish looks at the Mac again ────────────────────────────────

def test_finish_after_a_driver_that_was_not_confirmed_checks_again_and_stages_nothing(tmp_path, stubs, rec):
    stubs.guest = ["failed: no kexts"]
    pve = nv_pve(halts=())
    assert not first_run(stubs, rec, tmp_path, pve).ok
    assert "hostpci0" in pve.cfg and len(stubs.staged) == 1 and engine.read_update(9)["phase"] == "failed"
    before = (len(bash_runs(pve)), list(pve.calls))
    stubs.guest = [VERIFIED]
    res = finish(pve, rec, tmp_path)
    assert res.ok, res.error
    later = pve.calls[len(before[1]):]
    assert len(stubs.staged) == 1 and len(bash_runs(pve)) == before[0]                 # not staged again, the OpenCore disk not touched
    assert ("set", "9", "--delete", "hostpci0") not in later and not [c for c in later if c[0] in ("shutdown", "snapshot")]
    assert engine.read_record(9)["components"][0]["version"] == "v2.9" and engine.read_update(9)["phase"] == "done"


def test_finish_after_a_staging_failure_stages_with_the_card_off_and_the_mac_stopped(tmp_path, stubs, rec):
    stubs.stage_error = "The staging command did not start after 3 tries."
    pve = nv_pve()
    assert not first_run(stubs, rec, tmp_path, pve).ok
    assert "hostpci0" in pve.cfg                                                # the failed run put the card back
    pve.status = "running"                                                      # the user started the Mac meanwhile
    stubs.stage_error = None
    res = finish(pve, rec, tmp_path)
    assert res.ok, res.error
    assert stubs.at_stage == [(False, "stopped")]                               # never with the card on, never on a running Mac
    assert any(c[0] == "shutdown" for c in pve.calls) and ("set", "9", "--hostpci0", "0000:01:00,pcie=0") in pve.calls
    assert pve.calls_of("snapshot") == [("snapshot", "9", NEW_SNAP, "--description", "before driver update to v2.9")]   # not saved twice


def test_finish_after_an_amd_failure_stops_the_mac_the_user_started_before_it_writes_the_disk(tmp_path, amd_zips, rec):
    bad = DPve(bash_ok=False, bash_output="ERROR: mount failed", snapshots=[])
    plan = plan_for("amd", bad, installed={"Lilu": "1.6.8", "WhateverGreen": "1.6.0"})
    plan.target = amd_target()
    assert not driver_run.run_update(plan, env_for(bad, rec, tmp_path)).ok
    assert engine.read_update(9)["phase"] == "failed" and bad.status == "stopped"
    good = DPve(cfg=bad.cfg, status="running", bash_output="VERSION 1.7.2\nVERSION 1.7.0\n", snapshots=[NEW_SNAP])    # the user started it
    res = finish(good, rec, tmp_path)
    assert res.ok, res.error
    assert good.bash_status == ["stopped"]                                      # the OpenCore disk was written with the Mac stopped
    assert [c[0] for c in good.calls if c[0] in ("shutdown", "start")] == ["shutdown", "start"]


def test_finish_after_a_kill_following_the_staging_restores_the_card_without_staging_again(tmp_path, stubs, rec):
    cut_off(["done", "done", "done", "done", "pending", "pending", "pending"], phase="running", staged=True)
    pve = nv_pve(cfg=mac(hostpci0=None, vga="std", boot="order=sata1;virtio0;ide0"))
    res = finish(pve, rec, tmp_path)
    assert res.ok, res.error
    assert stubs.staged == [] and not pve.calls_of("snapshot") and ("set", "9", "--hostpci0", "0000:01:00,pcie=0") in pve.calls
    assert ("set", "9", "--boot", "order=ide0;virtio0") in pve.calls and pve.calls_of("start")


def test_finish_after_a_kill_once_the_mac_runs_again_only_checks_the_driver(tmp_path, stubs, rec):
    cut_off(["done", "done", "done", "done", "done", "done", "active"], phase="running", staged=True)
    pve = nv_pve(status="running", halts=())
    res = finish(pve, rec, tmp_path)
    assert res.ok, res.error
    assert stubs.staged == [] and not [c for c in pve.calls if c[0] in ("shutdown", "set")] and not bash_runs(pve)


def test_a_mac_that_cannot_be_stopped_is_refused_with_the_way_back(tmp_path, stubs, rec):
    cut_off(["done", "done", "done", "fail", "pending", "pending", "pending"])

    class Stuck(DPve):
        def qm(self, *a):
            from osx_proxmox_next.infrastructure import CommandResult
            return CommandResult(True, 0, "status: running") if a[0] == "status" else super().qm(*a)
    pve = Stuck(cfg=mac(hostpci0=None), status="running", snapshots=[NEW_SNAP])
    res = finish(pve, rec, tmp_path)
    assert not res.ok and "would not stop" in res.error and "driver-undo goes back to the snapshot of 2026-11-21 10:30" in res.error
    assert stubs.staged == []


def test_a_resumed_run_that_has_no_staged_payload_to_check_says_so(tmp_path, stubs, rec):
    cut_off(["done", "done", "done", "done", "done", "done", "active"], phase="running", staged=False)
    res = finish(nv_pve(status="running", halts=()), rec, tmp_path)
    assert not res.ok and "no staged driver to check" in res.error


def test_a_no_snapshot_run_is_finished_without_typing_the_phrase_again(tmp_path, stubs, rec):
    cut_off(["done", "done", "done", "fail", "pending", "pending", "pending"], snapshot="", no_snapshot=True)
    pve = nv_pve(cfg=mac(hostpci0=None), storage="Name Type\nlocal-lvm lvm\n", snapshots=[])
    res = finish(pve, rec, tmp_path)
    assert res.ok, res.error and not pve.calls_of("snapshot")


# ── S1, S3: the lock and what a failure says ─────────────────────────

def test_the_lock_is_held_for_the_whole_run_and_freed_after_a_failure(tmp_path, stubs, rec):
    seen = []
    pve = nv_pve()
    stubs.stage_error = "boom"
    plan = plan_for("nvidia", pve)
    plan.target = nv_target()
    env = env_for(pve, rec, tmp_path)
    env.on_step = lambda sid, state: seen.append(engine.lock_holder(9))
    assert not driver_run.run_update(plan, env).ok
    assert seen and set(seen) == {os.getpid()} and engine.lock_holder(9) == 0
    other = foreign_lock(engine.lock_path(9))                    # a second process cannot run it at the same time
    with pytest.raises(DriverError, match="Another driver update"):
        driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    other.close()


def test_a_failure_says_what_changed_and_whether_the_mac_runs(tmp_path, stubs, rec, monkeypatch):
    pve = nv_pve(bash_output=['{"AMFIPass.kext": "1.3.0"}'])        # the driver is staged, then AMFIPass cannot be replaced
    monkeypatch.setattr(driver_nvidia.driver_oc, "replace_kexts", lambda *a, **k: (_ for _ in ()).throw(driver_nvidia.driver_oc.OcError("disk busy")))
    res = first_run(stubs, rec, tmp_path, pve)
    assert not res.ok and "disk busy" in res.error
    assert "The driver is staged inside macOS and installs at the next start. The Mac is stopped." in res.error
    plan = plan_for("nvidia", DPve())
    plan.target = nv_target()
    monkeypatch.setattr(driver_nvidia, "_download", lambda *a: (_ for _ in ()).throw(RuntimeError("offline")))
    assert "Nothing on the Mac's disks was changed." in driver_run.run_update(plan, env_for(DPve(), rec, tmp_path)).error


def test_the_recovery_disks_a_failed_staging_leaves_on_a_stopped_mac_are_dropped(tmp_path, stubs, rec, monkeypatch):
    pve = nv_pve(cfg=mac(sata1="local-lvm:vm-9-disk-20,media=disk", sata2="local-lvm:vm-9-disk-21,media=disk"))
    stubs.stage_error = "boom"
    monkeypatch.setattr(driver_nvidia, "_stage", lambda u, *a: (_ for _ in ()).throw(driver_core.DriverError("killed")))
    assert not first_run(stubs, rec, tmp_path, pve).ok
    assert "sata1" not in pve.cfg and "sata2" not in pve.cfg


# ── S4: the AMD pair ─────────────────────────────────────────────────

def test_an_amd_update_replaces_only_the_kext_that_is_behind(tmp_path, amd_zips, rec):
    pve = DPve(bash_output="VERSION 1.7.0\n", snapshots=[OLD_SNAP])
    plan = plan_for("amd", pve, installed={"Lilu": "1.7.9", "WhateverGreen": "1.6.9"})   # Lilu is newer than the pin
    plan.target = amd_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path, ask_first_boot=True))
    assert res.ok, res.error
    script = [r for r in pve.runs if r[0] == "bash"][0][2]
    assert "replace WhateverGreen.kext" in script and "replace Lilu.kext" not in script
    assert {c["name"]: c["version"] for c in engine.read_record(9)["components"]} == {"Lilu": "1.7.9", "WhateverGreen": "1.7.0"}
    assert "The kexts that were behind" in res.detail and "Lilu 1.7.9 is newer than the pin" in " ".join(engine.read_update(9)["events"])
    assert any(line.startswith("  Lilu: 1.7.9 is newer than 1.7.2, kept") for line in plan.lines())


# ── S6, S7: undo checks before it touches the Mac ────────────────────

def test_undo_refuses_before_stopping_when_a_newer_snapshot_stands_in_the_way(tmp_path, rec):
    pve = DPve(status="running", snapshots=[NEW_SNAP, "mine"])
    pve_listing = pve.qm

    def listing(*a):
        from osx_proxmox_next.infrastructure import CommandResult
        if a[0] == "listsnapshot":
            return CommandResult(True, 0, f"`-> {NEW_SNAP}  2026  x\n `-> mine  2026  y\n  `-> current  You are here!")
        return pve_listing(*a)
    pve.qm = listing
    with pytest.raises(DriverError, match=r"Newer snapshots exist \(mine\)"):
        driver_run.put_back(9, env_for(pve, rec, tmp_path))
    assert not [c for c in pve.calls if c[0] in ("shutdown", "stop", "rollback")] and pve.status == "running"


def test_a_rollback_that_fails_starts_the_mac_again(tmp_path, rec):
    class NoRollback(DPve):
        def qm(self, *a):
            from osx_proxmox_next.infrastructure import CommandResult
            return CommandResult(False, 1, "locked") if a[0] == "rollback" else super().qm(*a)
    pve = NoRollback(status="running", snapshots=[NEW_SNAP])
    with pytest.raises(DriverError, match="locked"):
        driver_run.put_back(9, env_for(pve, rec, tmp_path))
    assert pve.calls_of("start") == [("start", "9")] and engine.lock_holder(9) == 0


def test_undo_drops_the_disks_a_cut_off_run_imported_after_the_snapshot(tmp_path, rec):
    cfg = mac(sata1="local-lvm:vm-9-disk-20,media=disk", sata2="local-lvm:vm-9-disk-21,media=disk")
    pve = DPve(cfg=cfg, snapshots=[NEW_SNAP])
    pve.snap_cfg = mac()                                              # the snapshot has neither disk
    driver_run.put_back(9, env_for(pve, rec, tmp_path))
    deleted = [c for c in pve.calls if c[:3] == ("set", "9", "--delete")]
    assert ("set", "9", "--delete", "sata1") in deleted and ("set", "9", "--delete", "sata2") in deleted
    assert pve.calls.index(("set", "9", "--delete", "sata2")) < pve.calls.index(("rollback", "9", NEW_SNAP))
    own = DPve(cfg=cfg, snapshots=[NEW_SNAP])
    own.snap_cfg = cfg                                                # disks that the snapshot has are not touched
    driver_run.put_back(9, env_for(own, rec, tmp_path))
    assert not [c for c in own.calls if c[:3] == ("set", "9", "--delete")]


def test_undo_is_refused_when_the_card_setup_changed_since_the_snapshot(tmp_path, rec):
    engine.write_record(9, "nvidia", comps(NullMoth="v2.9"), previous=comps(NullMoth="v2.8.1"), snapshot=NEW_SNAP, card="0000:01:00,pcie=0")
    gone = DPve(cfg=mac(hostpci0=None), snapshots=[NEW_SNAP])         # the card setup was put back from the Mac's view
    with pytest.raises(DriverError, match="card setup of this Mac changed"):
        driver_run.put_back(9, env_for(gone, rec, tmp_path))
    assert not [c for c in gone.calls if c[0] in ("shutdown", "stop", "rollback", "start")]
    other = DPve(cfg=mac(hostpci0="0000:02:00,pcie=0"), snapshots=[NEW_SNAP])
    with pytest.raises(DriverError, match="card setup of this Mac changed"):
        driver_run.put_back(9, env_for(other, rec, tmp_path))
    same = DPve(snapshots=[NEW_SNAP])
    assert driver_run.put_back(9, env_for(same, rec, tmp_path)) == NEW_SNAP


def test_undo_of_an_update_that_took_the_card_off_is_allowed(tmp_path, rec):
    cut_off(["done", "done", "done", "fail", "pending", "pending", "pending"])
    pve = DPve(cfg=mac(hostpci0=None), snapshots=[NEW_SNAP])
    assert driver_run.put_back(9, env_for(pve, rec, tmp_path)) == NEW_SNAP and pve.rollbacks == [NEW_SNAP]


# ── retention: at most one snapshot of this tool per Mac ─────────────

def test_after_an_update_only_the_previous_driver_snapshot_goes(tmp_path, stubs, rec):
    pve = nv_pve(snapshots=[OLD_SNAP, "mine", "osx-next-driver-20261105-0800"])
    assert first_run(stubs, rec, tmp_path, pve).ok
    assert pve.snapshots == ["mine", NEW_SNAP]                         # somebody else's snapshot stays
    failing = nv_pve(snapshots=[OLD_SNAP])
    stubs.guest = ["failed: x"]
    assert not first_run(stubs, rec, tmp_path, failing).ok
    assert failing.snapshots == [NEW_SNAP]                             # the new one replaced it as soon as it was saved: one stays


def test_a_new_snapshot_name_has_seconds_so_a_retry_in_the_same_minute_is_a_new_one():
    assert len(engine.snapshot_name().split("-")[-1]) == 6
    assert not Choice().macos
