"""Fix wave 3: one snapshot per Mac on every outcome, the leftover disks of a manual rollback, the texts, the installed
versions of a running Mac, the macOS release of a Mac this tool made, and the first boot that never reports."""
from __future__ import annotations

import pytest

from driver_fakes import Clock, DPve, comps, mac
from osx_proxmox_next import driver_core, driver_disks, driver_engine as engine, driver_intel, driver_plan, driver_run, igpu_finish
from osx_proxmox_next.domain import SUPPORTED_MACOS, VmConfig
from osx_proxmox_next.driver_plan import DriverPlan
from osx_proxmox_next.nullmoth import detect_macos
from osx_proxmox_next.planner import build_plan
from test_driver_resume import cut_off, first_run, nv_pve
from test_driver_run import (  # noqa: F401 - fixtures and helpers
    NEW_SNAP, OLD_SNAP, amd_target, env_for, intel_target, nv_target, plan_for, rec, small_vm_ids, stubs,
)

MINE = "local-lvm:vm-9-disk-11"           # the payload disk a run of this tool imported
THEIRS = "local-lvm:vm-9-disk-77"         # an unused disk that is the Mac's own


# ── at most one driver snapshot per Mac, on every outcome ────────────

def test_a_failed_update_keeps_exactly_one_snapshot_the_new_one(tmp_path, stubs, rec):
    pve = nv_pve(snapshots=[OLD_SNAP, "mine", "osx-next-driver-20261105-0800"])
    stubs.guest = ["failed: x"]
    assert not first_run(stubs, rec, tmp_path, pve).ok
    assert pve.snapshots == ["mine", NEW_SNAP]                   # theirs stays, ours are one


def test_the_older_snapshot_goes_as_soon_as_the_new_one_is_saved_not_only_at_the_end(tmp_path, stubs, rec):
    pve = nv_pve(snapshots=[OLD_SNAP])
    stubs.stage_error = "staging stopped"                        # the run dies long before it could finish
    assert not first_run(stubs, rec, tmp_path, pve).ok
    assert pve.snapshots == [NEW_SNAP]


def test_a_finish_adds_no_snapshot_and_leaves_one(tmp_path, stubs, rec):
    cut_off(("done", "done", "done", "fail", "pending", "pending", "pending"))
    pve = nv_pve(status="stopped", cfg=mac(hostpci0=None), snapshots=[NEW_SNAP])
    assert driver_run.finish_interrupted(9, env_for(pve, rec, tmp_path)).ok
    assert pve.snapshots == [NEW_SNAP] and not [c for c in pve.calls if c[0] == "snapshot"]


def test_a_good_undo_removes_the_snapshot_it_went_back_to(tmp_path, rec):
    pve = nv_pve(status="running", snapshots=["mine", NEW_SNAP])
    engine.write_record(9, "nvidia", comps(NullMoth="v2.9"), previous=comps(NullMoth="v2.8.1"), snapshot=NEW_SNAP,
                        card=mac()["hostpci0"])
    assert driver_run.put_back(9, env_for(pve, rec, tmp_path)) == NEW_SNAP
    assert pve.rollbacks == [NEW_SNAP] and pve.snapshots == ["mine"]
    assert f"Removed the driver snapshot {NEW_SNAP}" in rec.events
    assert "snapshot" not in engine.read_record(9)               # the record no longer points at it


def test_an_undo_that_cannot_remove_the_snapshot_still_succeeds_and_says_how(tmp_path, rec, monkeypatch):
    pve = nv_pve(status="running", snapshots=[NEW_SNAP])
    engine.write_record(9, "nvidia", comps(NullMoth="v2.9"), previous=comps(NullMoth="v2.8.1"), snapshot=NEW_SNAP,
                        card=mac()["hostpci0"])

    def refuse(*_a, **_k):
        raise driver_run.driver_snapshot.SnapshotError("busy")
    monkeypatch.setattr(driver_run.driver_snapshot, "remove", refuse)
    assert driver_run.put_back(9, env_for(pve, rec, tmp_path)) == NEW_SNAP
    assert any(f"qm delsnapshot 9 {NEW_SNAP}" in e for e in rec.events)


