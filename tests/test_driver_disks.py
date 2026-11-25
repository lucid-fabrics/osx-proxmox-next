"""Fix wave 2: device maps and loop devices are released before a volume is rolled back or removed, and the disks tell a
record from a rolled back Mac."""
from __future__ import annotations

import pytest

from driver_fakes import DPve, comps, mac
from osx_proxmox_next import driver_disks, driver_engine as engine, driver_plan, driver_run, driver_snapshot, igpu_finish
from osx_proxmox_next.driver_core import DriverError
from osx_proxmox_next.driver_targets import Target
from osx_proxmox_next.infrastructure import CommandResult
from test_driver_run import NEW_SNAP, env_for, rec, small_vm_ids  # noqa: F401 - fixtures

MAP2, MAP3 = "pve-vm--9--disk--2p1", "pve-vm--9--disk--1p1"      # the Mac's virtio0 and its OpenCore disk


class MapPve(DPve):
    """A host whose logical volumes carry partition maps (what udev and kpartx leave behind) and loop devices."""

    def __init__(self, maps=(MAP2, MAP3), stuck=False, loops=None, uuids=None, **kw):
        super().__init__(**kw)
        self.maps, self.stuck, self.loops, self.detached, self.removed = list(maps), stuck, dict(loops or {}), [], []
        self.uuids = dict(uuids or {})

    def pvesm(self, *a):
        if a[0] == "path":
            return CommandResult(True, 0, "/dev/pve/" + a[1].split(":", 1)[1])
        return super().pvesm(*a)

    def run(self, argv):
        if argv[:2] == ["dmsetup", "ls"]:
            return CommandResult(True, 0, "\n".join(f"{m}\t(253:5)" for m in self.maps) or "No devices found")
        if argv[:2] == ["dmsetup", "remove"]:
            self.removed.append(argv[2])
            if self.stuck:
                return CommandResult(False, 1, "device busy")
            self.maps.remove(argv[2])
            return CommandResult(True, 0, "")
        if argv[:2] == ["losetup", "-j"]:
            return CommandResult(True, 0, "\n".join(self.loops.get(argv[2], [])))
        if argv[:2] == ["losetup", "-d"]:
            self.detached.append(argv[2])
            return CommandResult(True, 0, "")
        if argv[:1] == ["lvs"] and "lv_uuid" in argv:
            uid = self.uuids.get(argv[-1])
            return CommandResult(bool(uid), 0 if uid else 5, "  " + uid if uid else "not an lv")
        return super().run(argv)


def calls(pve, *head):
    return [c for c in pve.calls if c[: len(head)] == head]


def test_the_partition_maps_and_loop_devices_of_a_macs_volumes_go_before_a_rollback():
    pve = MapPve(snapshots=[NEW_SNAP], loops={"/dev/pve/vm-9-disk-2": ["/dev/loop4"]})
    driver_snapshot.rollback(pve, 9, NEW_SNAP)
    assert sorted(pve.removed) == sorted([MAP2, MAP3]) and pve.detached == ["/dev/loop4"] and pve.maps == []
    assert pve.rollbacks == [NEW_SNAP]


def test_a_map_that_will_not_go_stops_the_rollback_with_the_exact_command_and_leaves_no_lock():
    pve = MapPve(stuck=True, snapshots=[NEW_SNAP])
    with pytest.raises(driver_snapshot.SnapshotError) as err:
        driver_snapshot.rollback(pve, 9, NEW_SNAP)
    text = str(err.value)
    assert f"dmsetup remove {MAP2}" in text and f"dmsetup remove {MAP3}" in text and "VM 9" in text
    assert pve.rollbacks == []                       # qm rollback never ran, so Proxmox never set lock: rollback


def test_removing_a_snapshot_releases_the_disks_first_and_refuses_the_same_way():
    pve = MapPve(snapshots=[NEW_SNAP])
    driver_snapshot.remove(pve, 9, NEW_SNAP)
    assert sorted(pve.removed) == sorted([MAP2, MAP3]) and pve.snapshots == []
    stuck = MapPve(stuck=True, snapshots=[NEW_SNAP])
    with pytest.raises(driver_snapshot.SnapshotError, match="dmsetup remove"):
        driver_snapshot.remove(stuck, 9, NEW_SNAP)
    assert stuck.snapshots == [NEW_SNAP]


