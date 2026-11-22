"""driver_plan.prepare: what a Mac has, what an update would install, and why it is refused."""
from __future__ import annotations

import pytest

from driver_fakes import DPve, comps, mac
from osx_proxmox_next import driver_engine as engine, driver_plan, driver_targets, host_setup, igpu_engine, nullmoth_engine
from osx_proxmox_next.driver_targets import Choice, Target


@pytest.fixture
def target(monkeypatch):
    """The releases an update would install, without GitHub."""
    state = {"nvidia": "v2.9", "intel": ("1.3.0", "0.9.2")}

    def resolve(kind, choice, get=None):
        if kind == "nvidia":
            tag = choice.nullmoth or state["nvidia"]
            return Target(kind, {"NullMoth": {"version": tag, "tag": tag, "sha256": "d" * 64, "source": "chosen" if choice.nullmoth else "latest"}})
        if kind == "intel":
            return Target(kind, {"WhateverXe": {"version": state["intel"][0], "tag": "v" + state["intel"][0], "sha256": "e" * 64, "source": "latest"},
                                 "WhateverXe Graphics": {"version": state["intel"][1], "tag": "v" + state["intel"][1], "sha256": "f" * 64, "source": "latest"}})
        return driver_targets.resolve(kind, choice)
    monkeypatch.setattr(driver_plan, "resolve", resolve)
    return state


def attach_record(vendor):
    import json
    from osx_proxmox_next import gpu
    gpu.record_path(9).parent.mkdir(parents=True, exist_ok=True)
    gpu.record_path(9).write_text(json.dumps({"vmid": 9, "bdf": "0000:01:00", "vendor": vendor}))


def write(kind, **versions):
    engine.write_record(9, kind, comps(**versions))


def test_an_nvidia_mac_with_an_older_driver_gets_an_update_plan(target):
    write("nvidia", NullMoth="v2.8.1")
    plan = driver_plan.prepare(9, pve=DPve())
    assert plan.ok and plan.kind == "nvidia" and plan.verdict == "update" and plan.installed_known
    assert plan.target.versions() == {"NullMoth": "v2.9"} and plan.macos == "sequoia" and plan.name == "macos-sequoia"
    assert plan.snapshot_ok and plan.snapshot.startswith("osx-next-driver-") and plan.version_label == "v2.9"
    lines = plan.lines()
    assert lines[0] == "Update the NVIDIA driver of macos-sequoia (VM 9)"
    assert lines[1] == "  NullMoth: v2.8.1 to v2.9"
    assert "  4. Stage NullMoth v2.9" in lines and "The Mac stops for about 10 minutes." in lines
    assert plan.step_name() == "NullMoth v2.9"


def test_the_plan_for_a_mac_that_is_up_to_date_has_nothing_to_do(target):
    write("nvidia", NullMoth="v2.9")
    plan = driver_plan.prepare(9, pve=DPve())
    assert plan.nothing_to_do and plan.lines()[-1] == engine.UP_TO_DATE
    again = driver_plan.prepare(9, pve=DPve(), reinstall=True)
    assert not again.nothing_to_do and "Steps:" in again.lines() and again.lines()[1] == "  NullMoth: v2.9, reinstalled"


def test_a_picked_release_is_the_target(target):
    write("nvidia", NullMoth="v2.9")
    plan = driver_plan.prepare(9, Choice(nullmoth="v2.8"), pve=DPve())
    assert plan.target.versions() == {"NullMoth": "v2.8"} and plan.verdict == "current"   # older than installed: the tool does not go back by itself
    assert not plan.nothing_to_do or plan.verdict == "current"


def test_an_nvidia_mac_without_a_record_has_an_unknown_version(target):
    attach_record("nvidia")
    plan = driver_plan.prepare(9, pve=DPve())
    assert plan.ok and not plan.installed_known and plan.verdict == "unknown" and not plan.nothing_to_do
    assert plan.lines()[1] == "  NullMoth: installed version unknown, to v2.9"


def test_an_amd_mac_without_a_record_is_read_from_its_opencore_disk(target):
    pve = DPve(bash_output='{"Lilu.kext": "1.6.8", "WhateverGreen.kext": "1.7.0"}')
    attach_record("amd")
    plan = driver_plan.prepare(9, pve=pve)
    assert plan.kind == "amd" and plan.installed == {"Lilu": "1.6.8", "WhateverGreen": "1.7.0"} and plan.installed_known
    assert plan.verdict == "update" and plan.lines()[1] == "  Lilu: 1.6.8 to 1.7.2"
    assert plan.step_name() == "the driver" and plan.target.versions() == {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}
    script = [r for r in pve.runs if r[0] == "bash"][0][2]
    assert "versions Lilu.kext WhateverGreen.kext" in script and "mount" in script