def test_the_plan_says_the_older_snapshot_goes_once_the_new_one_is_saved():
    plan = DriverPlan(9, "m", "nvidia", "sequoia", False, {"NullMoth": "v1"}, True, nv_target(), "update", snapshot=NEW_SNAP,
                      older_snapshots=[OLD_SNAP])
    assert f"The previous driver snapshot {OLD_SNAP} is removed once the new one is saved." in plan.lines()
    assert f"qm delsnapshot 9 {OLD_SNAP}   (the previous driver snapshot)" in driver_plan.commands(plan)


# ── the recovery and payload disk a manual rollback leaves behind ────

def test_every_disk_a_run_creates_is_written_down_the_moment_it_exists(tmp_path, rec):
    pve = nv_pve()
    updater = driver_core.Updater(plan_for("nvidia", pve, nv_target().components), env_for(pve, rec, tmp_path))
    updater.track_disk("local-lvm:vm-9-disk-5")
    assert engine.read_update(9)["disks"] == ["local-lvm:vm-9-disk-5"]
    updater.track_disk("local-lvm:vm-9-disk-6")
    updater.track_disk("local-lvm:vm-9-disk-5")
    assert engine.read_update(9)["disks"] == ["local-lvm:vm-9-disk-5", "local-lvm:vm-9-disk-6"]


def test_the_staging_tells_the_run_about_each_disk_it_imports(tmp_path):
    from test_igpu_finish import Console, Pve
    told = []
    pve = Pve(cfg=mac())
    job = igpu_finish.StageJob(9, pve, Console(), "local-lvm", lambda m: None, on_disk=told.append)
    igpu_finish._import_disk(pve, 9, tmp_path / "x.img", "local-lvm")
    assert job.on_disk == told.append                            # the hook is the run's; stage_recovery calls it per import
    import inspect
    assert inspect.getsource(igpu_finish.stage_recovery).count("job.on_disk(") == 2


def test_an_unused_disk_that_is_the_runs_own_is_removed_and_the_macs_own_stays():
    pve = DPve(cfg=mac(unused0=THEIRS, unused1=MINE))
    gone = driver_disks.drop_unused(pve, 9, driver_disks.own_disks({"disks": [MINE], "payload_ref": ""}))
    assert gone == [MINE] and "unused1" not in pve.cfg and pve.cfg["unused0"] == THEIRS
    assert driver_disks.drop_unused(pve, 9, []) == []


def test_only_the_disks_a_run_wrote_down_and_still_owns_are_its_own():
    assert driver_disks.own_disks({"disks": ["a", "a", ""], "payload_ref": "b"}) == ["a"]     # the payload ref is for Finish, not for removal
    assert driver_disks.own_disks({}) == []


def test_a_disk_the_tool_deleted_is_forgotten_so_a_reused_volume_id_is_never_removed(tmp_path, rec):
    """Proxmox gives the next new disk the lowest free id: a user's data disk can take the id of the payload disk the tool
    deleted, and a rollback then lists it as unused. Nothing of the user's is removed by an id the tool no longer owns."""
    pve = nv_pve(status="stopped", cfg=mac(sata2=MINE, unused0=THEIRS), snapshots=[NEW_SNAP])
    updater = driver_core.Updater(plan_for("nvidia", pve, nv_target().components), env_for(pve, rec, tmp_path))
    updater.track_disk(MINE)
    updater.drop_disk("sata2")                                   # the tool deletes its payload disk
    assert engine.read_update(9)["disks"] == []
    pve.cfg["unused1"] = MINE                                    # later the user's new disk gets the same id and a rollback lists it
    updater.persist()
    assert driver_disks.drop_unused(pve, 9, driver_disks.own_disks(engine.read_update(9))) == []
    assert pve.cfg["unused1"] == MINE