def test_a_rollback_that_fails_after_proxmox_locked_the_vm_names_the_unlock_command():
    class Locked(MapPve):
        def qm(self, *a):
            if a[0] == "rollback":
                return CommandResult(False, 1, "lvremove failed")
            if a[0] == "config" and "--snapshot" not in a:
                return CommandResult(True, 0, self.text() + "\nlock: rollback")
            return super().qm(*a)
    with pytest.raises(driver_snapshot.SnapshotError) as err:
        driver_snapshot.rollback(Locked(snapshots=[NEW_SNAP], maps=()), 9, NEW_SNAP)
    assert "lock is still set" in str(err.value) and "qm unlock 9" in str(err.value)


def test_a_disk_a_run_drops_is_released_before_proxmox_removes_it():
    cfg = mac(sata1="local-lvm:vm-9-disk-2,media=disk")
    pve = MapPve(cfg=cfg)
    igpu_finish._drop_disk(pve, 9, "sata1")
    first_remove = next(i for i, c in enumerate(pve.calls) if c[:3] == ("set", "9", "--delete"))
    assert pve.removed == [MAP2] and "sata1" not in pve.cfg
    assert first_remove >= 0 and MAP3 in pve.maps                     # only the map of this volume went
    stuck = MapPve(cfg=cfg, stuck=True)
    igpu_finish._drop_disk(stuck, 9, "sata1")                          # best effort while another error is handled
    with pytest.raises(igpu_finish.FinishError, match="dmsetup remove " + MAP2):
        igpu_finish._drop_disk(MapPve(cfg=cfg, stuck=True), 9, "sata1", strict=True)


def test_an_undo_releases_everything_before_the_rollback_and_reports_each_step(rec, tmp_path):
    pve = MapPve(status="running", snapshots=[NEW_SNAP])
    steps = []
    env = env_for(pve, rec, tmp_path)
    env.on_step = lambda sid, st: steps.append((sid, st))
    driver_run.put_back(9, env)
    assert steps == [("stop", "active"), ("stop", "done"), ("release", "active"), ("release", "done"), ("rollback", "active"),
                     ("rollback", "done"), ("start", "active"), ("start", "done")]
    assert pve.maps == [] and pve.rollbacks == [NEW_SNAP]
    assert engine.lock_holder(9) == 0


def test_an_undo_that_cannot_free_the_disks_fails_before_the_rollback_and_says_so(rec, tmp_path):
    pve = MapPve(status="running", snapshots=[NEW_SNAP], stuck=True)
    steps = []
    env = env_for(pve, rec, tmp_path)
    env.on_step = lambda sid, st: steps.append((sid, st))
    with pytest.raises(DriverError, match=f"dmsetup remove {MAP2}"):
        driver_run.put_back(9, env)
    assert pve.rollbacks == [] and ("release", "fail") in steps
    assert pve.status == "running"                                       # the Mac this undo stopped is started again
    assert engine.lock_holder(9) == 0                                    # and the lock is free


def test_disk_refs_skip_drives_without_a_disk():
    cfg = {"ide0": "local-lvm:vm-9-disk-1,media=disk", "ide2": "local:iso/x.iso,media=cdrom", "sata0": "none", "net0": "x",
           "unused0": "local-lvm:vm-9-disk-7", "virtio0": "local-lvm:vm-9-disk-2,size=64G"}
    assert driver_disks.disk_refs(cfg) == ["local-lvm:vm-9-disk-1", "local-lvm:vm-9-disk-7", "local-lvm:vm-9-disk-2"]


