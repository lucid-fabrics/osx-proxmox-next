"""driver_run: the update of one Mac per kind, the safety snapshot, going back, and an interrupted run."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from driver_fakes import Clock, Console, DPve, comps, foreign_lock, mac
from osx_proxmox_next import (
    driver_amd, driver_core, driver_engine as engine, driver_intel, driver_nvidia, driver_oc, driver_plan, driver_run,
    driver_snapshot, igpu_engine, igpu_finish,
)
from osx_proxmox_next.driver_core import DriverError, RunEnv, Updater
from osx_proxmox_next.driver_plan import DriverPlan
from osx_proxmox_next.driver_targets import Choice, Target
from osx_proxmox_next.executor import ApplyResult, StepResult
from osx_proxmox_next.igpu_finish import FirstBoot

VERIFIED = "verified: kexts=5 display=no ip=10.0.0.9"
NEW_SNAP = "osx-next-driver-20261121-1030"
OLD_SNAP = "osx-next-driver-20261101-0900"


class Rec:
    """What an update reported while it ran."""

    def __init__(self):
        self.steps, self.events = [], []


@pytest.fixture(autouse=True)
def small_vm_ids(monkeypatch):
    """The fakes use VM 9, like the card setup's tests."""
    monkeypatch.setattr(driver_core, "MIN_VMID", 1)


@pytest.fixture
def rec():
    return Rec()


def plan_for(kind, pve, versions=None, installed=None, **kw):
    cfg = pve.cfg
    plan = DriverPlan(9, "macos-sequoia", kind, "sequoia", pve.status == "running", installed or {}, bool(installed),
                      Target(kind, versions), "update", cfg=dict(cfg), snapshot=NEW_SNAP, **kw)
    return plan


def nv_target():
    return Target("nvidia", {"NullMoth": {"version": "v2.9", "tag": "v2.9", "sha256": "", "source": "latest"}})


def intel_target():
    return Target("intel", {"WhateverXe": {"version": "1.3.0", "tag": "v1.3.0", "sha256": "e" * 64, "source": "latest"},
                            "WhateverXe Graphics": {"version": "0.9.2", "tag": "v0.9.2", "sha256": "f" * 64, "source": "latest"}})


def amd_target():
    return driver_plan.resolve("amd", Choice())


def env_for(pve, rec, tmp_path, **kw):
    clock = Clock()
    kw.setdefault("accel", lambda **k: (True, "RCS_TAIL=0x1"))
    return RunEnv(pve, kw.pop("console", Console()), tmp_path, tmp_path, lambda sid, st: rec.steps.append((sid, st)),
                  rec.events.append, clock.monotonic, clock.sleep, find_ip=lambda mac: "10.0.0.7",
                  probe=lambda ip, port: True, **kw)


@pytest.fixture
def stubs(tmp_path, monkeypatch):
    """The downloads, the payload disk and the recovery staging are the card setup's own code: scripted here."""
    s = SimpleNamespace(staged=[], at_stage=[], guest=[VERIFIED], stage_error=None, wait_first=True, package="installed", applied=[],
                        attach_ok=True, payload_ok=True)
    (tmp_path / "a.zip").write_bytes(b"x")
    (tmp_path / "rec.img").write_bytes(b"x")
    (tmp_path / "nm.tar.gz").write_bytes(b"x")
    monkeypatch.setattr(driver_nvidia, "ensure_amfipass", lambda iso: tmp_path / "a.zip")
    monkeypatch.setattr(driver_nvidia, "_download", lambda tag, iso, say, get, read, fetch: (
        {"tag": tag}, tmp_path / "nm.tar.gz", "9" * 64, None, "", ["a note"]))
    monkeypatch.setattr(driver_nvidia.nm, "build_payload", lambda out, files: (s.payload_ok, "disk full"))
    monkeypatch.setattr(driver_nvidia, "resolve_recovery_or_installer_path", lambda cfg: tmp_path / "rec.img")
    monkeypatch.setattr(driver_intel, "resolve_recovery_or_installer_path", lambda cfg: tmp_path / "rec.img")

    def stage(job, payload, recovery, cfg):
        if s.stage_error:
            raise igpu_finish.FinishError(s.stage_error)
        s.staged.append((job.vmid, payload.name, tuple(job.leftover_args), job.tahoe))
        s.at_stage.append(("hostpci0" in job.pve.cfg, job.pve.status))
        return "local-lvm:vm-9-disk-11"
    monkeypatch.setattr(igpu_finish, "stage_recovery", stage)
    monkeypatch.setattr(driver_intel, "stage_recovery", stage)
    monkeypatch.setattr(driver_nvidia, "_wait_result", lambda *a: s.guest.pop(0) if len(s.guest) > 1 else s.guest[0])
    assets = SimpleNamespace(picks={"whateverxe": {"sha256": "e" * 64}})
    assets_map = {"whateverxe": tmp_path / "wex.zip", "amfipass": tmp_path / "a.zip", "graphics": tmp_path / "gfx.pkg"}

    class Assets(dict):
        picks = assets.picks
    monkeypatch.setattr(driver_intel, "ensure_igpu_assets", lambda *a, **k: Assets(assets_map))
    monkeypatch.setattr(igpu_engine, "build_payload", lambda pkg, out: (s.payload_ok, "disk full"))
    monkeypatch.setattr(driver_intel, "watch_first_boot", lambda *a, **k: FirstBoot(stopped=s.wait_first, waited=1500, idle=0))
    monkeypatch.setattr(driver_intel, "_read_payload_result", lambda pve, ref: s.package)

    def apply(steps, execute=True, adapter=None, **k):
        s.applied.append([st.title for st in steps])
        return ApplyResult(s.attach_ok, [] if s.attach_ok else [StepResult("t", "c", False, 1, "")], tmp_path / "log")
    monkeypatch.setattr(driver_intel, "apply_plan", apply)
    return s