def test_an_amd_mac_with_the_pinned_versions_is_up_to_date(target):
    write("amd", Lilu="1.7.2", WhateverGreen="1.7.0")
    plan = driver_plan.prepare(9, pve=DPve())
    assert plan.nothing_to_do and plan.verdict == "current"


def test_a_running_amd_mac_is_not_read_and_a_failed_read_is_unknown(target):
    write("amd", Lilu="1.7.2")
    engine.record_path(9).unlink()
    attach_record("amd")
    plan = driver_plan.prepare(9, pve=DPve(status="running"))
    assert plan.running and not plan.installed_known and not [1 for r in plan.cfg if r == "x"]
    failed = driver_plan.prepare(9, pve=DPve(bash_ok=False, bash_output="mount failed"))
    assert not failed.installed_known and failed.verdict == "unknown"
    empty = driver_plan.prepare(9, pve=DPve(bash_output='{"Lilu.kext": "", "WhateverGreen.kext": ""}'))
    assert not empty.installed_known


def test_the_kind_falls_back_to_the_vendor_of_the_device(tmp_path):
    for bdf, vendor, kind in (("0000:01:00.0", "0x10de", "nvidia"), ("0000:06:00.0", "0x1002", "amd"), ("0000:00:02.0", "0x8086", "intel")):
        dev = tmp_path / "bus/pci/devices" / bdf
        dev.mkdir(parents=True)
        (dev / "vendor").write_text(vendor + "\n")
        assert driver_targets.detect_kind(9, {"hostpci0": bdf[:-2] + ",pcie=0"}, tmp_path) == kind
    assert driver_targets.detect_kind(9, {"hostpci1": "0000:01:00,pcie=0"}, tmp_path) == "nvidia"
    assert driver_targets.detect_kind(9, {"hostpci0": "0000:09:00"}, tmp_path) == ""
    (tmp_path / "bus/pci/devices/0000:01:00.0/vendor").write_text("0x1234\n")
    assert driver_targets.detect_kind(9, {"hostpci0": "host=0000:01:00"}, tmp_path) == ""
    assert driver_targets.detect_kind(9, {}, tmp_path) == ""


def test_refusals(target, monkeypatch):
    p = driver_plan.prepare(9, pve=DPve(cfg=mac(hostpci0=None)))
    assert p.refusals == [engine.REFUSE_NO_CARD] and not p.ok and p.lines() == ["Refused: " + engine.REFUSE_NO_CARD]
    with monkeypatch.context() as m:
        m.setattr(driver_plan, "detect_kind", lambda vmid, cfg: "")
        p = driver_plan.prepare(9, pve=DPve())     # a card but nothing says which kind
        assert p.refusals == [engine.REFUSE_UNKNOWN_KIND]
    write("nvidia", NullMoth="v1")
    assert engine.REFUSE_NOT_MAC % 9 in driver_plan.prepare(9, pve=DPve(cfg=mac(args="-smbios x"))).refusals
    assert engine.REFUSE_INSTALLING in driver_plan.prepare(9, pve=DPve(cfg=mac(ide2="local:iso/rec.img,media=disk"))).refusals
    assert engine.REFUSE_INSTALLING in driver_plan.prepare(9, pve=DPve(cfg=mac(sata1="local:iso/rec.img"))).refusals
    monkeypatch.setattr(host_setup, "load_state", lambda root="/": {"vm": 9, "gpu": {"bdf": "x"}, "finished": False})
    assert engine.REFUSE_CARD_SETUP in driver_plan.prepare(9, pve=DPve()).refusals
    monkeypatch.setattr(host_setup, "load_state", lambda root="/": {"vm": 9, "gpu": {"bdf": "x"}, "finished": True})
    assert engine.REFUSE_CARD_SETUP not in driver_plan.prepare(9, pve=DPve()).refusals
    monkeypatch.setattr(host_setup, "load_state", lambda root="/": {"vm": 4, "gpu": {"bdf": "x"}})
    assert not driver_plan.card_setup_waiting(9)
    monkeypatch.setattr(host_setup, "load_state", lambda root="/": (_ for _ in ()).throw(OSError("x")))
    assert not driver_plan.card_setup_waiting(9)
    import os
    engine.lock_path(9).parent.mkdir(parents=True, exist_ok=True)
    engine.lock_path(9).write_text("%d\n" % os.getpid())
    assert engine.REFUSE_LOCKED in driver_plan.prepare(9, pve=DPve()).refusals
    engine.release_lock(9)


def test_a_missing_vm_is_refused():
    class Gone(DPve):
        def run(self, argv):
            from osx_proxmox_next.infrastructure import CommandResult
            return CommandResult(False, 2, "no such VM") if argv[:2] == ["qm", "status"] else super().run(argv)
    assert driver_plan.prepare(404, pve=Gone()).refusals == ["VM 404 not found."]