def test_a_disk_a_step_deleted_behind_the_runs_back_is_forgotten_at_the_end(tmp_path, rec):
    pve = nv_pve(status="stopped", cfg=mac(sata2=MINE), snapshots=[NEW_SNAP])
    updater = driver_core.Updater(plan_for("nvidia", pve, nv_target().components), env_for(pve, rec, tmp_path))
    updater.track_disk(MINE)
    updater.track_disk("local-lvm:vm-9-disk-12")
    updater.forget_gone_disks()                                  # the second one is not on the Mac any more
    assert engine.read_update(9)["disks"] == [MINE]


def test_an_add_or_forget_for_a_mac_with_no_run_writes_nothing(tmp_path, monkeypatch):
    """A card setup that runs after a driver update in the same shell must not leave a state file that looks like a
    stopped update."""
    monkeypatch.setenv("DRIVER_STATE_DIR", str(tmp_path))
    assert engine.main(["x", "update-add-disk", "9", MINE]) == 0 and engine.main(["x", "update-forget-disk", "9", MINE]) == 0
    assert not engine.update_path(9).exists() and engine.unfinished(9) == {}
    engine.write_update(9, {"kind": "nvidia", "steps": []})
    engine.main(["x", "update-add-disk", "9", MINE])
    assert engine.read_update(9)["disks"] == [MINE]
    engine.main(["x", "update-forget-disk", "9", MINE])
    assert engine.read_update(9)["disks"] == []


def test_undo_removes_the_payload_disk_a_manual_rollback_left_as_unused(tmp_path, rec):
    pve = nv_pve(status="stopped", cfg=mac(unused0=THEIRS, unused1=MINE), snapshots=[NEW_SNAP])
    engine.write_update(9, {"kind": "nvidia", "phase": "failed", "snapshot": NEW_SNAP, "steps": [], "disks": [MINE],
                            "card": mac()["hostpci0"]})
    driver_run.put_back(9, env_for(pve, rec, tmp_path))
    assert "unused1" not in pve.cfg and pve.cfg["unused0"] == THEIRS
    assert any("Removed the leftover disk of the update" in e for e in rec.events)


def test_finish_removes_the_leftover_disk_before_it_stages_again(tmp_path, stubs, rec):
    cut_off(("done", "done", "done", "fail", "pending", "pending", "pending"), disks=[MINE])
    pve = nv_pve(status="stopped", cfg=mac(hostpci0=None, unused1=MINE, unused0=THEIRS), snapshots=[NEW_SNAP])
    assert driver_run.finish_interrupted(9, env_for(pve, rec, tmp_path)).ok
    assert "unused1" not in pve.cfg and pve.cfg["unused0"] == THEIRS


def test_a_leftover_disk_that_a_device_map_still_holds_is_told_with_the_command_and_the_undo_goes_on(tmp_path, rec, monkeypatch):
    pve = nv_pve(status="stopped", cfg=mac(unused1=MINE), snapshots=[NEW_SNAP])
    engine.write_record(9, "nvidia", comps(NullMoth="v2.9"), previous=comps(NullMoth="v2.8.1"), snapshot=NEW_SNAP, card=mac()["hostpci0"])
    engine.write_update(9, {"kind": "nvidia", "phase": "failed", "snapshot": NEW_SNAP, "steps": [], "disks": [MINE],
                            "card": mac()["hostpci0"]})

    def held(*_a, **_k):
        raise driver_disks.MapsHeld("dmsetup remove pve-vm--9--disk--11p1")
    monkeypatch.setattr(driver_disks, "drop_unused", held)
    assert driver_run.put_back(9, env_for(pve, rec, tmp_path)) == NEW_SNAP
    assert any("Could not remove the leftover disk" in e and "dmsetup remove pve-vm--9--disk--11p1" in e for e in rec.events)
    assert pve.rollbacks == [NEW_SNAP] and engine.installed_versions(engine.read_record(9)) == {"NullMoth": "v2.8.1"}