def calls_after_snapshot(pve):
    return [c for c in pve.calls if c[0] in ("set", "start", "stop", "shutdown", "snapshot", "delsnapshot")]


# ── NVIDIA ───────────────────────────────────────────────────────────

def test_an_nvidia_update_runs_every_step_and_records_the_new_version(tmp_path, stubs, rec):
    pve = DPve(status="running", snapshots=[OLD_SNAP])
    plan = plan_for("nvidia", pve, nv_target().components, {"NullMoth": "v2.8.1"})
    plan.target = nv_target()
    plan.snapshot_ok = True
    plan.older_snapshots = [OLD_SNAP]
    pve.bash_output = ['{"AMFIPass.kext": "1.4.1"}']
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path, ask_first_boot=True))
    assert res.ok and res.snapshot == NEW_SNAP and "a note" in res.notes
    assert [s for s in rec.steps if s[1] == "done"] == [(i, "done") for i in ("stop", "snapshot", "takeoff", "stage", "putback", "start", "verify")]
    calls = calls_after_snapshot(pve)
    assert calls[0] == ("shutdown", "9", "--timeout", "120")
    assert calls[1] == ("snapshot", "9", NEW_SNAP, "--description", "before driver update to v2.9")
    assert calls[2] == ("delsnapshot", "9", OLD_SNAP)                  # the leftover goes as soon as the new one is saved
    assert calls[3] == ("set", "9", "--delete", "hostpci0")
    # the card, the display and the args go back exactly as the Mac had them, then it starts
    assert ("set", "9", "--hostpci0", "0000:01:00,pcie=0") in calls and ("set", "9", "--vga", "none") in calls
    assert ("set", "9", "--args", mac()["args"]) in calls and ("set", "9", "--boot", "order=ide0;virtio0") in calls
    assert calls.index(("set", "9", "--hostpci0", "0000:01:00,pcie=0")) < max(i for i, c in enumerate(calls) if c[0] == "start")
    assert ("delsnapshot", "9", OLD_SNAP) in calls and pve.snapshots == [NEW_SNAP]
    assert stubs.staged == [(9, "nullmoth-payload-9.img", (), False)]
    record = engine.read_record(9)
    assert record["kind"] == "nvidia" and record["components"][0]["version"] == "v2.9" and record["components"][0]["sha256"] == "9" * 64
    assert record["previous"][0]["version"] == "v2.8.1" and record["snapshot"] == NEW_SNAP
    state = engine.read_update(9)
    assert state["phase"] == "done" and engine.unfinished(9) == {} and engine.lock_holder(9) == 0
    assert "Removed the previous driver snapshot: " + OLD_SNAP in " ".join(state["events"])
    assert "5 extensions loaded" in res.detail and "10.0.0.7" in res.detail
    assert pve.status == "running" and not (tmp_path / "nullmoth-payload-9.img").exists()


def test_amfipass_on_the_opencore_disk_is_replaced_only_when_it_differs(tmp_path, stubs, rec):
    pve = DPve(bash_output=['{"AMFIPass.kext": "1.3.0"}', "1.4.1\n"])
    plan = plan_for("nvidia", pve)
    plan.target = nv_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert res.ok
    scripts = [r[2] for r in pve.runs if r[0] == "bash"]
    assert len(scripts) == 2 and "versions AMFIPass.kext" in scripts[0] and "replace AMFIPass.kext" in scripts[1]
    assert engine.installed_versions(engine.read_record(9))["AMFIPass"] == driver_nvidia.AMFIPASS.version
    pve2 = DPve(bash_output=['{"AMFIPass.kext": "%s"}' % driver_nvidia.AMFIPASS.version])
    plan2 = plan_for("nvidia", pve2)
    plan2.target = nv_target()
    driver_run.run_update(plan2, env_for(pve2, rec, tmp_path))
    assert len([r for r in pve2.runs if r[0] == "bash"]) == 1          # read, found it right, wrote nothing


