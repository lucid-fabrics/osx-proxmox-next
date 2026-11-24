"""Fix wave 3, the bash twin: the lines and the behaviour the Python side has, compared line for line."""
from __future__ import annotations

import hashlib
import json

from driver_fakes import comps
from osx_proxmox_next import driver_engine as engine
from test_driver_parity import (  # noqa: F401 - fixtures and helpers
    HOLD_LOCK, NV, SNAP, STUB_NULLMOTH, UNDO, host, needs_bash4, py_lines, write_record)

OLD = "osx-next-driver-20261101-0900"
MINE = "local-lvm:vm-109-disk-30"
THEIRS = "local-lvm:vm-109-disk-77"
INTEL = {"WhateverXe": {"version": "0.8.3", "tag": "v0.8.3", "sha256": "", "source": "latest"},
         "WhateverXe Graphics": {"version": "0.4.4", "tag": "v0.4.4", "sha256": "", "source": "latest"}}


def amd_run(host, extra=""):
    """An AMD update of VM 109 from old kexts, with the OpenCore script replaced by a log line."""
    write_record(host, "amd", Lilu="1.6.8", WhateverGreen="1.6.0")
    zips = {}
    for name in ("Lilu", "WhateverGreen"):
        path = host.cache / f"{name}.zip"
        path.write_bytes(name.encode())
        zips[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return ('OC_COMPONENT_URLS=([Lilu]="https://x/releases/download/1.7.2/Lilu.zip" [WhateverGreen]="https://x/releases/download/1.7.0/WhateverGreen.zip")\n'
            f'OC_COMPONENT_SHA256=([Lilu]="{zips["Lilu"]}" [WhateverGreen]="{zips["WhateverGreen"]}")\n'
            + extra + 'DRIVER_VERSION_TEXT="1.7.2, 1.7.0"\ncfg=$(qm config 109)\n'
            'DRIVER_INSTALLED=\'{"Lilu": "1.6.8", "WhateverGreen": "1.6.0"}\'\n'
            f'driver_target amd "" "" ""\ndriver_apply 109 amd "$cfg" sequoia {SNAP} yes "" no macos-sequoia\n')


# ── plans ────────────────────────────────────────────────────────────

@needs_bash4
def test_the_bash_dry_run_names_its_own_undo_command_not_the_cli(host):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    out = host.run(STUB_NULLMOTH + "driver_update_command 109 --dry-run\n").stdout
    assert "Afterwards osx-proxmox-next.sh driver-undo 109 undoes the driver update." in out
    assert "osx-next-cli" not in out and "Afterwards r undoes" not in out


@needs_bash4
def test_the_bash_finish_plan_is_the_python_finish_plan(host):
    steps = [{"id": i, "title": t, "done": d, "state": st} for (i, t), (_i, d), st in zip(
        engine.step_titles("nvidia", "NullMoth v2.9"), engine.step_titles("nvidia", "NullMoth v2.9", done=True),
        ["done", "done", "done", "fail", "pending", "pending", "pending"])]
    state = {"kind": "nvidia", "phase": "failed", "snapshot": SNAP, "steps": steps}
    engine.write_update(109, state, str(host.state))
    snippet = (STUB_NULLMOTH + "DRIVER_VERSIONS='{\"NullMoth\": \"v2.9\"}'\nDRIVER_TARGET=$(cat <<'J'\n" + json.dumps(NV) + "\nJ\n)\n"
               "driver_print_plan 109 macos-sequoia nvidia '{\"NullMoth\": \"v2.8.1\"}' \"$DRIVER_TARGET\" osx-next-driver-NEW yes no sequoia yes\n")
    out = host.run(snippet).stdout.splitlines()
    assert out == py_lines("nvidia", {"NullMoth": "v2.8.1"}, NV, "osx-next-driver-NEW", resume=state)
    assert out[0] == "Finish the NVIDIA driver update of macos-sequoia (VM 109)" and "  1. Stage NullMoth v2.9" in out
    assert not [l for l in out if "is saved first" in l or "Save a snapshot" in l]


@needs_bash4
def test_the_bash_intel_plan_has_the_python_warnings(host):
    for macos in ("sequoia", "tahoe"):
        snippet = ("nullmoth_detect_macos() { echo " + macos + "; }\nDRIVER_VERSIONS='{\"WhateverXe\": \"0.8.3\", \"WhateverXe Graphics\": \"0.4.4\"}'\n"
                   "DRIVER_TARGET=$(cat <<'J'\n" + json.dumps(INTEL) + "\nJ\n)\n"
                   "driver_print_plan 109 macos-sequoia intel '{\"WhateverXe\": \"0.8.0\"}' \"$DRIVER_TARGET\" " + SNAP + " yes no " + macos + "\n")
        out = host.run(snippet).stdout.splitlines()
        assert out == py_lines("intel", {"WhateverXe": "0.8.0"}, INTEL, SNAP, macos=macos)
        assert "Warning: " + engine.INTEL_BOOT_NOTE in out


# ── status ───────────────────────────────────────────────────────────

@needs_bash4
def test_bash_status_marks_versions_of_a_running_mac_that_come_from_the_record(host):
    write_record(host, "amd", Lilu="1.7.2", WhateverGreen="1.7.0")
    (host.qm_dir / "status").write_text("running\n")
    snippet = ('OC_COMPONENT_URLS=([Lilu]="https://x/releases/download/1.7.2/Lilu.zip" [WhateverGreen]="https://x/releases/download/1.7.0/WhateverGreen.zip")\n'
               'OC_COMPONENT_SHA256=([Lilu]="a" [WhateverGreen]="b")\n')
    out = host.run(snippet + "driver_status_command 109\n").stdout
    assert "Installed: Lilu 1.7.2 and WhateverGreen 1.7.0 (from the last update)" in out
    assert json.loads(host.run(snippet + "driver_status_command 109 --json\n").stdout)["installed_from_record"] is True
    (host.qm_dir / "status").write_text("stopped\n")
    stopped = host.run(snippet + 'driver_oc_kext() { echo \'{"Lilu.kext": "1.7.2", "WhateverGreen.kext": "1.7.0"}\'; }\ndriver_status_command 109\n').stdout
    assert "Installed: Lilu 1.7.2 and WhateverGreen 1.7.0\n" in stopped and "from the last update" not in stopped


# ── one snapshot, the leftover disks, the start line ─────────────────

@needs_bash4
def test_a_bash_update_that_fails_keeps_exactly_one_driver_snapshot(host):
    (host.qm_dir / "snaps").write_text(OLD + "\nmine\n")
    out = host.run(amd_run(host, 'driver_oc_kext() { return 1; }\n'), expect=None).stdout
    assert "The update did not finish." in out and f"Removed the previous driver snapshot: {OLD}" in out
    assert (host.qm_dir / "snaps").read_text().split() == ["mine", SNAP]


@needs_bash4
def test_a_bash_run_that_starts_the_mac_says_so(host):
    out = host.run(amd_run(host, 'driver_oc_kext() { :; }\n')).stdout
    assert "  Started the Mac" in out


@needs_bash4
def test_bash_undo_removes_the_disks_the_update_added_that_a_manual_rollback_kept(host):
    write_record(host, "nvidia", NullMoth="v2.9")
    (host.qm_dir / "snaps").write_text(SNAP + "\n")
    host.config(unused0=THEIRS, unused1=MINE)
    engine.write_update(109, {"kind": "nvidia", "phase": "failed", "snapshot": SNAP, "steps": [], "disks": [MINE],
                              "card": "0000:01:00,pcie=0"}, str(host.state))
    out = host.run("driver_undo_command 109 --yes\n").stdout
    assert f"  Removed the leftover disk of the update: {MINE}" in out and f"Removed the driver snapshot {SNAP}" in out
    cfg = (host.qm_dir / "config").read_text()
    assert "unused1" not in cfg and f"unused0: {THEIRS}" in cfg
    assert (host.qm_dir / "snaps").read_text().strip() == ""


@needs_bash4
def test_bash_records_each_disk_the_staging_imports_only_while_a_driver_run_holds_the_lock(host):
    host.state.mkdir(parents=True, exist_ok=True)
    engine.write_update(109, {"kind": "intel", "steps": []}, str(host.state))
    imports = ('qm() { if [ "$1" == importdisk ] || [ "$1" == disk ]; then echo "successfully imported disk \'local-lvm:vm-109-disk-5\'"; fi; }\n')
    out = host.run(imports + 'DRIVER_VMID=109\ndriver_lock_acquire 109\nigpu_import_disk 109 /x.img local-lvm\ndriver_lock_release 109\n'
                   'engine_after=$(driver_engine update-get 109 disks)\nigpu_import_disk 109 /y.img local-lvm\n').stdout
    assert out.split() == ["local-lvm:vm-109-disk-5"] * 2
    assert engine.read_update(109, str(host.state))["disks"] == ["local-lvm:vm-109-disk-5"]      # the import after the run is not recorded


@needs_bash4
def test_a_card_setup_after_a_driver_run_in_the_same_shell_leaves_no_state_of_a_stopped_update(host):
    imports = ('qm() { if [ "$1" == importdisk ] || [ "$1" == disk ]; then echo "successfully imported disk \'local-lvm:vm-109-disk-5\'"; fi; }\n')
    host.run(imports + 'DRIVER_VMID=109\ndriver_lock_acquire 109\ndriver_lock_release 109\nigpu_import_disk 109 /y.img local-lvm >/dev/null\n')
    assert not (host.state / "109.update.json").exists() and engine.unfinished(109, str(host.state)) == {}


@needs_bash4
def test_bash_finish_removes_the_leftover_disk_first_and_starts_a_fresh_list(host):
    write_record(host, "amd", Lilu="1.6.8", WhateverGreen="1.6.0")
    host.config(hostpci0=None, unused1=MINE)
    steps = [{"id": i, "title": t, "done": d, "state": st, "started": 1.0, "ended": 99.0} for (i, t), (_i, d), st in zip(
        engine.step_titles("amd"), engine.step_titles("amd", done=True), ["done", "done", "fail", "pending"])]
    engine.write_update(109, {"kind": "amd", "phase": "failed", "snapshot": SNAP, "steps": steps, "disks": [MINE]}, str(host.state))
    host.run("driver_drop_unused 109\ndriver_engine update-resume 109\n")
    assert "unused1" not in (host.qm_dir / "config").read_text()
    rows = engine.read_update(109, str(host.state))["steps"]
    assert rows[0] == {"id": "stop", "title": "Stop the Mac", "done": "Stopped the Mac", "state": "done", "carried": True}
    assert rows[2]["state"] == "pending" and "started" not in rows[2] and "ended" not in rows[2]


# ── the OpenCore disk of a running Mac leaves nothing behind ─────────

@needs_bash4
def test_bash_reading_the_opencore_disk_of_a_running_mac_refuses_and_leaves_no_map(host):
    (host.qm_dir / "status").write_text("running\n")
    (host.qm_dir / "maps").write_text("pve-vm--9--disk--1p1\t(253:5)\npve-vm--9--disk--2p1\t(253:6)\n")
    out = host.run("driver_oc_kext 109 versions Lilu.kext\n", expect=1)
    assert "not stopped" in out.stderr
    assert (host.qm_dir / "maps").read_text().strip() == ""           # the Mac's volumes carry no partition map afterwards


# ── the first boot that never reports ────────────────────────────────

@needs_bash4
def test_bash_first_boot_text_is_the_python_text(host):
    for result, waited, idle in (("staged", 700, 650), ("", 1500, 30), ("failed: installer error (try 1 of 3)", 100, 0),
                                 ("not installed: WhateverXe not loaded (try 1 of 3)", 100, 0)):
        out = host.run(f'driver_engine first-boot-text "{result}" {waited} {idle}\n').stdout.strip()
        assert out == engine.first_boot_text(result, waited, idle)


@needs_bash4
def test_bash_calls_a_silent_mac_with_no_disk_io_stuck_and_a_busy_one_not(host):
    (host.qm_dir / "status").write_text("running\n")
    (host.qm_dir / "ops").write_text("100\n")
    stuck = ('IGPU_FIRSTBOOT_TIMEOUT=60 DRIVER_STUCK_AFTER=2 DRIVER_POLL=1\nigpu_engine() { echo staged; }\npve_vol_path() { echo /dev/x; }\n'
             'driver_watch_first_boot 109 ref no; echo "rc=$? stuck=$DRIVER_STUCK idle=$DRIVER_IDLE"\n')
    out = host.run(stuck).stdout
    assert "rc=1 stuck=yes" in out and int(out.split("idle=")[1]) >= 2
    busy = ('IGPU_FIRSTBOOT_TIMEOUT=4 DRIVER_STUCK_AFTER=2 DRIVER_POLL=1\nigpu_engine() { echo staged; }\npve_vol_path() { echo /dev/x; }\n'
            '( while :; do echo $RANDOM > "$QM_DIR/ops"; sleep 0.3; done ) & mover=$!\n'
            'driver_watch_first_boot 109 ref no; rc=$?; kill $mover; echo "rc=$rc stuck=$DRIVER_STUCK"\n')
    assert "rc=1 stuck=no" in host.run(busy).stdout
    (host.qm_dir / "ops").unlink()
    assert "rc=1 stuck=no" in host.run(stuck.replace("TIMEOUT=60", "TIMEOUT=4")).stdout       # no counter: never stuck


@needs_bash4
def test_bash_watch_ends_at_once_when_the_mac_powers_itself_off(host):
    (host.qm_dir / "status").write_text("stopped\n")
    out = host.run("IGPU_FIRSTBOOT_TIMEOUT=60 DRIVER_POLL=1\ndriver_watch_first_boot 109 ref no; echo \"rc=$? stuck=$DRIVER_STUCK\"\n").stdout
    assert "rc=0 stuck=no" in out


@needs_bash4
def test_bash_forgets_a_disk_it_deleted_and_never_removes_one_by_a_reused_id(host):
    engine.write_update(109, {"kind": "nvidia", "phase": "failed", "steps": [], "disks": [MINE]}, str(host.state))
    host.config(sata2=MINE)
    host.run("DRIVER_VMID=109\ndriver_lock_acquire 109\nigpu_drop_disk 109 sata2\ndriver_lock_release 109\n")
    assert engine.read_update(109, str(host.state))["disks"] == []
    host.config(unused1=MINE, unused2=MINE + "0")
    host.run("driver_drop_unused 109\n")
    assert f"unused1: {MINE}" in (host.qm_dir / "config").read_text()              # not the run's any more
    engine.write_update(109, {"kind": "nvidia", "phase": "failed", "steps": [], "disks": [MINE]}, str(host.state))
    host.run("driver_drop_unused 109\n")
    cfg = (host.qm_dir / "config").read_text()
    assert "unused1" not in cfg and f"unused2: {MINE}0" in cfg                      # exact id, not a prefix or a pattern


# ── the cosmetic fixes: plan words, one verdict, versions after an undo ──

@needs_bash4
def test_the_bash_plan_says_kept_for_a_component_already_at_the_target_in_the_python_words(host):
    installed = {"WhateverXe": "0.8.2", "WhateverXe Graphics": "0.4.4"}
    snippet = ("nullmoth_detect_macos() { echo sequoia; }\nDRIVER_VERSIONS='{\"WhateverXe\": \"0.8.3\", \"WhateverXe Graphics\": \"0.4.4\"}'\n"
               "DRIVER_TARGET=$(cat <<'J'\n" + json.dumps(INTEL) + "\nJ\n)\n"
               "driver_print_plan 109 macos-sequoia intel '" + json.dumps(installed) + "' \"$DRIVER_TARGET\" " + SNAP + " yes no sequoia\n")
    out = host.run(snippet).stdout.splitlines()
    assert out == py_lines("intel", installed, INTEL, SNAP)
    assert "  WhateverXe Graphics: already 0.4.4, installed again with the update" in out and not any("reinstalled" in l for l in out)


@needs_bash4
def test_bash_status_of_an_unfinished_update_has_no_up_to_date_line(host):
    write_record(host, "nvidia", NullMoth="v2.9")
    host.config(hostpci0=None)
    engine.write_update(109, {"kind": "nvidia", "phase": "failed", "snapshot": SNAP, "steps": []}, str(host.state))
    out = host.run(STUB_NULLMOTH + "driver_status_command 109\n").stdout
    assert "The driver update stopped before it finished." in out and "Up to date." not in out
    engine.clear_update(109, str(host.state))
    host.config()
    assert "Up to date." in host.run(STUB_NULLMOTH + "driver_status_command 109\n").stdout


@needs_bash4
def test_bash_undo_reads_the_rolled_back_disk_so_the_versions_are_not_from_the_last_update(host):
    write_record(host, "intel", WhateverXe="0.8.3", WhateverXe_Graphics="0.4.4")
    rec = json.loads((host.state / "109.json").read_text())
    rec["previous"] = comps(WhateverXe="0.8.2", WhateverXe_Graphics="0.4.3")
    (host.state / "109.json").write_text(json.dumps(rec))
    (host.qm_dir / "snaps").write_text(SNAP + "\n")
    disk = 'driver_oc_kext() { echo \'{"WhateverXe.kext": "0.8.0"}\'; }\n'
    host.run(disk + "driver_undo_command 109 --yes\n")
    record = engine.read_record(109, str(host.state))
    assert record["disk_read"] is True and engine.installed_versions(record) == {"WhateverXe": "0.8.0", "WhateverXe Graphics": "0.4.3"}
    (host.qm_dir / "status").write_text("running\n")                         # the Mac runs again: the record is the disk's word
    out = host.run('driver_oc_kext() { echo "{}"; }\n' + STUB_NULLMOTH + "driver_status_command 109\n").stdout
    assert "Installed: WhateverXe 0.8.0 and WhateverXe Graphics 0.4.3\n" in out and "from the last update" not in out


# ── a Mac that runs now is stopped first, whatever the run recorded ──

def _amd_without_stop(host):
    rows = [(i, t, d) for (i, t), (_i, d) in zip(engine.step_titles("amd"), engine.step_titles("amd", done=True)) if i != "stop"]
    steps = [{"id": i, "title": t, "done": d, "state": st} for (i, t, d), st in zip(rows, ["done", "fail", "pending"])]
    state = {"kind": "amd", "phase": "failed", "snapshot": SNAP, "steps": steps}
    engine.write_update(109, state, str(host.state))
    return state


@needs_bash4
def test_bash_finish_of_a_run_recorded_without_a_stop_row_lists_and_does_the_stop_when_the_mac_runs(host):
    write_record(host, "amd", Lilu="1.6.8", WhateverGreen="1.6.0")
    state = _amd_without_stop(host)
    target = '{"Lilu": {"version": "1.7.2"}, "WhateverGreen": {"version": "1.7.0"}}'
    plan = ("DRIVER_VERSIONS='{\"Lilu\": \"1.7.2\", \"WhateverGreen\": \"1.7.0\"}'\n"
            f"driver_print_plan 109 mac amd '{{\"Lilu\": \"1.6.8\"}}' '{target}' osx-next-driver-NEW yes yes sequoia yes\n")
    assert "  1. Stop the Mac" in host.run(plan).stdout
    assert "Stop the Mac" not in host.run(plan.replace("yes sequoia yes", "no sequoia yes")).stdout
    (host.qm_dir / "status").write_text("running\n")
    host.run('DRIVER_VMID=109\ndriver_engine update-resume 109 running\ndriver_step_stop 109\n')
    rows = engine.read_update(109, str(host.state))["steps"]
    assert [r["id"] for r in rows][:2] == ["snapshot", "stop"] and rows[1]["state"] == "done"
    assert any(c.startswith(("shutdown 109", "stop 109")) for c in host.calls())      # the running Mac was stopped first
    assert [r["id"] for r in state["steps"]] == ["snapshot", "replace", "start"]       # the run was recorded without the row


@needs_bash4
def test_bash_finish_of_a_run_recorded_without_a_stop_row_adds_none_for_a_stopped_mac(host):
    write_record(host, "amd", Lilu="1.6.8", WhateverGreen="1.6.0")
    _amd_without_stop(host)
    host.run('DRIVER_VMID=109\ndriver_engine update-resume 109\ndriver_step_stop 109\n')
    assert all(r["id"] != "stop" for r in engine.read_update(109, str(host.state))["steps"])