# ── a Finish starts a fresh list ─────────────────────────────────────

def test_a_resumed_run_has_no_clock_of_the_run_before():
    state = {"steps": [{"id": "stop", "state": "done", "started": 1.0, "ended": 156.0},
                       {"id": "stage", "state": "fail", "started": 160.0, "ended": 230.0},
                       {"id": "start", "state": "pending"}]}
    engine.reset_for_resume(state)
    rows = state["steps"]
    assert rows[0] == {"id": "stop", "state": "done", "carried": True}
    assert rows[1] == {"id": "stage", "state": "pending"} and rows[2] == {"id": "start", "state": "pending"}
    assert state["resume_at"] == 1 and state["partial"] == "stage"


def test_what_a_cut_off_step_left_is_still_told_by_a_resumed_run_that_fails_early():
    state = {"kind": "nvidia", "partial": "stage", "steps": [{"id": "stage", "state": "pending"}]}
    assert "partly staged" in engine.failure_text(state, False)


# ── texts and parity ─────────────────────────────────────────────────

def test_the_undo_line_names_the_bash_command_for_the_bash_installer():
    assert engine.before_facts("nvidia", NEW_SNAP)[-1] == "Afterwards r undoes the driver update (osx-next-cli driver-undo)."
    line = engine.before_facts("nvidia", NEW_SNAP, undo="osx-proxmox-next.sh driver-undo 9")[-1]
    assert line == "Afterwards osx-proxmox-next.sh driver-undo 9 undoes the driver update."


def test_finish_says_what_finish_does_and_that_no_snapshot_is_saved():
    facts = engine.finish_facts("nvidia", NEW_SNAP)
    assert f"No new snapshot is saved: {NEW_SNAP} from before the update stays." in facts
    assert not [f for f in facts if "is saved first" in f]
    assert "No new snapshot is saved: this update ran without one." in engine.finish_facts("amd", "")
    assert "The card is taken off when a step needs it, and put back." not in engine.finish_facts("amd", NEW_SNAP)


def test_the_plan_of_a_stopped_update_is_the_finish_plan_not_a_new_update():
    resume = {"snapshot": NEW_SNAP, "steps": [{"id": "stop", "state": "done"}, {"id": "snapshot", "state": "done"},
                                              {"id": "takeoff", "state": "done"}, {"id": "stage", "state": "fail"},
                                              {"id": "putback", "state": "pending"}]}
    plan = DriverPlan(9, "m", "nvidia", "sequoia", False, {"NullMoth": "v1"}, True, nv_target(), "update", snapshot="osx-next-driver-NEW",
                      interrupted=True, resume=resume)
    lines = plan.lines()
    assert lines[0] == "Finish the NVIDIA driver update of m (VM 9)"
    assert "  1. Stage NullMoth v2.9" in lines and not any("Save a snapshot" in l for l in lines)
    assert not any("is saved first" in l for l in lines) and any("No new snapshot is saved" in l for l in lines)
    cmds = driver_plan.commands(plan)
    assert not [c for c in cmds if c.startswith("qm snapshot")] and f"qm rollback 9 {NEW_SNAP}   (r, driver-undo)" in cmds


def test_amd_and_intel_versions_of_a_running_mac_come_from_the_record_and_say_so(tmp_path, monkeypatch):
    monkeypatch.setenv("DRIVER_STATE_DIR", str(tmp_path))
    engine.write_record(9, "amd", comps(Lilu="1.7.2", WhateverGreen="1.7.0"), base=str(tmp_path))
    where = {}
    have, known = driver_plan.installed_versions(DPve(status="running"), 9, "amd", True, None, where)
    assert known and have["Lilu"] == "1.7.2" and where["record"] is True
    assert engine.FROM_RECORD == "(from the last update)"


