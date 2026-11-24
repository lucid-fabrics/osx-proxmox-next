"""driver_engine: the record, version rules, plan lines, snapshot rules, lock, unfinished run and the daily check."""
from __future__ import annotations

import io
import json
import os
import time
from contextlib import redirect_stdout

import pytest

from driver_fakes import foreign_lock
from osx_proxmox_next import driver_engine as e


@pytest.fixture
def base(tmp_path):
    return str(tmp_path / "drivers")


def comp(name, version, **kw):
    return e.component(name, version, kw.get("tag", ""), kw.get("sha256", "a" * 64), kw.get("source", "latest"), "2026-11-20T10:00:00Z")


def test_a_record_is_written_and_read_back(base):
    written = e.write_record(101, "nvidia", [comp("NullMoth", "v2.8.1")], base)
    assert written["kind"] == "nvidia" and written["vmid"] == 101
    back = e.read_record(101, base)
    assert back["components"][0] == {"name": "NullMoth", "version": "v2.8.1", "tag": "v2.8.1", "sha256": "a" * 64,
                                     "source": "latest", "installed_at": "2026-11-20T10:00:00Z"}
    assert json.loads(e.record_path(101, base).read_text()) == back
    assert e.installed_versions(back) == {"NullMoth": "v2.8.1"}


def test_an_unknown_kind_is_refused(base):
    with pytest.raises(ValueError):
        e.write_record(101, "matrox", [], base)


def test_no_record_and_a_broken_record_read_as_nothing(base, tmp_path):
    assert e.read_record(7, base, str(tmp_path / "none")) == {}
    e.record_path(7, base).parent.mkdir(parents=True)
    e.record_path(7, base).write_text("{not json")
    assert e.read_record(7, base, str(tmp_path / "none")) == {}
    e.record_path(7, base).write_text("[1]")
    assert e.read_record(7, base, str(tmp_path / "none")) == {}


def test_an_older_igpu_record_migrates_when_there_is_no_driver_record(base, tmp_path):
    igpu = tmp_path / "igpu"
    igpu.mkdir()
    (igpu / "104.json").write_text(json.dumps({
        "whateverxe_version": "1.2.0", "whateverxe_tag": "v1.2.0", "whateverxe_sha256": "b" * 64, "whateverxe_source": "latest",
        "graphics_version": "0.9.1", "amfipass_version": "1.4.1-Mod", "updated": "2026-11-01T00:00:00Z"}))
    rec = e.read_record(104, base, str(igpu))
    assert rec["kind"] == "intel" and rec["migrated"] is True
    names = {c["name"]: c for c in rec["components"]}
    assert names["WhateverXe"]["tag"] == "v1.2.0" and names["WhateverXe"]["sha256"] == "b" * 64
    assert names["WhateverXe Graphics"]["version"] == "0.9.1" and names["WhateverXe Graphics"]["source"] == "pinned"
    assert names["AMFIPass"]["version"] == "1.4.1-Mod"
    e.write_record(104, "intel", [comp("WhateverXe", "9")], base)   # a driver record wins from then on
    assert e.read_record(104, base, str(igpu))["components"][0]["version"] == "9"
    assert e.from_igpu_state(104, {"macos": "sequoia"}) == {}


def test_put_it_back_restores_the_previous_components(base):
    e.write_record(101, "amd", [comp("Lilu", "1.7.2")], base, previous=[comp("Lilu", "1.6.8")], snapshot="osx-next-driver-20261120-1000")
    assert e.read_record(101, base)["snapshot"] == "osx-next-driver-20261120-1000"
    assert e.revert_record(101, base) is True
    back = e.read_record(101, base)
    assert back["components"][0]["version"] == "1.6.8" and "previous" not in back and "snapshot" not in back
    assert e.revert_record(101, base) is False
    e.write_record(102, "nvidia", [comp("NullMoth", "v2")], base, previous=[])
    assert e.revert_record(102, base) and e.read_record(102, base)["components"] == []