def test_a_failed_opencore_read_stops_the_update_and_puts_the_card_back(tmp_path, stubs, rec):
    pve = DPve(bash_ok=False, bash_output="mount failed")
    plan = plan_for("nvidia", pve)
    plan.target = nv_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert not res.ok and "Could not read the OpenCore disk" in res.error
    assert "The Mac is stopped." in res.error and "Finish continues it (osx-next-cli driver-update --vmid 9 --finish)." in res.error
    assert "driver-undo goes back to the snapshot of 2026-11-21 10:30 (%s)" % NEW_SNAP in res.error
    assert engine.read_update(9)["error"] == res.error
    assert ("set", "9", "--hostpci0", "0000:01:00,pcie=0") in pve.calls
    state = engine.read_update(9)
    assert state["phase"] == "failed" and state["steps"][3]["state"] == "fail" and engine.unfinished(9)["phase"] == "failed"
    assert ("stage", "fail") in rec.steps and engine.read_record(9) == {}


def test_a_staging_failure_is_a_failed_update_with_the_card_back(tmp_path, stubs, rec):
    stubs.stage_error = "Staging failed: the script left no result."
    pve = DPve()
    plan = plan_for("nvidia", pve)
    plan.target = nv_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert not res.ok and "Staging failed" in res.error and ("set", "9", "--hostpci0", "0000:01:00,pcie=0") in pve.calls
    assert engine.lock_holder(9) == 0


def test_a_driver_the_guest_did_not_confirm_fails_the_update(tmp_path, stubs, rec):
    stubs.guest = ["failed: the driver installer stopped: STOP macOS 14"]
    pve = DPve(bash_output=['{"AMFIPass.kext": "%s"}' % driver_nvidia.AMFIPASS.version])
    plan = plan_for("nvidia", pve)
    plan.target = nv_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert not res.ok and "STOP macOS 14" in res.error and engine.read_record(9) == {}
    assert pve.calls_of("start")        # the Mac was started again, on its old driver
    assert "The driver is staged inside macOS and installs at the next start." in res.error


def test_downloads_fail_before_the_mac_is_touched(tmp_path, stubs, rec, monkeypatch):
    def boom(*a):
        raise driver_nvidia.FinishError("SHA256SUMS and the GitHub digest of NullMoth v2.9 disagree, refused.")
    monkeypatch.setattr(driver_nvidia, "_download", boom)
    pve = DPve(status="running")
    plan = plan_for("nvidia", pve)
    plan.target = nv_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert not res.ok and "disagree" in res.error and not calls_after_snapshot(pve) and pve.status == "running"
    stubs.payload_ok = False
    monkeypatch.setattr(driver_nvidia, "_download", lambda *a: ({"tag": "v2.9"}, tmp_path / "nm.tar.gz", "9" * 64, None, "", []))
    assert "disk full" in driver_run.run_update(plan, env_for(pve, rec, tmp_path)).error


def test_an_amfipass_download_error_is_a_clean_failure(tmp_path, stubs, rec, monkeypatch):
    from osx_proxmox_next.downloader import DownloadError

    def boom(iso):
        raise DownloadError("offline")
    monkeypatch.setattr(driver_nvidia, "ensure_amfipass", boom)
    pve = DPve()
    plan = plan_for("nvidia", pve)
    plan.target = nv_target()
    assert "offline" in driver_run.run_update(plan, env_for(pve, rec, tmp_path)).error


def test_a_missing_recovery_image_is_downloaded(tmp_path, stubs, rec, monkeypatch):
    got = []
    monkeypatch.setattr(driver_nvidia, "resolve_recovery_or_installer_path", lambda cfg: tmp_path / "missing.img")
    import osx_proxmox_next.downloader as dl
    monkeypatch.setattr(dl, "download_recovery", lambda version, iso: got.append(version) or tmp_path / "rec.img")
    pve = DPve(bash_output=['{"AMFIPass.kext": "%s"}' % driver_nvidia.AMFIPASS.version])
    plan = plan_for("nvidia", pve)
    plan.target = nv_target()
    assert driver_run.run_update(plan, env_for(pve, rec, tmp_path)).ok and got == ["sequoia"]


def test_a_picker_that_cannot_restore_is_a_note(tmp_path, stubs, rec):
    pve = DPve(bash_output=['{"AMFIPass.kext": "%s"}' % driver_nvidia.AMFIPASS.version])
    plan = plan_for("nvidia", pve)
    plan.target = nv_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path, console=Console(restore_error="no qm")))
    assert res.ok and any("picker's auto-boot (no qm)" in n for n in res.notes)


# ── the safety snapshot ──────────────────────────────────────────────

def test_without_snapshot_support_the_phrase_is_needed(tmp_path, stubs, rec):
    pve = DPve(bash_output=['{"AMFIPass.kext": "%s"}' % driver_nvidia.AMFIPASS.version])
    plan = plan_for("nvidia", pve)
    plan.target, plan.snapshot_ok, plan.snapshot_reason = nv_target(), False, "virtio0 is on storage x (type lvm)"
    with pytest.raises(DriverError, match="no snapshot"):
        driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert not pve.calls                                               # refused before anything happened
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path, no_snapshot=True, ask_first_boot=True))
    assert res.ok and res.snapshot == "" and not pve.calls_of("snapshot")
    assert any("Going on without a snapshot" in e for e in engine.read_update(9)["events"])