def test_the_fingerprint_follows_the_logical_volumes_of_the_disks():
    uuids = {"/dev/pve/vm-9-disk-1": "AAA", "/dev/pve/vm-9-disk-2": "BBB"}
    first = driver_disks.fingerprint(MapPve(uuids=uuids), mac())
    assert first and first == driver_disks.fingerprint(MapPve(uuids=uuids), mac())
    rolled_back = dict(uuids, **{"/dev/pve/vm-9-disk-2": "CCC"})          # a thin LVM rollback made a new volume
    assert driver_disks.fingerprint(MapPve(uuids=rolled_back), mac()) != first
    assert driver_disks.fingerprint(MapPve(uuids={}), mac()) == ""         # no logical volume: nothing to tell


def test_a_record_is_believed_unless_the_disks_are_other_than_the_ones_it_was_made_for():
    uuids = {"/dev/pve/vm-9-disk-1": "AAA"}
    pve = MapPve(uuids=uuids)
    fp = driver_disks.fingerprint(pve, mac())
    assert driver_disks.record_is_true(pve, 9, {}, mac())                                  # no fingerprint stored
    assert driver_disks.record_is_true(pve, 9, {"fingerprint": fp}, mac())
    assert not driver_disks.record_is_true(MapPve(uuids={"/dev/pve/vm-9-disk-1": "ZZZ"}), 9, {"fingerprint": fp}, mac())
    assert driver_disks.record_is_true(MapPve(uuids={}), 9, {"fingerprint": fp}, mac())    # the disks give no identity now


# ── truth comes from the disk, not the record ────────────────────────

def plan_installed(pve, kind, running=False):
    return driver_plan.installed_versions(pve, 9, kind, running, mac())


def test_an_amd_mac_trusts_the_kexts_on_its_opencore_disk_over_its_record():
    engine.write_record(9, "amd", comps(Lilu="1.7.2", WhateverGreen="1.7.0"))
    pve = DPve(bash_output='{"Lilu.kext": "1.6.8", "WhateverGreen.kext": "1.6.7"}')
    have, known = plan_installed(pve, "amd")
    assert known and have == {"Lilu": "1.6.8", "WhateverGreen": "1.6.7"}
    assert driver_plan.driver_oc.read_versions  # the disk was asked for both
    assert plan_installed(DPve(bash_output='{"Lilu.kext": "1.7.2", "WhateverGreen.kext": "1.7.0"}'), "amd")[0]["Lilu"] == "1.7.2"


def test_a_running_amd_mac_is_not_mounted_and_uses_the_record():
    engine.write_record(9, "amd", comps(Lilu="1.7.2", WhateverGreen="1.7.0"))
    pve = DPve(status="running", bash_output="not used")
    assert plan_installed(pve, "amd", running=True)[0] == {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}
    assert [r for r in pve.runs if r[0] == "bash"] == []


def test_an_intel_mac_reads_whateverxe_from_the_opencore_disk_when_it_has_no_record():
    pve = DPve(bash_output='{"WhateverXe.kext": "0.8.0"}')
    have, known = plan_installed(pve, "intel")
    assert known and have == {"WhateverXe": "0.8.0"}                   # the graphics package is only in the record


def test_an_intel_record_that_the_disk_contradicts_is_dropped_whole():
    engine.write_record(9, "intel", comps(WhateverXe="0.8.3", WhateverXe_Graphics="0.4.4"))
    have, known = plan_installed(DPve(bash_output='{"WhateverXe.kext": "0.8.0"}'), "intel")
    assert known and have == {"WhateverXe": "0.8.0"}                   # the package version of that record is stale too
    have, _ = plan_installed(DPve(bash_output='{"WhateverXe.kext": "0.8.3"}'), "intel")
    assert have == {"WhateverXe": "0.8.3", "WhateverXe Graphics": "0.4.4"}    # the disk agrees: the record stands