def test_a_stopped_mac_whose_disk_says_the_versions_is_not_marked(tmp_path, monkeypatch):
    monkeypatch.setenv("DRIVER_STATE_DIR", str(tmp_path))
    engine.write_record(9, "amd", comps(Lilu="1.7.2", WhateverGreen="1.7.0"), base=str(tmp_path))
    pve = DPve(status="stopped", bash_output='{"Lilu.kext": "1.7.2", "WhateverGreen.kext": "1.7.0"}')
    where = {}
    driver_plan.installed_versions(pve, 9, "amd", False, None, where)
    assert where["record"] is False


def test_nvidia_has_only_the_record_and_is_never_marked(tmp_path, monkeypatch):
    monkeypatch.setenv("DRIVER_STATE_DIR", str(tmp_path))
    engine.write_record(9, "nvidia", comps(NullMoth="v2.8.1"), base=str(tmp_path))
    where = {}
    driver_plan.installed_versions(DPve(status="running"), 9, "nvidia", True, None, where)
    assert where["record"] is False


def test_the_cli_status_marks_versions_that_come_from_the_record(tmp_path, monkeypatch, capsys):
    from osx_proxmox_next import driver_cli
    plan = DriverPlan(9, "m", "amd", "sequoia", True, {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}, True, amd_target(), "current",
                      from_record=True)
    monkeypatch.setattr(driver_cli, "prepare", lambda *a, **k: plan)
    monkeypatch.setattr(driver_cli, "get_proxmox_adapter", lambda: DPve())
    import argparse
    assert driver_cli.run_status(argparse.Namespace(vmid=100, json=False)) == 0
    assert "Installed: Lilu 1.7.2 and WhateverGreen 1.7.0 (from the last update)" in capsys.readouterr().out


def test_every_run_that_starts_the_mac_says_so(tmp_path, rec):
    pve = DPve(status="stopped", halts=())
    updater = driver_core.Updater(plan_for("amd", pve, amd_target().components), env_for(pve, rec, tmp_path))
    updater.state["steps"] = engine.new_update("amd", "m", "l", "x", {}, [])["steps"]
    updater.start()
    assert "Started the Mac" in rec.events
    rec.events.clear()
    updater.start()                                              # already running: nothing was started, nothing is said
    assert rec.events == []


# ── the macOS release of a Mac this tool made ────────────────────────

@pytest.mark.parametrize("release", sorted(SUPPORTED_MACOS))
def test_a_mac_made_by_this_tool_says_its_release_whatever_it_is_called(release):
    cfg = VmConfig(vmid=980, name="drvtest-980", macos=release, cores=4, memory_mb=8192, disk_gb=64, bridge="vmbr0", storage="local-lvm")
    create = build_plan(cfg)[0 if SUPPORTED_MACOS[release]["channel"] != "preview" else 1].argv
    assert create[:3] == ["qm", "create", "980"]
    vm = {create[i][2:]: create[i + 1] for i in range(3, len(create) - 1) if create[i].startswith("--")}
    assert vm["name"] == "drvtest-980"                           # the name does not say it
    assert detect_macos(vm) == release                           # the description does
    assert "osx-proxmox-next" in vm["description"]


def test_a_mac_made_before_has_nothing_to_find_and_the_refusal_names_the_flag():
    plan = driver_plan.DriverPlan(980, "drvtest-980", "nvidia")
    plan.cfg = {"name": "drvtest-980", "args": "-device isa-applesmc", "hostpci0": "0000:0a:00"}
    plan.macos = detect_macos(plan.cfg) or ""
    driver_plan._kind_checks(plan)
    assert plan.macos == "" and any("Pass --macos" in r for r in plan.refusals)


# ── the first boot that never reports ────────────────────────────────