def test_a_snapshot_that_proxmox_refuses_stops_the_update_before_any_change(tmp_path, stubs, rec):
    pve = DPve(snap_ok=False, status="running")
    plan = plan_for("nvidia", pve)
    plan.target = nv_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert not res.ok and "Could not save a snapshot of VM 9" in res.error
    assert not [c for c in pve.calls if c[:3] == ("set", "9", "--delete")]


def test_snapshot_helpers(tmp_path):
    pve = DPve(snapshots=[OLD_SNAP, NEW_SNAP, "mine"])
    assert driver_snapshot.list_tool(pve, 9) == [OLD_SNAP, NEW_SNAP]
    assert driver_snapshot.check_support(pve, mac()) == (True, "")
    assert driver_snapshot.prune_older(pve, 9, NEW_SNAP) == ([OLD_SNAP], [])
    assert pve.snapshots == [NEW_SNAP, "mine"]
    driver_snapshot.rollback(pve, 9, NEW_SNAP)
    assert pve.rollbacks == [NEW_SNAP]
    with pytest.raises(ValueError):
        driver_snapshot.rollback(pve, 9, "mine")

    class Failing(DPve):
        def run(self, argv):
            from osx_proxmox_next.infrastructure import CommandResult
            return CommandResult(False, 1, "nope") if argv[0] == "qm" and argv[1] != "listsnapshot" else super().run(argv)
    bad = Failing(snapshots=[OLD_SNAP, NEW_SNAP])
    assert driver_snapshot.prune_older(bad, 9, NEW_SNAP) == ([], [OLD_SNAP])
    for call in (lambda: driver_snapshot.rollback(bad, 9, NEW_SNAP), lambda: driver_snapshot.remove(bad, 9, NEW_SNAP),
                 lambda: driver_snapshot.create(bad, 9, "v1", NEW_SNAP)):
        with pytest.raises(driver_snapshot.SnapshotError, match="nope"):
            call()


def test_put_it_back_rolls_back_and_reverts_the_record(tmp_path, rec):
    pve = DPve(status="running", snapshots=[OLD_SNAP, NEW_SNAP])
    engine.write_record(9, "nvidia", comps(NullMoth="v2.9"), previous=comps(NullMoth="v2.8.1"), snapshot=NEW_SNAP)
    engine.write_update(9, {"kind": "nvidia", "phase": "failed", "snapshot": NEW_SNAP})
    name = driver_run.put_back(9, env_for(pve, rec, tmp_path))
    assert name == NEW_SNAP                                            # the run's own snapshot
    calls = calls_after_snapshot(pve)
    assert calls[0][:2] == ("shutdown", "9") and pve.rollbacks == [NEW_SNAP] and calls[-1] == ("start", "9")
    assert engine.read_record(9)["components"][0]["version"] == "v2.8.1" and engine.read_update(9) == {}
    assert any(e.startswith("Undid the driver update: the Mac is back to") for e in rec.events)


def test_put_it_back_reads_the_rolled_back_disk_and_shows_its_versions_after_the_mac_runs_again(tmp_path, rec):
    engine.write_record(9, "intel", comps(WhateverXe="0.8.3", WhateverXe_Graphics="0.4.4"),
                        previous=comps(WhateverXe="0.8.2", WhateverXe_Graphics="0.4.3"), snapshot=NEW_SNAP, fingerprint="f" * 24)
    pve = DPve(status="running", snapshots=[NEW_SNAP], bash_output='{"WhateverXe.kext": "0.8.0"}')
    driver_run.put_back(9, env_for(pve, rec, tmp_path))
    record = engine.read_record(9)
    assert pve.bash_status == ["stopped"]                              # the disk was read while the Mac was stopped
    assert engine.installed_versions(record) == {"WhateverXe": "0.8.0", "WhateverXe Graphics": "0.4.3"} and record["disk_read"] is True
    where = {}
    driver_plan.installed_versions(DPve(status="running"), 9, "intel", True, None, where)
    assert where["record"] is False                                    # no "(from the last update)" beside what the disk said


def test_put_it_back_keeps_the_record_when_the_disk_cannot_be_read(tmp_path, rec):
    engine.write_record(9, "intel", comps(WhateverXe="0.8.3"), previous=comps(WhateverXe="0.8.2"), snapshot=NEW_SNAP)
    driver_run.put_back(9, env_for(DPve(snapshots=[NEW_SNAP], bash_ok=False, bash_output="mount failed"), rec, tmp_path))
    record = engine.read_record(9)
    assert engine.installed_versions(record) == {"WhateverXe": "0.8.2"} and "disk_read" not in record
    engine.write_record(9, "nvidia", comps(NullMoth="v2.9"), previous=comps(NullMoth="v2.8.1"), snapshot=NEW_SNAP)
    pve = DPve(snapshots=[NEW_SNAP])
    driver_run.put_back(9, env_for(pve, rec, tmp_path))
    assert pve.bash_status == [] and "disk_read" not in engine.read_record(9)      # NVIDIA has nothing on the OpenCore disk to read


