"""driver_engine: the record, version rules, plan lines, snapshot rules, lock, unfinished run and the daily check."""
from __future__ import annotations

import io
import json
import os
import time
from contextlib import redirect_stdout

import pytest

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
    assert name == "osx-next-driver-20261121-1030" and e.SNAPSHOT_RE.match(name)
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


def test_the_lock_is_held_by_one_live_process(base, other_pid):
    assert e.lock_holder(5, base) == 0
    assert e.acquire_lock(5, base, pid=os.getpid())
    assert e.lock_holder(5, base) == os.getpid()
    assert e.acquire_lock(5, base, pid=os.getpid())            # the holder may take it again
    e.lock_path(5, base).write_text("%d\n" % other_pid)         # another live process
    assert not e.acquire_lock(5, base, pid=os.getpid())
    e.lock_path(5, base).write_text("999999999\n")              # a process that is gone leaves a stale lock
    assert e.lock_holder(5, base) == 0 and e.acquire_lock(5, base, pid=os.getpid())
    e.release_lock(5, base)
    e.release_lock(5, base)
    assert e.lock_holder(5, base) == 0
    e.lock_path(5, base).parent.mkdir(parents=True, exist_ok=True)
    e.lock_path(5, base).write_text("abc")
    assert e.lock_holder(5, base) == 0


def test_an_unfinished_update_is_seen_only_without_a_runner(base):
    assert e.unfinished(9, base) == {}
    e.write_update(9, {"kind": "amd", "phase": "running", "snapshot": "s"}, base)
    assert e.unfinished(9, base)["kind"] == "amd" and e.read_update(9, base)["vmid"] == 9
    e.lock_path(9, base).write_text("%d\n" % os.getpid())
    assert e.unfinished(9, base) == {}                          # somebody is running it
    e.release_lock(9, base)
    e.write_update(9, {"kind": "amd", "phase": "done"}, base)
    assert e.unfinished(9, base) == {}                          # finished, waiting for the first boot question
    e.clear_update(9, base)
    e.clear_update(9, base)
    assert e.read_update(9, base) == {}


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
    assert run_main("lock", "101", str(os.getpid()))[0] == 0 and run_main("lock", "101", "x")[0] == 1
    assert run_main("unlock", "101")[0] == 0
    assert run_main("update-write", "101", '{"phase": "running", "kind": "amd"}')[0] == 0
    assert json.loads(run_main("unfinished", "101")[1])["kind"] == "amd"
    assert run_main("update-clear", "101")[0] == 0
    igpu = tmp_path / "igpu"
    igpu.mkdir()
    monkeypatch.setenv("IGPU_STATE_DIR", str(igpu))
    assert run_main("from-igpu", "104")[0] == 1
    (igpu / "104.json").write_text('{"whateverxe_version": "1.2"}')
    assert run_main("from-igpu", "104")[0] == 0 and e.read_record(104, base)["kind"] == "intel"
    assert run_main("bogus")[0] == 64