def test_an_nvidia_record_made_for_other_disks_is_unknown_after_a_rollback(monkeypatch):
    uuids = {"/dev/pve/vm-9-disk-1": "AAA"}
    fp = driver_disks.fingerprint(MapPve(uuids=uuids), mac())
    engine.write_record(9, "nvidia", comps(NullMoth="v3.0"), fingerprint=fp)
    assert plan_installed(MapPve(uuids=uuids, maps=()), "nvidia")[0] == {"NullMoth": "v3.0"}
    have, known = plan_installed(MapPve(uuids={"/dev/pve/vm-9-disk-1": "NEW"}, maps=()), "nvidia")
    assert have == {} and not known                                      # a manual `qm rollback` never leaves "Up to date"
    monkeypatch.setattr(driver_plan, "resolve", lambda kind, choice, get=None: Target("nvidia", {"NullMoth": {
        "version": "v3.0", "tag": "v3.0", "sha256": "", "source": "latest"}}))
    plan = driver_plan.prepare(9, pve=MapPve(uuids={"/dev/pve/vm-9-disk-1": "NEW"}, maps=()))
    assert plan.verdict == "unknown" and not plan.installed_known and not plan.nothing_to_do


def test_an_update_stamps_its_record_with_the_disks_and_an_undo_stamps_it_again(rec, tmp_path, monkeypatch):
    uuids = {"/dev/pve/vm-9-disk-1": "AAA"}
    pve = MapPve(maps=(), uuids=uuids, status="running", snapshots=[NEW_SNAP])
    engine.write_record(9, "nvidia", comps(NullMoth="v3.0"), previous=comps(NullMoth="v2.8.1"), snapshot=NEW_SNAP)
    pve.uuids = {"/dev/pve/vm-9-disk-1": "BBB"}                         # the rollback makes a new volume
    driver_run.put_back(9, env_for(pve, rec, tmp_path))
    record = engine.read_record(9)
    assert record["components"][0]["version"] == "v2.8.1"
    assert record["fingerprint"] == driver_disks.fingerprint(pve, mac())    # true for the rolled back Mac: believed
    assert driver_plan.installed_versions(pve, 9, "nvidia", True, mac())[0] == {"NullMoth": "v2.8.1"}


def test_only_the_boot_and_opencore_disks_tell_a_rolled_back_mac():
    uuids = {"/dev/pve/vm-9-disk-1": "AAA", "/dev/pve/vm-9-disk-2": "BBB"}
    fp = driver_disks.fingerprint(MapPve(uuids=uuids), mac())
    with_extra = mac(scsi5="local-lvm:vm-9-disk-8,size=1G", sata2="local-lvm:vm-9-disk-5,media=disk", unused0="local-lvm:vm-9-disk-7")
    extra = dict(uuids, **{"/dev/pve/vm-9-disk-8": "XXX", "/dev/pve/vm-9-disk-5": "YYY", "/dev/pve/vm-9-disk-7": "ZZZ"})
    assert driver_disks.fingerprint(MapPve(uuids=extra), with_extra) == fp       # a data disk or the staging disks change nothing
    assert driver_disks.boot_refs(with_extra) == ["local-lvm:vm-9-disk-1", "local-lvm:vm-9-disk-2"]


def test_qm_rollback_is_not_cut_off_by_a_signal_and_the_signal_comes_after():
    import os
    import signal
    pve = MapPve(maps=(), snapshots=[NEW_SNAP])
    original = pve.run

    def run(argv):
        if argv[:2] == ["qm", "rollback"]:
            os.kill(os.getpid(), signal.SIGTERM)        # a TERM that arrives while qm works
        return original(argv)
    pve.run = run
    with pytest.raises(KeyboardInterrupt):
        driver_snapshot.rollback(pve, 9, NEW_SNAP)
    assert pve.rollbacks == [NEW_SNAP]                  # the rollback ran to its end first
    assert signal.getsignal(signal.SIGTERM) in (signal.SIG_DFL, signal.default_int_handler) or callable(signal.getsignal(signal.SIGTERM))


def test_an_undo_that_dies_of_something_unexpected_fails_its_step_and_starts_the_mac(rec, tmp_path, monkeypatch):
    pve = MapPve(maps=(), status="running", snapshots=[NEW_SNAP])
    monkeypatch.setattr(driver_run.engine, "revert_record", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    steps = []
    env = env_for(pve, rec, tmp_path)
    env.on_step = lambda sid, st: steps.append((sid, st))
    with pytest.raises(DriverError, match="disk full"):
        driver_run.put_back(9, env)
    assert ("rollback", "fail") in steps and engine.lock_holder(9) == 0