def test_put_it_back_uses_the_newest_snapshot_and_names_the_failures(tmp_path, rec):
    pve = DPve(snapshots=[OLD_SNAP, NEW_SNAP])
    assert driver_run.put_back(9, env_for(pve, rec, tmp_path)) == NEW_SNAP
    with pytest.raises(DriverError, match="nothing to undo"):
        driver_run.put_back(9, env_for(DPve(), rec, tmp_path))
    assert engine.lock_holder(9) == 0
    other = foreign_lock(engine.lock_path(9))                           # another process runs an update
    with pytest.raises(DriverError, match="Another driver update"):
        driver_run.put_back(9, env_for(DPve(snapshots=[NEW_SNAP]), rec, tmp_path))
    other.close()

    class NoRollback(DPve):
        def qm(self, *a):
            from osx_proxmox_next.infrastructure import CommandResult
            return CommandResult(False, 1, "locked") if a[0] == "rollback" else super().qm(*a)
    with pytest.raises(DriverError, match="locked"):
        driver_run.put_back(9, env_for(NoRollback(snapshots=[NEW_SNAP]), rec, tmp_path))


def test_the_safety_snapshot_can_be_removed_after_a_good_first_boot(tmp_path, rec):
    pve = DPve(snapshots=[OLD_SNAP, NEW_SNAP])
    assert driver_run.remove_safety_snapshot(9, env_for(pve, rec, tmp_path)) == NEW_SNAP
    assert pve.snapshots == [OLD_SNAP]                                  # only a snapshot of this tool, the newest
    assert driver_run.remove_safety_snapshot(9, env_for(pve, rec, tmp_path), OLD_SNAP) == OLD_SNAP     # the one the screen named
    with pytest.raises(DriverError, match="nothing to undo"):
        driver_run.remove_safety_snapshot(9, env_for(DPve(snapshots=[NEW_SNAP]), rec, tmp_path), OLD_SNAP)
    with pytest.raises(DriverError):
        driver_run.remove_safety_snapshot(9, env_for(DPve(), rec, tmp_path))
    with pytest.raises(DriverError, match="Could not remove"):
        class Bad(DPve):
            def run(self, argv):
                from osx_proxmox_next.infrastructure import CommandResult
                return CommandResult(False, 1, "busy") if argv[:2] == ["qm", "delsnapshot"] else super().run(argv)
        driver_run.remove_safety_snapshot(9, env_for(Bad(snapshots=[NEW_SNAP]), rec, tmp_path))


# ── stopping the Mac ─────────────────────────────────────────────────

def test_a_mac_that_ignores_the_shutdown_request_is_stopped_by_force_after_two_minutes(tmp_path, rec):
    pve = DPve(status="running", shutdown_ok=False)
    driver_core.stop_mac(env_for(pve, rec, tmp_path), 9)
    assert pve.calls[:3] == [("status", "9"), ("shutdown", "9", "--timeout", "120"), ("status", "9")] or ("stop", "9") in pve.calls
    assert ("stop", "9") in pve.calls and any("by force" in e for e in rec.events) and pve.status == "stopped"
    quiet = DPve()
    driver_core.stop_mac(env_for(quiet, rec, tmp_path), 9)
    assert quiet.calls == [("status", "9")]

    class Stuck(DPve):
        def qm(self, *a):
            from osx_proxmox_next.infrastructure import CommandResult
            return CommandResult(True, 0, "status: running") if a[0] == "status" else CommandResult(True, 0, "")
    with pytest.raises(DriverError, match="would not stop"):
        driver_core.stop_mac(env_for(Stuck(status="running"), rec, tmp_path), 9)


# ── Intel ────────────────────────────────────────────────────────────

def test_an_intel_update_swaps_the_kext_stages_the_package_and_records_both(tmp_path, stubs, rec):
    cfg = mac(machine="pc", hostpci0="0000:00:02.0,legacy-igd=1,romfile=x.rom", vga="none", name="macos-sequoia")
    pve = DPve(cfg=cfg, snapshots=[OLD_SNAP])
    plan = plan_for("intel", pve, installed={"WhateverXe": "1.2.0", "WhateverXe Graphics": "0.9.1"})
    plan.target = intel_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert res.ok and "macOS uses the iGPU" in res.detail
    assert stubs.staged[0][2] == tuple(igpu_engine.vm_settings(0, "")["args_add"])    # leftover -set args are stripped for staging
    assert stubs.applied == [["Replace WhateverXe and AMFIPass on the OpenCore disk"],
                             ["Note in OpenCore that the graphics package is installed"]]
    assert ("set", "9", "--hostpci0", cfg["hostpci0"]) in pve.calls and ("set", "9", "--delete", "hostpci0") in pve.calls
    record = engine.read_record(9)
    assert {c["name"]: c["version"] for c in record["components"]} == {
        "WhateverXe": "1.3.0", "WhateverXe Graphics": "0.9.2", "AMFIPass": igpu_engine.AMFIPASS_VERSION}
    assert {c["name"]: c["version"] for c in record["previous"]} == {"WhateverXe": "1.2.0", "WhateverXe Graphics": "0.9.1"}
    assert pve.snapshots == [NEW_SNAP]