@pytest.mark.parametrize("a,b,want", [("v2.10", "v2.9", 1), ("2.9", "v2.9", 0), ("1.7.0", "1.7.2", -1), ("", "1", -1), ("x", "y", 0)])
def test_versions_compare_by_their_numbers(a, b, want):
    assert e.compare(a, b) == want


def test_is_newer_needs_both_versions():
    assert e.is_newer("v2.9", "v2.8.1") and not e.is_newer("v2.8.1", "v2.9")
    assert not e.is_newer("", "v1") and not e.is_newer("v1", "")


def test_judge_unknown_update_and_current():
    target = {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}
    assert e.judge({}, target)["state"] == "unknown"
    assert e.judge({"Lilu": "1.6.8", "WhateverGreen": "1.7.0"}, target)["state"] == "update"
    assert e.judge({"Lilu": "1.7.2"}, target)["state"] == "update"      # one component unknown, so something to do
    assert e.judge({"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}, target)["state"] == "current"
    assert e.judge({"Lilu": "1.8.0", "WhateverGreen": "1.7.0"}, target)["state"] == "current"   # never goes back on its own
    assert e.judge({}, {})["state"] == "current"
    rows = e.judge({"Lilu": "1.6.8"}, target)["rows"]
    assert rows[0] == ("Lilu", "1.6.8", "1.7.2", "update") and rows[1][3] == "unknown"


def test_differs_counts_an_unknown_install_as_work():
    assert e.differs({}, {"NullMoth": "v2.9"}) and e.differs({"NullMoth": "v2.8"}, {"NullMoth": "v2.9"})
    assert not e.differs({"NullMoth": "v2.9"}, {"NullMoth": "v2.9"})


def test_the_driver_line_names_what_is_installed():
    assert e.driver_line("nvidia", {"NullMoth": "v2.8.1"}) == "NullMoth v2.8.1"
    assert e.driver_line("amd", {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}) == "Lilu 1.7.2 and WhateverGreen 1.7.0"
    assert e.driver_line("intel", {}) == ""


def test_change_lines_say_what_changes():
    assert e.change_lines("nvidia", {"NullMoth": "v2.8.1"}, {"NullMoth": "v2.9"}) == ["NullMoth: v2.8.1 to v2.9"]
    assert e.change_lines("nvidia", {}, {"NullMoth": "v2.9"}) == ["NullMoth: installed version unknown, to v2.9"]
    assert e.change_lines("nvidia", {"NullMoth": "v2.9"}, {"NullMoth": "v2.9"}) == ["NullMoth: v2.9, reinstalled"]


def test_a_component_at_the_target_next_to_one_that_is_behind_is_kept_not_reinstalled():
    amd = e.change_lines("amd", {"Lilu": "1.7.2", "WhateverGreen": "1.6.0"}, {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"})
    assert amd == ["Lilu: already 1.7.2, kept", "WhateverGreen: 1.6.0 to 1.7.0"]
    intel = e.change_lines("intel", {"WhateverXe": "0.8.2", "WhateverXe Graphics": "0.4.4"},
                           {"WhateverXe": "0.8.3", "WhateverXe Graphics": "0.4.4"})
    assert intel == ["WhateverXe: 0.8.2 to 0.8.3", "WhateverXe Graphics: already 0.4.4, installed again with the update"]
    kext = e.change_lines("intel", {"WhateverXe": "0.8.3", "WhateverXe Graphics": "0.4.3"}, {"WhateverXe": "0.8.3", "WhateverXe Graphics": "0.4.4"})
    assert kext == ["WhateverXe: already 0.8.3, installed again with the update", "WhateverXe Graphics: 0.4.3 to 0.4.4"]
    assert not any("reinstalled" in line for line in amd + intel)         # the package goes in again, but it is not called a reinstall


def test_the_disk_read_after_an_undo_replaces_the_versions_and_flags_the_record(tmp_path):
    base = str(tmp_path)
    e.write_record(9, "intel", [e.component("WhateverXe", "0.8.3"), e.component("WhateverXe Graphics", "0.4.4")], base,
                   previous=[e.component("WhateverXe", "0.8.0")], snapshot="s")
    assert e.revert_record(9, base) and "disk_read" not in e.read_record(9, base)
    assert e.mark_disk_read(9, {"WhateverXe": "0.8.0", "Missing": ""}, base)
    rec = e.read_record(9, base)
    assert rec["disk_read"] is True and e.installed_versions(rec) == {"WhateverXe": "0.8.0"}
    assert not e.mark_disk_read(9, {}, base) and not e.mark_disk_read(77, {"WhateverXe": "1"}, base)
    e.write_record(9, "intel", [e.component("WhateverXe", "0.8.3")], base)       # a new update writes a new record: the flag is gone
    assert "disk_read" not in e.read_record(9, base)


def test_steps_per_kind_and_tense():
    assert [t for _i, t in e.step_titles("nvidia", "NullMoth v2.9")] == [
        "Stop the Mac", "Save a snapshot", "Take the card off", "Stage NullMoth v2.9", "Put the card back", "Start the Mac",
        "Install the driver"]
    assert [t for _i, t in e.step_titles("nvidia", "NullMoth v2.9", done=True)] == [
        "Stopped the Mac", "Saved a snapshot", "Took the card off", "Staged NullMoth v2.9", "Put the card back", "Started the Mac",
        "Driver installed"]
    assert [i for i, _t in e.step_titles("amd")] == ["stop", "snapshot", "replace", "start"]
    assert e.STEPS["intel"] is e.STEPS["nvidia"]


def test_before_facts_per_kind():
    nv = e.before_facts("nvidia", "osx-next-driver-20261121-1030")
    assert nv[0] == "The Mac stops for about 10 minutes." and "The card comes off and goes back on." in nv
    assert "A snapshot called osx-next-driver-20261121-1030 is saved first." in nv
    amd = e.before_facts("amd", "x")
    assert amd[0] == "The Mac stops for about 1 minute." and "The card comes off and goes back on." not in amd
    none = e.before_facts("amd", "x", supported=False)
    assert e.NO_SNAPSHOT_WARNING in none and "no snapshot" in e.NO_SNAPSHOT_WARNING


def test_snapshot_names_and_exact_argv():
    when = time.struct_time((2026, 11, 21, 10, 30, 0, 5, 325, 0))
    name = e.snapshot_name(when)
    assert name == "osx-next-driver-20261121-103000" and e.SNAPSHOT_RE.match(name)   # seconds: a retry in the same minute is a new name
    assert e.SNAPSHOT_RE.match("osx-next-driver-20261121-1030")                       # names made before still count
    assert e.snapshot_argv(101, name, "NullMoth v2.9") == [
        "qm", "snapshot", "101", name, "--description", "before driver update to NullMoth v2.9"]
    assert e.rollback_argv(101, name) == ["qm", "rollback", "101", name]
    assert e.delete_snapshot_argv(101, name) == ["qm", "delsnapshot", "101", name]
    assert e.snapshot_description("v2.9; rm -rf /") == "before driver update to v2.9 rm -rf "


def test_only_snapshots_of_this_tool_are_named(base):
    for bad in ("current", "osx-next-driver-x", "other; rm", ""):
        with pytest.raises(ValueError):
            e.rollback_argv(1, bad)
    with pytest.raises(ValueError):
        e.snapshot_argv(1, "mine", "v1")
    text = ("`-> before-upgrade   2026-01-01 00:00:00  mine\n `-> osx-next-driver-20261120-0900  2026  before driver update to v2\n"
            " `-> osx-next-driver-20261121-1030  2026  x\n `-> current  You are here!\n x-osx-next-driver-20261121-1030\n")
    assert e.tool_snapshots(text) == ["osx-next-driver-20261120-0900", "osx-next-driver-20261121-1030"]
    assert e.tool_snapshots(None) == []


def test_snapshot_support_by_storage_type():
    cfg = {"virtio0": "local-lvm:vm-1-disk-1,size=64G", "ide0": "local-lvm:vm-1-disk-0,size=40M", "ide2": "local:iso/x.iso,media=cdrom",
           "efidisk0": "local-lvm:vm-1-disk-2", "name": "x"}
    assert e.snapshot_support(cfg, {"local-lvm": "lvmthin"}) == (True, "")
    ok, why = e.snapshot_support(cfg, {"local-lvm": "lvm"})
    assert not ok and "storage local-lvm (type lvm)" in why
    ok, why = e.snapshot_support(cfg, {})
    assert not ok and "its type is unknown" in why
    files = {"virtio0": "nfs1:101/vm-101-disk-0.raw", "scsi0": "none"}
    ok, why = e.snapshot_support(files, {"nfs1": "nfs"})
    assert not ok and "without qcow2" in why
    assert e.snapshot_support({"virtio0": "nfs1:101/vm-101-disk-0.qcow2"}, {"nfs1": "nfs"}) == (True, "")
    assert e.snapshot_support({"virtio0": "z:vm-1-disk-0"}, {"z": "zfspool"}) == (True, "")


def test_pvesm_status_is_parsed():
    text = "Name  Type  Status  Total  Used  Available  %\nlocal  dir  active  1  1  1  1%\nlocal-lvm  lvmthin  active  1\n"
    assert e.parse_storage_types(text) == {"local": "dir", "local-lvm": "lvmthin"}
    assert e.parse_storage_types("") == {}


def test_the_lock_is_an_flock_so_a_reused_pid_cannot_hold_it(base, tmp_path):
    assert e.lock_holder(5, base) == 0
    assert e.acquire_lock(5, base)
    assert e.lock_holder(5, base) == os.getpid() and e.acquire_lock(5, base)       # the holder may take it again
    e.release_lock(5, base)
    e.release_lock(5, base)
    assert e.lock_holder(5, base) == 0 and not e.lock_path(5, base).exists()
    # a lock file that names a live pid but that nobody holds (the host restarted, the pid was reused) is free
    e.lock_path(5, base).write_text("%d\n" % os.getpid())
    assert e.lock_holder(5, base) == 0 and e.acquire_lock(5, base)
    e.release_lock(5, base)
    e.lock_path(5, base).write_text("abc")
    assert e.lock_holder(5, base) == 0


def test_a_lock_held_by_another_open_file_refuses_and_is_reported(base):
    other = foreign_lock(e.lock_path(5, base), pid=4242)
    assert e.lock_holder(5, base) == 4242 and not e.acquire_lock(5, base)
    other.seek(0)
    other.truncate()
    other.write("garbage")
    other.flush()
    assert e.lock_holder(5, base) == 1                                # held, whoever it is
    other.close()
    assert e.lock_holder(5, base) == 0 and e.acquire_lock(5, base)
    e.release_lock(5, base)


def test_two_processes_cannot_both_take_the_lock(base):
    import subprocess
    import sys
    code = ("import sys,time; sys.path.insert(0, %r); from osx_proxmox_next import driver_state as s\n"
            "print(s.acquire_lock(7, %r)); sys.stdout.flush(); time.sleep(30)" % (str(__import__('pathlib').Path(e.__file__).parent.parent), base))
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "True"
        assert e.lock_holder(7, base) == child.pid
        assert not e.acquire_lock(7, base)
    finally:
        child.kill()
        child.wait()
    assert e.acquire_lock(7, base)
    e.release_lock(7, base)


def test_an_unfinished_update_is_seen_only_without_a_runner(base):
    assert e.unfinished(9, base) == {}
    e.write_update(9, {"kind": "amd", "phase": "running", "snapshot": "s"}, base)
    assert e.unfinished(9, base)["kind"] == "amd" and e.read_update(9, base)["vmid"] == 9
    other = foreign_lock(e.lock_path(9, base))
    assert e.unfinished(9, base) == {}                          # somebody is running it
    other.close()
    e.write_update(9, {"kind": "amd", "phase": "done"}, base)
    assert e.unfinished(9, base) == {}                          # finished, waiting for the first boot question
    e.clear_update(9, base)
    e.clear_update(9, base)
    assert e.read_update(9, base) == {}


def test_a_run_goes_on_from_the_first_step_that_is_not_done(base):
    state = e.new_update("nvidia", "m", "v2.9", "NullMoth v2.9", {}, [], "0000:01:00")
    assert state["resume_at"] == 0 and all(r["state"] == "pending" for r in state["steps"]) and state["card"] == "0000:01:00"
    for sid in ("stop", "snapshot", "takeoff"):
        assert e.begin_step(state, sid)
        e.end_step(state, sid)
    assert e.begin_step(state, "stage")
    e.fail_update(state, "boom")
    assert state["phase"] == "failed" and [r["state"] for r in state["steps"]][2:5] == ["done", "fail", "pending"]
    assert e.resume_index(state) == 3
    state["resume_at"] = e.resume_index(state)
    assert not e.begin_step(state, "takeoff") and e.begin_step(state, "stage")       # done before the point: skipped
    assert e.resume_index({"steps": [{"state": "done"}]}) == 1 and e.resume_index({}) == 0


def test_what_a_failed_run_left_is_told_from_its_steps(base):
    def state(kind, **rows):
        steps = [{"id": i, "state": rows.get(i, "pending")} for i, _t in e.step_titles(kind)]
        return {"kind": kind, "steps": steps}
    assert e.failure_text(state("nvidia", stop="done"), False) == "Nothing on the Mac's disks was changed. The Mac is stopped."
    assert e.failure_text(state("nvidia", stage="fail"), True) == "The driver was only partly staged. The Mac is running."
    assert "staged inside macOS" in e.failure_text(state("nvidia", stage="done", putback="fail"), False)
    assert "replaced" in e.failure_text(state("amd", replace="done", start="fail"), False)
    assert "half changed" in e.failure_text(state("amd", replace="fail"), False)


def test_a_snapshot_is_undone_only_while_the_card_setup_is_the_one_it_was_taken_with():
    assert not e.card_changed("", "anything") and not e.card_changed("0000:01:00", "0000:01:00")
    assert e.card_changed("0000:01:00", "") and e.card_changed("0000:01:00", "0000:02:00")
    assert not e.card_changed("0000:01:00", "", interrupted=True)        # the cut off update took the card off itself
    assert e.card_changed("0000:01:00", "0000:02:00", interrupted=True)


def test_the_kind_comes_from_the_graphics_card_on_the_host(tmp_path):
    def dev(bdf, vendor, pci_class):
        folder = tmp_path / "bus/pci/devices" / bdf
        folder.mkdir(parents=True)
        (folder / "vendor").write_text(vendor + "\n")
        (folder / "class").write_text(pci_class + "\n")
    dev("0000:01:00.0", "0x10de", "0x030000")
    dev("0000:00:14.0", "0x8086", "0x0c0330")           # an Intel USB controller is no card
    root = str(tmp_path)
    assert e.live_kind({"hostpci0": "0000:01:00,pcie=0"}, root) == ("nvidia", True)
    assert e.live_kind({"hostpci0": "0000:00:14.0"}, root) == ("", True)
    assert e.live_kind({"hostpci0": "0000:00:14.0", "hostpci1": "host=0000:01:00.0"}, root) == ("nvidia", True)
    assert e.live_kind({"hostpci0": "0000:09:00"}, root) == ("", False)   # not on this host: cannot tell
    assert e.live_kind({}, root) == ("", False)
    assert e.resolve_kind("nvidia", "nvidia", True) == ("nvidia", False)
    assert e.resolve_kind("amd", "nvidia", True) == ("nvidia", True)     # the record is for another card
    assert e.resolve_kind("", "intel", True) == ("intel", False)
    assert e.resolve_kind("amd", "", False) == ("amd", False)            # nothing to look at: the record decides
    assert e.resolve_kind("amd", "", True) == ("", False)                # a device that is no card


def test_only_what_is_behind_is_replaced_and_a_newer_kext_is_kept():
    target = {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}
    assert e.replace_names({"Lilu": "1.7.3", "WhateverGreen": "1.6.9"}, target) == ["WhateverGreen"]
    assert e.replace_names({"Lilu": "1.7.3", "WhateverGreen": "1.7.0"}, target) == []
    assert e.replace_names({"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}, target, reinstall=True) == ["Lilu", "WhateverGreen"]
    assert e.replace_names({}, target) == ["Lilu", "WhateverGreen"]
    assert e.change_lines("amd", {"Lilu": "1.7.3"}, {"Lilu": "1.7.2"}) == ["Lilu: 1.7.3 is newer than 1.7.2, kept"]


def test_the_retention_rule_and_snapshot_helpers():
    assert e.retention_line([]) == ""
    assert e.retention_line(["a"]) == "The previous driver snapshot a is removed once the new one is saved."
    assert e.retention_line(["a", "b"]) == "The previous driver snapshots a, b are removed once the new one is saved."
    assert e.before_facts("amd", "s", True, ["osx-next-driver-20261101-0900"])[-1].startswith("The previous driver snapshot ")
    assert e.snapshot_date("osx-next-driver-20261121-103005") == "2026-11-21 10:30" and e.snapshot_date("x") == ""
    listing = "`-> osx-next-driver-20261120-0900  2026  x\n `-> mine  2026  y\n  `-> current  You are here!\n"
    assert e.all_snapshots(listing) == ["osx-next-driver-20261120-0900", "mine"]
    assert e.newer_than(listing, "osx-next-driver-20261120-0900") == ["mine"] and e.newer_than(listing, "mine") == []
    assert e.newer_than(listing, "gone") == []


def test_the_daily_check_cache(base):
    assert e.check_due(1000.0, base) and e.read_check(base) == {}
    e.write_check({"NullMoth": "v2.9"}, 1000.0, base)
    assert e.read_check(base)["latest"] == {"NullMoth": "v2.9"}
    assert not e.check_due(1000.0 + 3600, base)
    assert e.check_due(1000.0 + e.CHECK_MAX_AGE, base)
    e.check_path(base).write_text('{"latest": 3}')
    assert e.read_check(base) == {}


def run_main(*argv, stdin=""):
    out = io.StringIO()
    import sys
    old = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with redirect_stdout(out):
            code = e.main(["driver_engine.py", *argv])
    finally:
        sys.stdin = old
    return code, out.getvalue()


def test_the_command_line_the_bash_installer_uses(base, monkeypatch, tmp_path):
    monkeypatch.setenv("DRIVER_STATE_DIR", base)
    comps = json.dumps([comp("NullMoth", "v2.9")])
    assert run_main("write", "101", "nvidia", comps)[0] == 0
    code, out = run_main("record", "101")
    assert code == 0 and json.loads(out)["components"][0]["name"] == "NullMoth"
    assert run_main("write", "101", "nvidia", comps, "[]")[0] == 0
    assert run_main("write", "101", "nvidia", comps, "[]", "osx-next-driver-20261121-1030", "0000:01:00")[0] == 0
    assert e.read_record(101, base)["snapshot_card"] == "0000:01:00"
    assert run_main("revert", "101")[0] == 0 and run_main("revert", "102")[0] == 1
    assert json.loads(run_main("component", "Lilu", "1.7.2")[1])["tag"] == "1.7.2"
    assert json.loads(run_main("component", "Lilu", "1.7.2", "t", "s", "src")[1])["sha256"] == "s"
    assert json.loads(run_main("judge", '{"Lilu": "1"}', '{"Lilu": "2"}')[1])["state"] == "update"
    assert run_main("changes", "amd", '{"Lilu": "1"}', '{"Lilu": "2"}')[1] == "Lilu: 1 to 2\n"
    assert "A snapshot called s is saved first." in run_main("facts", "amd", "s")[1]
    assert e.NO_SNAPSHOT_WARNING in run_main("facts", "amd", "s", "unsupported")[1]
    assert run_main("steps", "amd", "x")[1].splitlines()[2] == "replace Replace the kexts"
    assert run_main("snapshot-name")[1].startswith("osx-next-driver-")
    assert run_main("snapshot-argv", "1", "osx-next-driver-20261121-1030", "v1")[1].splitlines()[:2] == ["qm", "snapshot"]
    assert run_main("tool-snapshots", stdin="`-> osx-next-driver-20261121-1030 x")[1] == "osx-next-driver-20261121-1030\n"
    status = tmp_path / "status"
    status.write_text("Name Type\nlocal-lvm lvmthin\nnfs nfs\n")
    assert run_main("snapshot-support", str(status), stdin="virtio0: local-lvm:vm-1-disk-0\n")[0] == 0
    code, out = run_main("snapshot-support", str(status), stdin="virtio0: nfs:1/vm-1-disk-0.raw\n")
    assert code == 1 and "nfs" in out
    assert run_main("compare", "v2.10", "v2.9")[1] == "1\n"
    assert run_main("text", "UP_TO_DATE")[1] == e.UP_TO_DATE + "\n"
    assert run_main("update-init", "101", "amd", "Mac", "v1", "the driver", "{}", "[]", "0000:01:00")[0] == 0
    assert run_main("pending-kind", "101")[1] == "amd\n" and json.loads(run_main("update-get", "101", "card")[1]) == "0000:01:00"
    assert run_main("update-begin", "101", "stop")[0] == 0 and run_main("update-end", "101", "stop")[0] == 0
    assert run_main("update-set", "101", "snapshot", '"s"')[0] == 0 and json.loads(run_main("update-get", "101", "snapshot")[1]) == "s"
    assert run_main("update-set", "101", "original", '{"hostpci0": "0000:01:00"}')[0] == 0
    assert run_main("update-fail", "101", "boom")[0] == 0 and run_main("update-resume", "101")[0] == 0
    assert run_main("update-begin", "101", "stop")[0] == 3                  # done before the point it goes on from
    assert "Nothing on the Mac's disks was changed. The Mac is stopped." in run_main("failure-text", "101", "no")[1]
    assert json.loads(run_main("unfinished", "101")[1])["kind"] == "amd"
    assert run_main("card-changed", "0000:01:00", "0000:02:00", "no")[0] == 0 and run_main("card-changed", "", "x", "no")[0] == 1
    assert run_main("snapshot-date", "osx-next-driver-20261121-1030")[1] == "2026-11-21 10:30\n"
    assert run_main("replace-names", '{"Lilu": "1.7.3"}', '{"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}', "no")[1] == "WhateverGreen\n"
    assert run_main("newer-than", "a", stdin="`-> a x\n `-> b y\n")[1] == "b\n"
    assert run_main("all-snapshots", stdin="`-> a x\n `-> current  here\n")[1] == "a\n"
    kind = json.loads(run_main("kind", "amd", stdin="hostpci0: 0000:09:00\n")[1])
    assert kind == {"kind": "amd", "conflict": False, "readable": False}
    assert run_main("update-clear", "101")[0] == 0 and run_main("pending-kind", "101")[1] == "\n"
    igpu = tmp_path / "igpu"
    igpu.mkdir()
    monkeypatch.setenv("IGPU_STATE_DIR", str(igpu))
    assert run_main("from-igpu", "104")[0] == 1
    (igpu / "104.json").write_text('{"whateverxe_version": "1.2"}')
    assert run_main("from-igpu", "104")[0] == 0 and e.read_record(104, base)["kind"] == "intel"
    assert run_main("bogus")[0] == 64


# ── fix wave 2 ───────────────────────────────────────────────────────

def test_a_plan_lists_the_stop_only_when_the_mac_runs():
    assert [i for i, _t in e.plan_steps("nvidia", "NullMoth v3", True)][0] == "stop"
    stopped = e.plan_steps("nvidia", "NullMoth v3", False)
    assert [i for i, _t in stopped] == ["snapshot", "takeoff", "stage", "putback", "start", "verify"]
    assert [i for i, _t in e.plan_steps("amd", "the driver", False)] == ["snapshot", "replace", "start"]


def test_an_undo_has_steps_like_an_update():
    assert [i for i, _t in e.undo_titles()] == ["stop", "release", "rollback", "start"]
    assert [t for _i, t in e.undo_titles(done=True)][0] == "Stopped the Mac"


def test_the_fingerprint_tells_a_rolled_back_mac_from_the_one_the_record_was_made_for():
    assert e.volume_fingerprint([]) == "" and e.volume_fingerprint(["", ""]) == ""
    fp = e.volume_fingerprint(["b", "a"])
    assert fp == e.volume_fingerprint(["a", "b"]) and fp != e.volume_fingerprint(["a", "c"])
    assert e.record_trusted({}, fp) and e.record_trusted({"fingerprint": fp}, fp) and e.record_trusted({"fingerprint": fp}, "")
    assert not e.record_trusted({"fingerprint": fp}, "other")


def test_a_record_carries_the_fingerprint_and_what_was_known_as_newest(base):
    e.write_record(9, "nvidia", [comp("NullMoth", "v3")], base, fingerprint="abc", latest={"NullMoth": "v3"})
    data = e.read_record(9, base)
    assert data["fingerprint"] == "abc" and data["latest"] == {"NullMoth": "v3"}
    assert e.stamp_record(9, "def", base) and e.read_record(9, base)["fingerprint"] == "def"
    assert e.stamp_record(9, "", base) and "fingerprint" not in e.read_record(9, base)
    assert not e.stamp_record(10, "x", base)
    e.write_record(9, "nvidia", [comp("NullMoth", "v3")], base, previous=[comp("NullMoth", "v2")], latest={"NullMoth": "v3"})
    assert e.revert_record(9, base) and e.read_record(9, base)["latest"] == {"NullMoth": "v3"}      # an undo keeps what it knew


def test_device_map_names_come_from_the_logical_volume():
    assert e.dm_name("/dev/pve/vm-980-disk-2") == "pve-vm--980--disk--2"
    assert e.dm_name("/dev/mapper/pve-vm--980--disk--2") == "pve-vm--980--disk--2"
    assert e.dm_name("/dev/zvol/rpool/vm-1-disk-0") == "" and e.dm_name("/var/lib/vz/images/9/x.qcow2") == ""
    listing = "pve-vm--980--disk--2p1\t(253:12)\npve-vm--980--disk--2\t(253:4)\npve-vm--980--disk--20p1\t(253:7)\nother-p1\t(253:9)\n"
    assert e.stale_maps(listing, ["/dev/pve/vm-980-disk-2"]) == ["pve-vm--980--disk--2p1"]     # not disk 20's, not the LV itself
    assert e.stale_maps("No devices found", ["/dev/pve/vm-980-disk-2"]) == [] and e.stale_maps("", []) == []
    text = e.maps_held_text(980, ["a", "b"])
    assert "VM 980" in text and "dmsetup remove a; dmsetup remove b" in text and "Nothing was changed" in text


def test_the_command_line_runs_the_new_helpers(capsys, base):
    def call(*argv, stdin=""):
        import io as _io
        import sys as _sys
        old = _sys.stdin
        _sys.stdin = _io.StringIO(stdin)
        try:
            code = e.main(["driver_engine.py", *argv])
        finally:
            _sys.stdin = old
        return code, capsys.readouterr().out.strip()
    assert call("fingerprint", stdin="b a\n") == (0, e.volume_fingerprint(["a", "b"]))
    assert call("stale-maps", "/dev/pve/vm-9-disk-1", stdin="pve-vm--9--disk--1p1\t(1:1)\n") == (0, "pve-vm--9--disk--1p1")
    assert call("maps-held", "9", "x")[1].endswith("(then try again).")
    assert call("steps", "nvidia", "NullMoth v3", "stopped")[1].splitlines()[0] == "snapshot Save a snapshot"
    assert call("steps", "nvidia", "NullMoth v3")[1].splitlines()[0] == "stop Stop the Mac"
    code, _out = call("write", "9", "nvidia", json.dumps([comp("NullMoth", "v3")]), "[]", "snap", "0000:01:00", json.dumps({"NullMoth": "v3"}), "fp1")
    assert code == 0 and e.read_record(9)["fingerprint"] == "fp1" and e.read_record(9)["latest"] == {"NullMoth": "v3"}
    assert call("trusted", "9", "fp1")[0] == 0 and call("trusted", "9", "other")[0] == 1
    assert call("stamp", "9", "fp2")[0] == 0 and e.read_record(9)["fingerprint"] == "fp2"