class OpsPve:
    """A running Mac whose disk counter does what the test says."""

    def __init__(self, clock, ops, result="staged", stops_at=None):
        self.clock, self.ops, self.result, self.stops_at, self.calls = clock, ops, result, stops_at, []

    def qm(self, *a):
        self.calls.append(a)
        from osx_proxmox_next.infrastructure import CommandResult
        if a[0] == "status" and "--verbose" in a:
            n = self.ops(self.clock.now)
            return CommandResult(True, 0, f"blockstat:\n\tvirtio0:\n\t\trd_operations: {n}\n\t\twr_operations: 0\n")
        if a[0] == "status":
            stopped = self.stops_at is not None and self.clock.now >= self.stops_at
            return CommandResult(True, 0, "status: stopped" if stopped else "status: running")
        return CommandResult(True, 0, "")


@pytest.fixture
def clock():
    return Clock()


def watch(pve, clock, monkeypatch, timeout=1500, tahoe=False):
    monkeypatch.setattr(igpu_finish, "_read_payload_result", lambda pve_, ref: pve.result)
    return igpu_finish.watch_first_boot(pve, 9, timeout, tahoe, "ref", clock.monotonic, clock.sleep)


def test_a_mac_that_does_no_disk_io_and_reports_nothing_is_stuck_after_ten_minutes_not_twenty_five(clock, monkeypatch):
    out = watch(OpsPve(clock, lambda t: 100 if t < 60 else 150), clock, monkeypatch)
    assert out.stuck and not out.stopped and 600 <= out.idle < 700 and out.waited < 800


def test_a_mac_that_keeps_working_is_waited_for_until_the_timeout(clock, monkeypatch):
    out = watch(OpsPve(clock, lambda t: int(t)), clock, monkeypatch, timeout=900)
    assert not out.stuck and not out.stopped and out.waited >= 900 and out.idle < 60


def test_a_mac_that_powers_itself_off_ends_the_wait_at_once(clock, monkeypatch):
    out = watch(OpsPve(clock, lambda t: int(t), stops_at=200), clock, monkeypatch)
    assert out.stopped and not out.stuck and out.waited < 250


def test_a_mac_with_no_counter_is_never_called_stuck(clock, monkeypatch):
    class NoOps(OpsPve):
        def qm(self, *a):
            from osx_proxmox_next.infrastructure import CommandResult
            if a[0] == "status" and "--verbose" in a:
                return CommandResult(False, 1, "")
            return super().qm(*a)
    out = watch(NoOps(clock, lambda t: 0), clock, monkeypatch, timeout=800)
    assert not out.stuck and out.waited >= 800


def test_tahoe_ends_the_wait_when_the_guest_left_its_result(clock, monkeypatch):
    out = watch(OpsPve(clock, lambda t: int(t), result="installed"), clock, monkeypatch, tahoe=True)
    assert not out.stuck and not out.stopped and out.waited < 100


def test_a_stuck_mac_with_a_result_is_not_called_stuck(clock, monkeypatch):
    out = watch(OpsPve(clock, lambda t: 5, result="installed"), clock, monkeypatch, timeout=900)
    assert not out.stuck


def test_the_text_says_what_was_seen_and_what_to_do_when_the_guest_never_ran():
    text = engine.first_boot_text("staged", 700, 650)
    assert "did not report back within 11 minutes" in text and "the staged package never ran" in text
    assert "its disks did nothing for the last 10 minutes" in text and "stopped at startup" in text
    assert "IntelAccelerator::probe" in text and "osx-next-cli igpu-watchdog <vmid>" in text and "iGPU setup is the cause" in text
    busy = engine.first_boot_text("", 1500, 30)
    assert "did nothing" not in busy and "within 25 minutes" in busy and "was left" not in busy and "was left" not in text


def test_a_failure_the_guest_reported_is_told_as_it_said_it():
    assert engine.first_boot_text("failed: installer error (try 1 of 3)", 100, 0) == (
        "The graphics package is not installed: failed: installer error (try 1 of 3).")
    assert engine.first_boot_text("not installed: WhateverXe not loaded (try 1 of 3)", 100, 0) == (
        "The graphics package is not installed: WhateverXe not loaded (try 1 of 3).")