def test_intel_failures_put_the_card_back(tmp_path, stubs, rec):
    cfg = mac(machine="pc", hostpci0="0000:00:02.0,legacy-igd=1")
    plan_args = dict(installed={"WhateverXe": "1.2.0"})
    stubs.attach_ok = False
    pve = DPve(cfg=cfg)
    plan = plan_for("intel", pve, **plan_args)
    plan.target = intel_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert not res.ok and "Replacing WhateverXe on the OpenCore disk failed" in res.error
    assert ("set", "9", "--hostpci0", cfg["hostpci0"]) in pve.calls
    stubs.attach_ok = True
    stubs.package = "failed: WhateverXe is not loaded"
    res = driver_run.run_update(_intel_plan(cfg), env_for(DPve(cfg=cfg), rec, tmp_path))
    assert not res.ok and "graphics package is not installed: failed: WhateverXe is not loaded" in res.error
    stubs.package = ""
    assert "never ran" in driver_run.run_update(_intel_plan(cfg), env_for(DPve(cfg=cfg), rec, tmp_path)).error
    stubs.stage_error = "The staging command did not start after 3 tries."
    assert "did not start" in driver_run.run_update(_intel_plan(cfg), env_for(DPve(cfg=cfg), rec, tmp_path)).error


def _intel_plan(cfg):
    plan = plan_for("intel", DPve(cfg=cfg), installed={"WhateverXe": "1.2.0"})
    plan.target = intel_target()
    return plan


def test_intel_notes_and_a_guest_that_does_not_power_off(tmp_path, stubs, rec):
    cfg = mac(machine="pc", hostpci0="0000:00:02.0,legacy-igd=1", name="macos-tahoe")
    stubs.wait_first = False
    stubs.attach_ok = True
    pve = DPve(cfg=cfg)
    plan = _intel_plan(cfg)
    plan.macos = "tahoe"
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path, accel=lambda **k: (False, "no GPU work"), console=Console(restore_error="e")))
    assert res.ok and ("stop", "9") in pve.calls and stubs.staged[0][3] is True
    assert any("did not see macOS use the iGPU" in n for n in res.notes) and any("auto-boot (e)" in n for n in res.notes)
    assert "not used yet" in res.detail


def test_intel_download_errors_are_clean(tmp_path, stubs, rec, monkeypatch):
    def boom(*a, **k):
        raise igpu_engine.ReleaseError("WhateverXe has no release tagged v9.")
    monkeypatch.setattr(driver_intel, "ensure_igpu_assets", boom)
    cfg = mac(machine="pc")
    res = driver_run.run_update(_intel_plan(cfg), env_for(DPve(cfg=cfg), rec, tmp_path))
    assert not res.ok and "no release tagged v9" in res.error


def test_intel_payload_and_recovery_download(tmp_path, stubs, rec, monkeypatch):
    got = []
    import osx_proxmox_next.downloader as dl
    monkeypatch.setattr(driver_intel, "resolve_recovery_or_installer_path", lambda cfg: tmp_path / "missing.img")
    monkeypatch.setattr(dl, "download_recovery", lambda version, iso: got.append(version) or tmp_path / "rec.img")
    cfg = mac(machine="pc")
    assert driver_run.run_update(_intel_plan(cfg), env_for(DPve(cfg=cfg), rec, tmp_path)).ok and got == ["sequoia"]
    stubs.payload_ok = False
    assert "disk full" in driver_run.run_update(_intel_plan(cfg), env_for(DPve(cfg=cfg), rec, tmp_path)).error


# ── AMD ──────────────────────────────────────────────────────────────

@pytest.fixture
def amd_zips(monkeypatch, tmp_path):
    monkeypatch.setattr(driver_amd, "ensure_component", lambda name, iso: tmp_path / f"{name}.zip")


def test_an_amd_update_replaces_the_two_kexts_with_no_recovery_and_no_card_removal(tmp_path, amd_zips, rec):
    pve = DPve(status="running", bash_output="VERSION 1.7.2\nVERSION 1.7.0\n", snapshots=[OLD_SNAP])
    plan = plan_for("amd", pve, installed={"Lilu": "1.6.8", "WhateverGreen": "1.6.0"})
    plan.target = amd_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert res.ok and not [c for c in pve.calls if "hostpci0" in c] and not pve.calls_of("disk")
    assert [s for s in rec.steps if s[1] == "done"] == [(i, "done") for i in ("stop", "snapshot", "replace", "start")]
    script = [r for r in pve.runs if r[0] == "bash"][0][2]
    assert "replace Lilu.kext" in script and "replace WhateverGreen.kext" in script and script.count("python3 -I -c") == 2 and "mount -o ro" not in script
    assert script.startswith("{ qm status 9 | grep -q 'status: stopped'")           # the script itself refuses a running Mac
    assert str(tmp_path / "Lilu.zip") in script and amd_target().components["Lilu"]["sha256"] in script
    assert pve.calls_of("start") == [("start", "9")]
    record = engine.read_record(9)
    assert {c["name"]: c["version"] for c in record["components"]} == {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}
    assert record["previous"][0]["version"] in ("1.6.8", "1.6.0")