def test_macos_that_cannot_be_told_and_unsupported_intel_are_refused(target):
    write("nvidia", NullMoth="v1")
    plan = driver_plan.prepare(9, pve=DPve(cfg=mac(name="vm9")))
    assert any("Cannot tell which macOS" in r for r in plan.refusals)
    assert driver_plan.prepare(9, Choice(macos="sequoia"), pve=DPve(cfg=mac(name="vm9"))).ok
    write("intel", WhateverXe="1.2.0")
    assert igpu_engine and any("Monterey" in r for r in driver_plan.prepare(9, pve=DPve(cfg=mac(name="macos-monterey"))).refusals)
    tahoe = driver_plan.prepare(9, pve=DPve(cfg=mac(name="macos-tahoe")))
    assert tahoe.ok and any("Tahoe" in w for w in tahoe.warnings) and any(l.startswith("Warning: ") for l in tahoe.lines())
    assert tahoe.step_name() == "WhateverXe 1.3.0"


def test_a_release_that_cannot_be_used_is_a_refusal(monkeypatch):
    write("nvidia", NullMoth="v1")

    def boom(kind, choice, get=None):
        raise igpu_engine.ReleaseError("GitHub refused the request (rate limit reached).")
    monkeypatch.setattr(driver_plan, "resolve", boom)
    assert driver_plan.prepare(9, pve=DPve()).refusals == ["GitHub refused the request (rate limit reached)."]


def test_storage_that_cannot_snapshot_is_said_and_older_snapshots_are_listed(target):
    write("nvidia", NullMoth="v2.8")
    plan = driver_plan.prepare(9, pve=DPve(storage="Name Type\nlocal-lvm lvm\n", snapshots=["osx-next-driver-20261101-0900"]))
    assert not plan.snapshot_ok and plan.needs_no_snapshot_phrase and "type lvm" in plan.snapshot_reason
    lines = plan.lines()
    assert engine.NO_SNAPSHOT_WARNING in lines
    assert "Older safety snapshots are removed when this one is done: osx-next-driver-20261101-0900" in lines


def test_the_commands_are_written_out(target):
    write("nvidia", NullMoth="v1")
    nv = driver_plan.commands(driver_plan.prepare(9, pve=DPve()))
    assert nv[0] == "qm shutdown 9 --timeout 120" and nv[1].startswith("qm snapshot 9 osx-next-driver-")
    assert "qm set 9 --delete hostpci0" in nv and nv[-1].startswith("qm rollback 9 osx-next-driver-")
    write("amd", Lilu="1")
    amd = driver_plan.commands(driver_plan.prepare(9, pve=DPve()))
    assert "qm set 9 --delete hostpci0" not in amd and any("OpenCore disk" in c for c in amd)


def test_targets_resolve_each_kind_from_the_releases_the_engines_name(monkeypatch):
    pick = {"tag": "v2.9", "digest": "a" * 64, "source": "latest"}
    monkeypatch.setattr(nullmoth_engine, "resolve", lambda choice, get: dict(pick, source="chosen" if choice != "latest" else "latest"))
    t = driver_targets.resolve("nvidia", Choice(nullmoth="v2.8"))
    assert t.components["NullMoth"] == {"version": "v2.9", "tag": "v2.9", "sha256": "a" * 64, "source": "chosen"}
    picks = {"whateverxe": {"version": "1.3", "tag": "v1.3", "sha256": "1", "source": "latest"},
             "graphics": {"version": "0.9", "tag": "v0.9", "sha256": "2", "source": "chosen"}}
    seen = []
    monkeypatch.setattr(igpu_engine, "select_assets", lambda wex, gfx, get: seen.append((wex, gfx)) or picks)
    t = driver_targets.resolve("intel", Choice(wex="v1.3"))
    assert seen == [("v1.3", "latest")] and t.versions() == {"WhateverXe": "1.3", "WhateverXe Graphics": "0.9"}
    amd = driver_targets.resolve("amd", Choice())
    assert amd.components["Lilu"]["source"] == "pinned" and len(amd.components["Lilu"]["sha256"]) == 64


def test_the_newest_releases_are_asked_for_the_daily_check(monkeypatch):
    monkeypatch.setattr(nullmoth_engine, "resolve", lambda choice, get: {"tag": "v3"})
    monkeypatch.setattr(igpu_engine, "resolve", lambda kind, choice, get=None: {"version": "1." + kind[:1]})
    assert driver_targets.latest_known() == {"NullMoth": "v3", "WhateverXe": "1.w", "WhateverXe Graphics": "1.g"}