def test_an_intel_update_whose_mac_never_reports_stops_it_and_leaves_it_stopped(tmp_path, stubs, rec, monkeypatch):
    cfg = mac(machine="pc", hostpci0="0000:00:02.0,legacy-igd=1", name="macos-sequoia")
    stubs.package = ""
    monkeypatch.setattr(driver_intel, "watch_first_boot", lambda *a, **k: igpu_finish.FirstBoot(stopped=False, waited=720, idle=650, stuck=True))
    pve = DPve(cfg=cfg)
    plan = plan_for("intel", pve, installed={"WhateverXe": "1.2.0"})
    plan.target = intel_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert not res.ok and "stopped at startup" in res.error
    assert ("stop", "9") in pve.calls and not [c for c in pve.calls if c[0] == "start" and pve.calls.index(c) > pve.calls.index(("stop", "9"))]
    assert any("did nothing for 10 minutes" in e for e in rec.events)
    assert "The Mac is stopped." in res.error                    # the failure text agrees with the Mac


def test_a_guest_that_said_it_failed_leaves_the_mac_running_as_before(tmp_path, stubs, rec):
    cfg = mac(machine="pc", hostpci0="0000:00:02.0,legacy-igd=1", name="macos-sequoia")
    stubs.package = "failed: installer error (try 1 of 3)"
    pve = DPve(cfg=cfg, halts=())
    plan = plan_for("intel", pve, installed={"WhateverXe": "1.2.0"})
    plan.target = intel_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert not res.ok and "installer error" in res.error and pve.status == "running"


def test_the_intel_plan_states_the_limit_of_the_update():
    plan = DriverPlan(9, "m", "intel", "sequoia", False, {}, False, intel_target(), "unknown", snapshot=NEW_SNAP)
    driver_plan._kind_checks(plan)
    assert engine.INTEL_BOOT_NOTE in plan.warnings and "Warning: " + engine.INTEL_BOOT_NOTE in plan.lines()
    old = DriverPlan(9, "m", "intel", "monterey", False, {}, False, intel_target(), "unknown")
    driver_plan._kind_checks(old)
    assert engine.INTEL_BOOT_NOTE not in old.warnings           # a release the iGPU cannot do is refused, not warned about
    amd = DriverPlan(9, "m", "amd", "sequoia", False, {}, False, amd_target(), "unknown")
    driver_plan._kind_checks(amd)
    assert not amd.warnings


def test_the_disk_counter_adds_reads_and_writes_of_every_disk(monkeypatch):
    from osx_proxmox_next.infrastructure import CommandResult

    class P:
        def qm(self, *a):
            return CommandResult(True, 0, "blockstat:\n\tide0:\n\t\trd_operations: 3\n\t\twr_operations: 4\n\tvirtio0:\n\t\trd_operations: 10\n\t\twr_operations: 5\n")
    assert igpu_finish.disk_ops(P(), 9) == 22

    class Q:
        def qm(self, *a):
            return CommandResult(True, 0, "status: running")
    assert igpu_finish.disk_ops(Q(), 9) is None


def test_versions_an_undo_read_from_the_disk_are_not_called_from_the_last_update(tmp_path, monkeypatch):
    monkeypatch.setenv("DRIVER_STATE_DIR", str(tmp_path))
    engine.write_record(9, "intel", comps(WhateverXe="0.8.0"), base=str(tmp_path))
    engine.mark_disk_read(9, {"WhateverXe": "0.8.0"}, str(tmp_path))
    where = {}
    have, known = driver_plan.installed_versions(DPve(status="running"), 9, "intel", True, None, where)
    assert known and have == {"WhateverXe": "0.8.0"} and where["record"] is False       # the Mac runs again: the disk was read before
    engine.write_record(9, "intel", comps(WhateverXe="0.8.0"), base=str(tmp_path))     # a record nobody read from the disk
    where = {}
    driver_plan.installed_versions(DPve(status="running"), 9, "intel", True, None, where)
    assert where["record"] is True