def test_an_amd_pin_that_the_disk_does_not_report_is_noted(tmp_path, amd_zips, rec):
    pve = DPve(bash_output="VERSION 1.7.1\nVERSION 1.7.0\nNOTE: WhateverGreen.kext is not listed in config.plist, so it was not added\n")
    plan = plan_for("amd", pve)
    plan.target = amd_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert res.ok and any("Lilu reports version 1.7.1, the pin is 1.7.2" in n for n in res.notes)
    assert any("WhateverGreen.kext is not listed in config.plist" in n for n in res.notes)   # OpenCore will not load it: said


def test_amd_failures(tmp_path, rec, monkeypatch):
    from osx_proxmox_next.downloader import DownloadError

    def boom(name, iso):
        raise DownloadError("Lilu checksum mismatch")
    monkeypatch.setattr(driver_amd, "ensure_component", boom)
    pve = DPve(status="running")
    plan = plan_for("amd", pve)
    plan.target = amd_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert not res.ok and "checksum mismatch" in res.error and pve.status == "running"
    monkeypatch.setattr(driver_amd, "ensure_component", lambda name, iso: tmp_path / "x.zip")
    bad = DPve(bash_ok=False, bash_output="ERROR: Lilu zip checksum mismatch")
    plan2 = plan_for("amd", bad)
    plan2.target = amd_target()
    res = driver_run.run_update(plan2, env_for(bad, rec, tmp_path))
    assert not res.ok and "Replacing the kexts failed" in res.error and engine.read_record(9) == {}


# ── refusals, the lock and an interrupted run ────────────────────────

def test_run_update_refuses_what_the_plan_refused_and_up_to_date(tmp_path, rec):
    pve = DPve()
    plan = DriverPlan(9, refusals=["no card"])
    with pytest.raises(DriverError, match="no card"):
        driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    current = plan_for("amd", pve, installed={"Lilu": "1.7.2", "WhateverGreen": "1.7.0"})
    current.target, current.verdict = amd_target(), "current"
    with pytest.raises(DriverError, match="Up to date"):
        driver_run.run_update(current, env_for(pve, rec, tmp_path))
    other = foreign_lock(engine.lock_path(9))
    held = plan_for("amd", pve)
    held.target = amd_target()
    with pytest.raises(DriverError, match="Another driver update"):
        driver_run.run_update(held, env_for(pve, rec, tmp_path))
    other.close()
    assert not pve.calls


def test_an_out_of_range_vm_id_is_refused(tmp_path, rec):
    plan = DriverPlan(0, kind="amd")
    plan.target = amd_target()
    with pytest.raises(DriverError, match="out of range"):
        Updater(plan, env_for(DPve(), rec, tmp_path))


def test_an_interrupted_update_is_finished_from_where_the_card_came_off(tmp_path, stubs, rec, monkeypatch):
    monkeypatch.setattr(driver_plan, "resolve", lambda kind, choice, get=None: nv_target())     # no network in tests
    # the first run was cut off after the card came off: the state file remembers how the Mac was
    original = {"hostpci0": "0000:01:00,pcie=0", "vga": "none", "args": mac()["args"], "boot": "order=ide0;virtio0"}
    steps = [{"id": i, "title": t, "done": d, "state": st} for (i, t), (_i, d), st in zip(
        engine.step_titles("nvidia", "NullMoth v2.9"), engine.step_titles("nvidia", "NullMoth v2.9", done=True),
        ["done", "done", "done", "active", "pending", "pending", "pending"])]
    engine.write_update(9, {"kind": "nvidia", "phase": "running", "snapshot": NEW_SNAP, "original": original, "steps": steps,
                            "choice": {"NullMoth": "v2.9"}, "previous": [{"name": "NullMoth", "version": "v2.8.1"}]})
    engine.lock_path(9).parent.mkdir(parents=True, exist_ok=True)
    engine.lock_path(9).write_text("999999999\n")                      # its process is gone: nobody holds the flock
    cut_off = mac(hostpci0=None, vga="std", boot="order=sata1;virtio0;ide0", sata1="local-lvm:vm-9-disk-20,media=disk", sata2="local-lvm:vm-9-disk-21,media=disk")
    pve = DPve(cfg=cut_off, snapshots=[NEW_SNAP], bash_output=['{"AMFIPass.kext": "%s"}' % driver_nvidia.AMFIPASS.version])
    res = driver_run.finish_interrupted(9, env_for(pve, rec, tmp_path, ask_first_boot=True))
    assert res.ok, res.error
    assert pve.calls_of("snapshot") == [] and ("set", "9", "--delete", "hostpci0") not in pve.calls   # snapshot and take-off are not repeated
    assert ("set", "9", "--delete", "sata1") in pve.calls and ("set", "9", "--delete", "sata2") in pve.calls   # leftovers of the cut-off run
    assert ("set", "9", "--hostpci0", "0000:01:00,pcie=0") in pve.calls and ("set", "9", "--vga", "none") in pve.calls
    assert engine.read_record(9)["components"][0]["version"] == "v2.9" and engine.read_update(9)["phase"] == "done"
    assert not [s for s in rec.steps if s[0] in ("stop", "snapshot", "takeoff")]


def test_finishing_needs_something_unfinished(tmp_path, rec):
    with pytest.raises(DriverError, match="no unfinished"):
        driver_run.finish_interrupted(9, env_for(DPve(), rec, tmp_path))


def test_a_failed_run_with_the_card_already_on_does_not_touch_it(tmp_path, stubs, rec, monkeypatch):
    pve = DPve()
    plan = plan_for("nvidia", pve)
    plan.target = nv_target()
    monkeypatch.setattr(driver_nvidia, "_download", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert not res.ok and "boom" in res.error and not pve.calls_of("set")
    assert json.loads(engine.update_path(9).read_text())["phase"] == "failed"


def test_a_card_that_cannot_be_put_back_is_reported(tmp_path, stubs, rec):
    class NoSet(DPve):
        def qm(self, *a):
            from osx_proxmox_next.infrastructure import CommandResult
            if a[0] == "set" and "--hostpci0" in a:
                return CommandResult(False, 1, "no such device")
            return super().qm(*a)
    pve = NoSet(bash_output=['{"AMFIPass.kext": "%s"}' % driver_nvidia.AMFIPASS.version])
    plan = plan_for("nvidia", pve)
    plan.target = nv_target()
    res = driver_run.run_update(plan, env_for(pve, rec, tmp_path))
    assert not res.ok and "Could not put hostpci0 back on VM 9: no such device" in res.error


def test_a_card_that_cannot_be_taken_off_fails_the_update(tmp_path, stubs, rec):
    class Stuck(DPve):
        def qm(self, *a):
            from osx_proxmox_next.infrastructure import CommandResult
            if a[:3] == ("set", "9", "--delete") and a[3] == "hostpci0":
                return CommandResult(False, 1, "locked")
            return super().qm(*a)
    pve = Stuck()
    plan = plan_for("nvidia", pve)
    plan.target = nv_target()
    assert "Could not take the card off VM 9: locked" in driver_run.run_update(plan, env_for(pve, rec, tmp_path)).error


def test_kext_names_are_bundles():
    assert driver_oc.bundle_of("Lilu") == "Lilu.kext" and driver_oc.bundle_of("Lilu.kext") == "Lilu.kext"


def test_a_start_that_fails_fails_the_amd_update(tmp_path, amd_zips, rec):
    class NoStart(DPve):
        def qm(self, *a):
            from osx_proxmox_next.infrastructure import CommandResult
            return CommandResult(False, 1, "no memory") if a[0] == "start" else super().qm(*a)
    pve = NoStart(bash_output="VERSION 1.7.2\nVERSION 1.7.0\n")
    plan = plan_for("amd", pve)
    plan.target = amd_target()
    assert "Could not start VM 9: no memory" in driver_run.run_update(plan, env_for(pve, rec, tmp_path)).error


def test_a_command_line_update_clears_its_state_and_a_tui_update_keeps_it_for_the_first_boot_question(tmp_path, stubs, rec):
    pve = DPve(status="running", snapshots=[OLD_SNAP], bash_output=['{"AMFIPass.kext": "1.4.1"}'])
    plan = plan_for("nvidia", pve, nv_target().components, {"NullMoth": "v2.8.1"})
    plan.target, plan.snapshot_ok = nv_target(), True
    assert driver_run.run_update(plan, env_for(pve, rec, tmp_path)).ok
    assert engine.read_update(9) == {}                            # nothing waits for an answer: no question later
    assert engine.read_record(9)["components"][0]["version"] == "v2.9"
    pve2 = DPve(status="running", snapshots=[OLD_SNAP], bash_output=['{"AMFIPass.kext": "1.4.1"}'])
    plan2 = plan_for("nvidia", pve2, nv_target().components, {"NullMoth": "v2.8.1"})
    plan2.target, plan2.snapshot_ok = nv_target(), True
    assert driver_run.run_update(plan2, env_for(pve2, rec, tmp_path, ask_first_boot=True)).ok
    assert engine.read_update(9)["phase"] == "done"              # the screen that started it asks, and clears it when answered


def test_the_run_uses_the_snapshot_name_of_its_plan(tmp_path, stubs, rec):
    pve = DPve(status="stopped", bash_output=['{"AMFIPass.kext": "1.4.1"}'])
    plan = plan_for("nvidia", pve, nv_target().components)
    plan.target, plan.snapshot_ok, plan.snapshot = nv_target(), True, "osx-next-driver-20200102-030405"
    assert driver_run.run_update(plan, env_for(pve, rec, tmp_path)).snapshot == "osx-next-driver-20200102-030405"
    assert pve.snapshots == ["osx-next-driver-20200102-030405"]
