"""The bash `driver-status --json` and the driver record it writes are the Python ones: the same keys, the same values in every
state, and the same record fields after a finish, an update and an undo. Python is the reference."""
from __future__ import annotations

import json
import re

import pytest
from pathlib import Path
from types import SimpleNamespace

from driver_fakes import DPve, foreign_lock, mac
from gpu_fakes import add_card, make_host
from nullmoth_fakes import good_card
from osx_proxmox_next import cli, driver_cli, driver_engine as engine, driver_plan, driver_run, driver_targets, igpu_engine
from osx_proxmox_next import driver_oc, nullmoth_finish, oc_builder
from osx_proxmox_next.driver_targets import Target
from osx_proxmox_next.infrastructure import CommandResult
from test_driver_parity import BASH, HOLD_LOCK, STUB_NULLMOTH, host, needs_bash4, write_record  # noqa: F401 - fixtures and helpers
from test_driver_parity_wave3 import amd_run

SHA = "ab" * 32
NV = {"NullMoth": {"version": "v2.9", "tag": "v2.9", "sha256": "", "source": "latest"}}
INTEL = {"WhateverXe": {"version": "0.8.3", "tag": "v0.8.3", "sha256": "", "source": "latest"},
         "WhateverXe Graphics": {"version": "0.4.4", "tag": "v0.4.4", "sha256": "", "source": "latest"}}
STUB_INTEL = ("driver_target() { DRIVER_TARGET=$(cat <<'J'\n" + json.dumps(INTEL) + "\nJ\n)\n"
              "  DRIVER_VERSIONS='{\"WhateverXe\": \"0.8.3\", \"WhateverXe Graphics\": \"0.4.4\"}'; }\n"
              "nullmoth_detect_macos() { echo sequoia; }\ngpu_record_load() { return 1; }\n")
KEYS = {"vmid", "name", "kind", "refused", "installed", "installed_from_record", "latest", "state", "snapshots", "unfinished",
        "latest_error", "updating", "step"}


def record(host, kind, sha="", source="", **versions):
    """A driver record of VM 109; `sha` and `source` empty is what the bash finish wrote before this fix."""
    host.state.mkdir(parents=True, exist_ok=True)
    parts = [engine.component(n.replace("_", " "), v, v, sha, source, "2026-11-01T00:00:00Z") for n, v in versions.items()]
    (host.state / "109.json").write_text(json.dumps({"vmid": 109, "kind": kind, "components": parts}))


def python_status(monkeypatch, capsys, host, cfg, target, status="running", snaps=(), error="", gone=False, disk=None, sysfs=None, get=None):
    """What `osx-next-cli driver-status --vmid 109 --json` prints on the Mac the bash host stub describes, and its exit code."""
    monkeypatch.setenv("DRIVER_STATE_DIR", str(host.state))
    monkeypatch.setenv("IGPU_STATE_DIR", host.env["IGPU_STATE_DIR"])
    pve = DPve(cfg=cfg, status=status, snapshots=list(snaps))
    for module in (driver_plan, driver_cli):
        monkeypatch.setattr(module, "get_proxmox_adapter", lambda: pve)
    if gone:
        monkeypatch.setattr(driver_plan, "fetch_vm_info", lambda *_a, **_k: None)
    if sysfs:      # the host's PCI devices: the one the test built, not the ones of the machine it runs on
        monkeypatch.setattr(driver_plan, "detect_kind", lambda vmid, cfg: driver_targets.detect_kind(vmid, cfg, Path(sysfs)))

    def resolve(kind, _choice, get=None):
        if error:
            raise igpu_engine.ReleaseError(error)
        return Target(kind, target)
    if get:        # the real lookup of the releases, against the canned answers of GitHub
        monkeypatch.setattr(igpu_engine, "github_get", get)
    else:
        monkeypatch.setattr(driver_plan, "resolve", resolve)
    disk = disk or {}
    monkeypatch.setattr(driver_plan.driver_oc, "read_marker", lambda _pve, _vmid, name: disk.get(name, ""))
    monkeypatch.setattr(driver_plan.driver_oc, "read_versions", lambda _pve, _vmid, names: {n: disk.get(n, "") for n in names})
    code = cli.run_cli(["driver-status", "--vmid", "109", "--json"])
    return code, json.loads(capsys.readouterr().out)


def bash_status(host, snippet, status="running", snaps=()):
    (host.qm_dir / "status").write_text(status + "\n")
    (host.qm_dir / "snaps").write_text("".join(s + "\n" for s in snaps))
    res = host.run(snippet + "driver_status_command 109 --json\n", expect=None)
    return res.returncode, json.loads(res.stdout), res


def both(monkeypatch, capsys, host, over, bash_stubs, target, expect_rc=0, **kw):
    """Run both statuses on the Mac `mac(**over)`, require the same exit code and the same object, and return it."""
    host.config(**over)
    py_rc, py_data = python_status(monkeypatch, capsys, host, mac(**over), target, **kw)
    sh_rc, sh_data, res = bash_status(host, bash_stubs, status=kw.get("status", "running"), snaps=kw.get("snaps", ()))
    assert set(py_data) == KEYS == set(sh_data)
    assert (sh_rc, sh_data) == (py_rc, py_data), res.stderr
    assert py_rc == expect_rc
    return py_data


@needs_bash4
def test_the_status_json_of_every_normal_state_is_the_python_one(host, monkeypatch, capsys):
    record(host, "nvidia", NullMoth="v2.8.1")
    data = both(monkeypatch, capsys, host, {}, STUB_NULLMOTH, NV, snaps=["osx-next-driver-20261120-0900"])
    assert data["state"] == "update" and data["installed"] == {"NullMoth": "v2.8.1"} and data["latest"] == {"NullMoth": "v2.9"}
    assert data["refused"] == [] and data["latest_error"] == "" and data["name"] == "macos-sequoia" and data["snapshots"]
    record(host, "nvidia", NullMoth="v2.9")                                                           # up to date
    assert both(monkeypatch, capsys, host, {}, STUB_NULLMOTH, NV)["state"] == "current"
    (host.state / "109.json").unlink()                                                                # no record at all
    gpu = "gpu_record_load() { REC_VENDOR=nvidia; return 0; }\n"
    monkeypatch.setattr(driver_targets.gpu, "load_record", lambda _vmid: SimpleNamespace(vendor="nvidia"))
    data = both(monkeypatch, capsys, host, {}, STUB_NULLMOTH + gpu, NV)
    assert data["installed"] is None and data["state"] == "unknown" and data["installed_from_record"] is False
    record(host, "intel", WhateverXe="0.8.0", WhateverXe_Graphics="0.4.0")                           # a running Mac: the record's word
    data = both(monkeypatch, capsys, host, {}, STUB_INTEL, INTEL)
    assert data["kind"] == "intel" and data["installed_from_record"] is True and data["state"] == "update"
    record(host, "nvidia", NullMoth="v2.8.1")                                                         # a stopped Mac: the marker beats the record
    marker = 'driver_oc_json() { echo \'{"NullMoth": "v2.9"}\'; }\n'
    data = both(monkeypatch, capsys, host, {}, STUB_NULLMOTH + marker, NV, status="stopped", disk={"NullMoth": "v2.9"})
    assert data["installed"] == {"NullMoth": "v2.9"} and data["state"] == "current"


@needs_bash4
def test_an_old_bash_record_with_empty_hash_and_source_reads_the_same_on_both_sides(host, monkeypatch, capsys):
    """Records written by bash before this fix have sha256 and source empty. Neither side distrusts them: the versions decide."""
    record(host, "nvidia", sha="", source="", NullMoth="v2.8.1")
    old = both(monkeypatch, capsys, host, {}, STUB_NULLMOTH, NV)
    record(host, "nvidia", sha=SHA, source="latest", NullMoth="v2.8.1")
    assert both(monkeypatch, capsys, host, {}, STUB_NULLMOTH, NV) == old
    assert old["installed"] == {"NullMoth": "v2.8.1"} and old["state"] == "update" and old["refused"] == []


@needs_bash4
def test_the_status_json_of_a_refused_mac_is_the_python_one(host, monkeypatch, capsys):
    record(host, "nvidia", NullMoth="v2.8.1")
    data = both(monkeypatch, capsys, host, {"hostpci0": None}, STUB_NULLMOTH, NV, expect_rc=2)           # no card on it
    assert data["refused"] == [engine.REFUSE_NO_CARD] and data["kind"] == "" and data["installed"] is None and data["latest"] == {}
    data = both(monkeypatch, capsys, host, {"args": "-smbios x"}, STUB_NULLMOTH, NV, expect_rc=2)       # not a Mac of this tool
    assert data["refused"] == [engine.REFUSE_NOT_MAC % 109] and data["kind"] == "nvidia" and data["state"] == ""
    data = both(monkeypatch, capsys, host, {"ide2": "local:iso/rec.img,media=disk"}, STUB_NULLMOTH, NV, expect_rc=2)
    assert data["refused"] == [engine.REFUSE_INSTALLING]
    data = both(monkeypatch, capsys, host, {"name": "plain"}, STUB_NULLMOTH.replace("echo sequoia", "true"), NV, expect_rc=2)
    assert len(data["refused"]) == 1 and data["refused"][0].startswith("Cannot tell which macOS")
    record(host, "intel", WhateverXe="0.8.0")
    data = both(monkeypatch, capsys, host, {"name": "macos-monterey"}, STUB_INTEL.replace("echo sequoia", "echo monterey"), INTEL, expect_rc=2)
    assert data["refused"] == [driver_plan.UNSUPPORTED_NOTE] and data["kind"] == "intel"


def pci_device(root, bdf, vendor, pci_class):
    folder = root / "bus/pci/devices" / bdf
    folder.mkdir(parents=True)
    (folder / "vendor").write_text(vendor + "\n")
    (folder / "class").write_text(pci_class + "\n")


@needs_bash4
def test_the_status_json_of_a_card_that_the_record_does_not_match_names_the_card(host, monkeypatch, capsys, tmp_path):
    """A record of one kind and a card of another: both refuse, and both say which card is there (not an empty kind)."""
    sysfs = tmp_path / "sys"
    pci_device(sysfs, "0000:01:00.0", "0x10de", "0x030000")
    host.env["SYSFS_ROOT"] = str(sysfs)
    record(host, "intel", WhateverXe="0.8.0")
    snaps = ["osx-next-driver-20261120-0900"]
    data = both(monkeypatch, capsys, host, {}, STUB_NULLMOTH, NV, expect_rc=2, sysfs=sysfs, snaps=snaps)
    assert data["refused"] == [engine.REFUSE_KIND_CHANGED] and data["kind"] == "nvidia"
    assert data["installed"] is None and data["latest"] == {} and data["state"] == "" and data["snapshots"] == snaps
    engine.write_update(109, {"kind": "intel", "phase": "failed", "snapshot": snaps[0]}, str(host.state))     # a cut-off run says its own kind
    data = both(monkeypatch, capsys, host, {}, STUB_INTEL, INTEL, sysfs=sysfs, snaps=snaps)
    assert data["kind"] == "intel" and data["unfinished"] is True
    engine.clear_update(109, str(host.state))
    (host.state / "109.json").unlink()                                                                       # no record, no readable card
    host.env["SYSFS_ROOT"] = str(tmp_path / "none")
    data = both(monkeypatch, capsys, host, {}, STUB_NULLMOTH, NV, expect_rc=2, sysfs=tmp_path / "none")
    assert data["refused"] == [engine.REFUSE_UNKNOWN_KIND] and data["kind"] == ""


@needs_bash4
def test_the_status_json_of_a_failed_release_lookup_is_the_python_one(host, monkeypatch, capsys):
    record(host, "nvidia", NullMoth="v2.8.1")
    down = 'nullmoth_engine() { echo "ERROR: GitHub could not be reached" >&2; return 1; }\nnullmoth_detect_macos() { echo sequoia; }\ngpu_record_load() { return 1; }\n'
    data = both(monkeypatch, capsys, host, {}, down, NV, expect_rc=2, error="GitHub could not be reached")
    assert data["refused"] == ["GitHub could not be reached"] and data["kind"] == "nvidia" and data["installed"] is None
    assert data["latest"] == {} and data["state"] == "" and data["latest_error"] == ""
    two = 'nullmoth_engine() { printf "ERROR: GitHub said no\\nTry again later\\n" >&2; return 1; }\nnullmoth_detect_macos() { echo sequoia; }\ngpu_record_load() { return 1; }\n'
    record(host, "nvidia", NullMoth="v2.8.1")
    data = both(monkeypatch, capsys, host, {}, two, NV, expect_rc=2, error="GitHub said no\nTry again later")     # the whole message, as Python shows it
    assert data["refused"] == ["GitHub said no\nTry again later"]
    record(host, "intel", WhateverXe="0.8.0")
    trace = ('driver_target() { printf \'Traceback (most recent call last):\\n  File "x", line 1\\nReleaseError: no WhateverXe release\\n\' >&2; return 1; }\n'
             "nullmoth_detect_macos() { echo sequoia; }\ngpu_record_load() { return 1; }\n")
    data = both(monkeypatch, capsys, host, {}, trace, INTEL, expect_rc=2, error="no WhateverXe release")
    assert data["refused"] == ["no WhateverXe release"]


@needs_bash4
def test_the_status_json_of_a_missing_vm_is_the_python_one(host, monkeypatch, capsys):
    python = python_status(monkeypatch, capsys, host, mac(), NV, gone=True)
    code, data, _res = bash_status(host, "qm() { return 1; }\n")
    assert (code, data) == python and data["refused"] == ["VM 109 not found."] and data["vmid"] == 109


@needs_bash4
def test_the_status_json_of_a_cut_off_update_is_the_python_one(host, monkeypatch, capsys):
    record(host, "nvidia", NullMoth="v2.8.1")                                                          # the card is off the Mac, the update stopped
    engine.write_update(109, {"kind": "nvidia", "phase": "failed", "snapshot": "osx-next-driver-20261121-1030"}, str(host.state))
    data = both(monkeypatch, capsys, host, {"hostpci0": None}, STUB_NULLMOTH, NV)
    assert data["unfinished"] is True and data["refused"] == [] and data["kind"] == "nvidia"


@needs_bash4
def test_the_status_json_while_an_update_runs_is_the_python_one(host, monkeypatch, capsys):
    steps = [{"id": i, "title": t, "done": d, "state": st} for (i, t), (_i, d), st in zip(
        engine.step_titles("nvidia", "NullMoth v2.9"), engine.step_titles("nvidia", "NullMoth v2.9", done=True),
        ["done", "done", "active", "pending", "pending", "pending", "pending"])]
    record(host, "nvidia", NullMoth="v2.8.1")
    engine.write_update(109, {"kind": "nvidia", "phase": "running", "steps": steps}, str(host.state))
    host.config()
    monkeypatch.setenv("DRIVER_STATE_DIR", str(host.state))
    held = foreign_lock(engine.lock_path(109, str(host.state)))
    try:
        python = python_status(monkeypatch, capsys, host, mac(), NV)
    finally:
        held.close()
    (host.qm_dir / "status").write_text("running\n")
    res = host.run(HOLD_LOCK + STUB_NULLMOTH + "driver_status_command 109 --json; rc=$?; kill $holder; exit $rc\n", expect=None)
    assert (res.returncode, json.loads(res.stdout)) == python
    assert python[1]["updating"] is True and python[1]["step"] == "3 of 7: Take the card off" and python[1]["refused"] == []
    assert python[1]["installed"] is None and python[1]["state"] == ""


@needs_bash4
def test_the_status_text_of_bash_is_untouched_by_the_json_work(host):
    record(host, "nvidia", NullMoth="v2.8.1")
    out = host.run(STUB_NULLMOTH + "driver_status_command 109\n").stdout.splitlines()
    assert out == ["VM 109 macos-sequoia: NVIDIA card", "Installed: NullMoth v2.8.1", "Latest:    NullMoth v2.9",
                   "A newer driver is available.", "Driver snapshots: none"]


# ── the record after a finish ────────────────────────────────────────────────

LVS_UUIDS = {"/dev/pve/vm-9-disk-1": "uuid-oc", "/dev/pve/vm-9-disk-2": "uuid-main"}


class RecordPve:
    """The Proxmox side of the Python record writers for a Mac whose OpenCore and main disks are logical volumes."""

    def __init__(self, cfg):
        self.cfg = cfg

    def qm(self, *args):
        return CommandResult(True, 0, "\n".join(f"{k}: {v}" for k, v in self.cfg.items())) if args[0] == "config" else CommandResult(True, 0, "")

    def pvesm(self, *args):
        return CommandResult(True, 0, "/dev/pve/" + args[1].split(":", 1)[1])

    def run(self, argv):
        return CommandResult(True, 0, LVS_UUIDS.get(argv[-1], "")) if argv[0] == "lvs" else CommandResult(True, 0, "")


def disks(host):
    host.env["UUIDS"] = "1"
    (host.qm_dir / "uuids").write_text("".join(f"{p} {u}\n" for p, u in LVS_UUIDS.items()))
    return RecordPve(mac())


def shape(rec):
    """A record without the moments it was written at."""
    out = {k: v for k, v in rec.items() if k != "updated"}
    out["components"] = [{k: v for k, v in c.items() if k != "installed_at"} for c in rec["components"]]
    if "previous" in rec:
        out["previous"] = [{k: v for k, v in c.items() if k != "installed_at"} for c in rec["previous"]]
    return out


def timed(rec):
    """Every moment of a record is a real UTC time (an empty one is what bash wrote before this fix)."""
    stamps = [rec["updated"]] + [c["installed_at"] for c in rec["components"] + rec.get("previous", [])]
    return all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", s) for s in stamps)


FINISH = r'''
LOGF="$TMPDIR/finish.log"; : >"$LOGF"
sleep() { :; }
igpu_wait_stopped() { return 0; }
igpu_drop_disk() { :; }
igpu_drop_disk_ref() { :; }
igpu_put_back() { :; }
igpu_oc_patch() { return 0; }
driver_oc_kext() { return 0; }
restore_picker_timeout() { return 0; }
download_recovery() { return 0; }
igpu_import_disk() { echo "local-lvm:vm-$1-disk-$RANDOM"; }
igpu_stage() { return 0; }
nullmoth_wait_result() { NM_RESULT="verified: kexts=4 display=no ip=10.0.0.9"; }
nullmoth_guest_address() { NM_IP=10.0.0.9; NM_SSH_UP=yes; NM_VNC_UP=yes; }
nullmoth_engine() {
  case "$1" in
    fetch|resolve) echo '{"tag": "v3.0", "path": "/c/nullmoth.tar.gz", "sha256": "SHA", "source": "chosen", "digest": "SHA"}' ;;
    fw-fetch) return 1 ;;
    payload) return 0 ;;
    parse-result) python3 -c 'print("{\"state\": \"verified\", \"detail\": \"\", \"kexts\": 4, \"display\": \"no\", \"ip\": \"10.0.0.9\"}")' ;;
  esac
}
'''.replace("SHA", SHA)


@needs_bash4
def test_the_nvidia_record_of_a_real_bash_finish_is_the_python_record(host, tmp_path, monkeypatch):
    """`nvidia-attach` from the checks to the record, with the host's card, the OpenCore disk, GitHub and the guest scripted."""
    root = make_host(tmp_path / "root")
    good_card(root / "sys")
    (tmp_path / "cmdline").write_text("quiet")
    host.env.update(GPU_ROOT=str(root), GPU_SYSFS=str(root / "sys"), GPU_CMDLINE=str(tmp_path / "cmdline"),
                    GPU_SNAPSHOT_BASE=str(tmp_path / "snap"), NULLMOTH_CACHE_DIR=str(tmp_path / "cache"))
    monkeypatch.setenv("DRIVER_STATE_DIR", str(host.state))
    pve = disks(host)
    host.config(hostpci0=None)
    res = host.run(FINISH + "nullmoth_fetch_amfipass() { echo /cache/a.zip; }\nnullmoth_oc_patch() { return 0; }\n"
                   "nullmoth_choose_version() { echo v3.0; }\nnullmoth_detect_macos() { echo sequoia; }\n"
                   "nvidia_attach 109 01:00 --accept-nullmoth-risk --yes\n", expect=None)
    assert res.returncode == 0, (res.stdout, res.stderr)
    bash_rec = json.loads((host.state / "109.json").read_text())
    nullmoth_finish._record_driver(110, {"tag": "v3.0", "sha": SHA, "source": "chosen"}, SimpleNamespace(notes=[]), pve)
    python_rec = json.loads((host.state / "110.json").read_text())
    python_rec["vmid"] = 109
    assert timed(bash_rec) and timed(python_rec)
    assert shape(bash_rec) == shape(python_rec)
    comp = bash_rec["components"][0]
    assert comp["sha256"] == SHA and comp["source"] == "chosen" and comp["tag"] == "v3.0" and bash_rec["fingerprint"]


IGPU_FINISH = r'''
sleep() { :; }
IGPU_ROM_KEY=x
igpu_check() { :; }
gpu_snapshot() { echo /dev/null; }
igpu_fetch_assets() { IGPU_ASSETS_JSON=$(cat <<'J'
@PICKS@
J
); IGPU_PKG=/dev/null; IGPU_ROM_KEY=x; }
igpu_engine_real() { gpu_engine_init && python3 "$GPU_ENGINE_DIR/igpu_engine.py" "$@"; }
igpu_engine() { case "$1" in payload) return 0 ;; result) echo installed ;; *) igpu_engine_real "$@" ;; esac; }
download_recovery() { return 0; }
igpu_import_disk() { echo "local-lvm:vm-109-disk-5"; }
igpu_stage() { return 0; }
igpu_drop_disk() { return 0; }
igpu_drop_disk_ref() { return 0; }
igpu_oc_patch() { return 0; }
restore_picker_timeout() { return 0; }
igpu_attach() { return 0; }
igpu_watchdog_enable() { return 0; }
igpu_wait_first_boot() { return 0; }
igpu_wait_stopped() { return 0; }
igpu_confirm_accel() { return 0; }
nullmoth_detect_macos() { echo sequoia; }
gpu_record_load() { return 1; }
'''
IGPU_PICKS = {"whateverxe": {"version": "0.8.3", "tag": "v0.8.3", "sha256": SHA, "source": "latest", "path": "/c/wex.zip"},
              "graphics": {"version": "0.4.4", "tag": "v0.4.4", "sha256": "cd" * 32, "source": "chosen", "path": "/c/gfx.pkg"}}


@needs_bash4
def test_the_intel_record_of_a_real_bash_finish_is_the_python_record(host, monkeypatch):
    """`igpu-finish` from the checks to the last record, with the host's disks, GitHub and the guest scripted."""
    pve = disks(host)
    host.config(hostpci0=None, machine="pc")
    igpu = Path(host.env["IGPU_STATE_DIR"])
    monkeypatch.setenv("IGPU_STATE_DIR", str(igpu))
    monkeypatch.setenv("DRIVER_STATE_DIR", str(host.state))
    res = host.run(IGPU_FINISH.replace("@PICKS@", json.dumps(IGPU_PICKS)) + "igpu_finish 109 sequoia\n", expect=None)
    assert res.returncode == 0, (res.stdout, res.stderr)
    bash_rec = json.loads((host.state / "109.json").read_text())
    from osx_proxmox_next import igpu_finish
    igpu_engine.write_state(110, igpu_engine.read_state(109))
    igpu_finish._record_state(110, {"package_installed": True, "package_result": "installed"}, SimpleNamespace(notes=[]), pve)
    python_rec = json.loads((host.state / "110.json").read_text())
    python_rec["vmid"] = 109
    assert timed(bash_rec) and timed(python_rec) and bash_rec["fingerprint"]
    assert shape(bash_rec) == shape(python_rec)
    assert [(c["name"], c["sha256"], c["source"]) for c in bash_rec["components"]][:2] == [
        ("WhateverXe", SHA, "latest"), ("WhateverXe Graphics", "cd" * 32, "chosen")]


@needs_bash4
def test_the_amd_record_of_a_real_bash_attach_is_the_python_record(host, tmp_path, monkeypatch):
    """`amd_attach` from the checks to the record, with the host's sysfs and the OpenCore disk scripted."""
    root = make_host(tmp_path / "root")
    add_card(root, device=0x73FF)
    (tmp_path / "cmdline").write_text("quiet")
    host.env.update(GPU_ROOT=str(root), GPU_SYSFS=str(root / "sys"), GPU_CMDLINE=str(tmp_path / "cmdline"), GPU_SNAPSHOT_BASE=str(tmp_path / "snap"))
    host.config(hostpci0=None)
    found = '{"Lilu.kext": "1.7.2", "WhateverGreen.kext": "1.7.0"}'
    host.run("gpu_oc_boot_args() { return 0; }\ndriver_oc_kext() { echo '" + found + "'; }\namd_attach 109 06:00 >/dev/null\n")
    bash_rec = json.loads((host.state / "109.json").read_text())
    monkeypatch.setenv("DRIVER_STATE_DIR", str(host.state))
    assert driver_oc.record_amd(DPve(bash_output=found), 110)
    python_rec = json.loads((host.state / "110.json").read_text())
    python_rec["vmid"] = 109
    assert timed(bash_rec) and timed(python_rec)
    assert shape(bash_rec) == shape(python_rec)
    assert [(c["name"], c["tag"], c["sha256"], c["source"]) for c in bash_rec["components"]] == [
        ("Lilu", "1.7.2", "", "disk"), ("WhateverGreen", "1.7.0", "", "disk")]


# ── the record after an update ───────────────────────────────────────────────

def python_update_record(host, kind, target, installed, extra=None, previous=(), pve=None, snapshot="osx-next-driver-20261121-1030",
                         card="0000:01:00,pcie=0"):
    """driver_run._record for VM 109 (the same Python function that writes the record after an update)."""
    pve = pve or RecordPve(mac())
    u = SimpleNamespace(
        vmid=109, env=SimpleNamespace(base=str(host.state)), pve=pve, live_cfg=lambda: mac(),
        result=SimpleNamespace(installed=dict(extra or {}), kept=[]),
        plan=SimpleNamespace(kind=kind, installed=installed, target=Target(kind, target)),
        state={"previous": [{"name": n, "version": v} for n, v in previous], "snapshot": snapshot, "card": card})
    driver_run._record(u)
    return json.loads((host.state / "109.json").read_text())


AMFIPASS = oc_builder.AMFIPASS
NV_UPDATE = FINISH + r'''
nullmoth_fetch_amfipass() { echo "$TMPDIR/amfi.zip"; }
nullmoth_detect_macos() { echo sequoia; }
gpu_record_load() { return 1; }
driver_oc_json() { case "$2" in marker) echo '{"NullMoth": "v2.8.1"}' ;; versions) echo '{"AMFIPass.kext": "AMFI_ON_DISK"}' ;; esac; }
'''


def bash_nvidia_update(host, amfi_on_disk):
    """The record after the real `driver-update 109 v3.0`: the whole command from the plan to the record, with only what touches
    the host's disks, GitHub and the guest stubbed."""
    res = host.run(NV_UPDATE.replace("AMFI_ON_DISK", amfi_on_disk)
                   + "driver_update_command 109 v3.0 --yes --accept-nullmoth-risk\n", expect=None)
    assert res.returncode == 0, (res.stdout, res.stderr)
    return json.loads((host.state / "109.json").read_text())


@needs_bash4
@pytest.mark.parametrize("amfi_on_disk,refreshed", [(AMFIPASS.version, False), ("1.0.0", True)])
def test_the_nvidia_record_of_a_real_bash_update_is_the_python_record(host, amfi_on_disk, refreshed):
    """The whole update, end to end. A component it did not refresh (AMFIPass) stays in the record, the versions before it and
    the snapshot are kept, and what first boot learned is not written on top of a stripped record."""
    pve = disks(host)
    before = {"NullMoth": "v2.8.1", "AMFIPass": "1.4.1-Mod"}
    record(host, "nvidia", NullMoth="v2.8.1", AMFIPass="1.4.1-Mod")
    bash_rec = bash_nvidia_update(host, amfi_on_disk)
    (host.state / "109.json").unlink()
    record(host, "nvidia", NullMoth="v2.8.1", AMFIPass="1.4.1-Mod")
    target = {"NullMoth": {"version": "v3.0", "tag": "v3.0", "sha256": SHA, "source": "chosen"}}
    extra = {"AMFIPass": {"version": AMFIPASS.version, "tag": AMFIPASS.version, "sha256": AMFIPASS.sha256, "source": "pinned"}}
    python_rec = python_update_record(host, "nvidia", target, before, extra=extra if refreshed else {}, previous=list(before.items()),
                                      pve=pve, snapshot=bash_rec["snapshot"], card=bash_rec["snapshot_card"])
    assert timed(bash_rec) and timed(python_rec)
    assert shape(bash_rec) == shape(python_rec)
    assert sorted(c["name"] for c in bash_rec["components"]) == ["AMFIPass", "NullMoth"]
    assert bash_rec["snapshot"] and len(bash_rec["previous"]) == 2 and bash_rec["fingerprint"]
    assert [c["sha256"] for c in bash_rec["components"] if c["name"] == "NullMoth"] == [SHA]


INTEL_UPDATE = r'''
sleep() { :; }
igpu_engine_real() { gpu_engine_init && python3 "$GPU_ENGINE_DIR/igpu_engine.py" "$@"; }
igpu_engine() {
  case "$1" in
    assets) cat <<'J'
@PICKS@
J
    ;;
    fetch) echo /dev/null ;;
    payload) return 0 ;;
    result) echo installed ;;
    *) igpu_engine_real "$@" ;;
  esac
}
driver_oc_json() { echo '{"WhateverXe.kext": "0.8.0"}'; }
download_recovery() { return 0; }
igpu_import_disk() { echo "local-lvm:vm-109-disk-5"; }
igpu_stage() { return 0; }
igpu_drop_disk() { return 0; }
igpu_drop_disk_ref() { return 0; }
driver_igpu_oc_patch() { return 0; }
restore_picker_timeout() { return 0; }
driver_watch_first_boot() { return 0; }
igpu_wait_stopped() { return 0; }
igpu_confirm_accel() { return 0; }
nullmoth_detect_macos() { echo sequoia; }
gpu_record_load() { return 1; }
'''


@needs_bash4
def test_the_intel_record_of_a_real_bash_update_is_the_python_record(host, monkeypatch):
    """`driver-update` of an Intel Mac, whole: the AMFIPass it staged is in the record next to the two it installed."""
    pve = disks(host)
    picks = {k: {f: v[f] for f in ("version", "tag", "sha256", "source")} for k, v in IGPU_PICKS.items()}
    target = {"WhateverXe": picks["whateverxe"], "WhateverXe Graphics": picks["graphics"]}
    record(host, "intel", WhateverXe="0.8.0", WhateverXe_Graphics="0.4.0")
    res = host.run(INTEL_UPDATE.replace("@PICKS@", json.dumps(IGPU_PICKS)) + "driver_update_command 109 --yes\n", expect=None)
    assert res.returncode == 0, (res.stdout, res.stderr)
    bash_rec = json.loads((host.state / "109.json").read_text())
    (host.state / "109.json").unlink()
    record(host, "intel", WhateverXe="0.8.0", WhateverXe_Graphics="0.4.0")
    amfi = {"AMFIPass": {"version": igpu_engine.AMFIPASS_VERSION, "tag": igpu_engine.AMFIPASS_VERSION,
                         "sha256": igpu_engine.PINS["amfipass"][2], "source": "pinned"}}
    python_rec = python_update_record(host, "intel", target, {"WhateverXe": "0.8.0", "WhateverXe Graphics": "0.4.0"}, extra=amfi,
                                      previous=[("WhateverXe", "0.8.0"), ("WhateverXe Graphics", "0.4.0")], pve=pve,
                                      snapshot=bash_rec["snapshot"], card=bash_rec["snapshot_card"])
    assert timed(bash_rec) and timed(python_rec) and bash_rec["fingerprint"]
    assert shape(bash_rec) == shape(python_rec)
    assert [c["name"] for c in bash_rec["components"]][-1] == "AMFIPass"


class UndoPve(DPve):
    """DPve on a Mac whose disks are logical volumes, so the record after an undo has the identity of its disks."""

    def run(self, argv):
        if argv[0] == "lvs":
            return CommandResult(True, 0, LVS_UUIDS.get(argv[-1], ""))
        return super().run(argv)

    def pvesm(self, *args):
        return CommandResult(True, 0, "/dev/pve/" + args[1].split(":", 1)[1])


@needs_bash4
@pytest.mark.parametrize("kind,versions,disk", [("intel", {"WhateverXe": "0.8.3", "WhateverXe_Graphics": "0.4.4"}, '{"WhateverXe.kext": "0.8.0"}'),
                                                ("nvidia", {"NullMoth": "v2.9"}, "{}")])
def test_the_record_after_a_real_bash_undo_is_the_python_record(host, monkeypatch, kind, versions, disk):
    """`driver-undo` whole: the versions before the update are back, the snapshot is gone and the disks' identity is the new one."""
    from osx_proxmox_next.driver_core import RunEnv
    disks(host)
    snap = "osx-next-driver-20261121-1030"
    previous = {"WhateverXe": "0.8.2", "WhateverXe_Graphics": "0.4.3"} if kind == "intel" else {"NullMoth": "v2.8.1"}
    monkeypatch.setenv("DRIVER_STATE_DIR", str(host.state))

    def prepare():
        engine.write_record(109, kind, [engine.component(n.replace("_", " "), v, v, SHA, "latest", "2026-11-01T00:00:00Z") for n, v in versions.items()],
                            str(host.state), previous=[engine.component(n.replace("_", " "), v) for n, v in previous.items()],
                            snapshot=snap, card="0000:01:00,pcie=0", fingerprint="old")
    prepare()
    (host.qm_dir / "snaps").write_text(snap + "\n")
    (host.qm_dir / "status").write_text("running\n")
    host.run(f"driver_oc_json() {{ echo '{disk}'; }}\ndriver_oc_kext() {{ echo '{disk}'; }}\ndriver_undo_command 109 --yes >/dev/null\n")
    bash_rec = json.loads((host.state / "109.json").read_text())
    prepare()
    driver_run.put_back(109, RunEnv(UndoPve(status="running", snapshots=[snap], bash_output=disk), base=str(host.state)))
    python_rec = json.loads((host.state / "109.json").read_text())
    assert timed(bash_rec) and timed(python_rec) and bash_rec["fingerprint"] != "old"
    assert shape(bash_rec) == shape(python_rec)
    assert engine.installed_versions(bash_rec) == ({"WhateverXe": "0.8.0", "WhateverXe Graphics": "0.4.3"} if kind == "intel" else {"NullMoth": "v2.8.1"})
    assert "snapshot" not in bash_rec


# ── the Intel status asks GitHub and downloads nothing ───────────────────────

FAKE_NET = """import json, os, urllib.request

class _Reply:
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
    def read(self):
        return self.body

def _urlopen(req, timeout=None):
    url = req if isinstance(req, str) else req.full_url
    if url.startswith("https://api.github.com/"):
        return _Reply(json.dumps(json.load(open(os.environ["FAKE_RELEASES"]))[url]).encode())
    with open(os.environ["FAKE_DOWNLOADS"], "a") as log:
        log.write(url + "\\n")
    raise OSError("the download is refused here")

urllib.request.urlopen = _urlopen
"""


def canned_releases():
    def release(repo, tag, name):
        return {"tag_name": tag, "prerelease": False, "draft": False, "published_at": "2026-11-01T00:00:00Z",
                "assets": [{"name": name, "digest": "sha256:" + SHA,
                            "browser_download_url": f"https://github.com/{repo}/releases/download/{tag}/{name}"}]}
    return {"https://api.github.com/repos/b00t0x/WhateverXe/releases?per_page=30":
            [release("b00t0x/WhateverXe", "v0.8.3", "WhateverXe-0.8.3-RELEASE.zip")],
            "https://api.github.com/repos/b00t0x/WhateverXe-Graphics/releases?per_page=30":
            [release("b00t0x/WhateverXe-Graphics", "v0.4.4", "WhateverXe-Graphics-0.4.4.pkg")]}


@needs_bash4
def test_the_intel_status_looks_the_releases_up_and_downloads_nothing(host, monkeypatch, capsys, tmp_path):
    """Python's status only asks GitHub which releases there are. Bash used to download them as well, so a download that
    failed showed as a refusal (exit 2) where Python says nothing is wrong."""
    net = tmp_path / "fakenet"
    net.mkdir()
    (net / "sitecustomize.py").write_text(FAKE_NET)
    (net / "releases.json").write_text(json.dumps(canned_releases()))
    host.env.update(PYTHONPATH=f"{net}:{host.env['PYTHONPATH']}", FAKE_RELEASES=str(net / "releases.json"),
                    FAKE_DOWNLOADS=str(tmp_path / "downloads.log"))
    record(host, "intel", WhateverXe="0.8.0", WhateverXe_Graphics="0.4.0")
    prelude = "nullmoth_detect_macos() { echo sequoia; }\ngpu_record_load() { return 1; }\n"
    data = both(monkeypatch, capsys, host, {}, prelude, None, get=lambda url: canned_releases()[url])
    assert data["latest"] == {"WhateverXe": "0.8.3", "WhateverXe Graphics": "0.4.4"} and data["refused"] == [] and data["state"] == "update"
    out = host.run(prelude + "driver_status_command 109\n").stdout                       # the text status asks the same way
    assert "Latest:    WhateverXe 0.8.3 and WhateverXe Graphics 0.4.4" in out
    assert not (tmp_path / "downloads.log").exists()


@needs_bash4
def test_the_amfipass_version_of_a_bash_intel_update_is_the_one_of_the_engine(host):
    """The version comes from the engine's own constant, not from cutting the pinned file name apart."""
    disks(host)
    record(host, "intel", WhateverXe="0.8.0", WhateverXe_Graphics="0.4.0")
    bump = ('gpu_engine_init\npython3 - "$GPU_ENGINE_DIR/igpu_engine.py" <<\'PY\'\nimport re, sys\ntext = open(sys.argv[1]).read()\n'
            'open(sys.argv[1], "w").write(re.sub(r"(?m)^AMFIPASS_VERSION = .*$", \'AMFIPASS_VERSION = "9.9.9-Mod"\', text))\nPY\n')
    res = host.run(bump + INTEL_UPDATE.replace("@PICKS@", json.dumps(IGPU_PICKS)) + "driver_update_command 109 --yes\n", expect=None)
    assert res.returncode == 0, (res.stdout, res.stderr)
    amfi = [c for c in json.loads((host.state / "109.json").read_text())["components"] if c["name"] == "AMFIPass"]
    assert [(c["version"], c["tag"]) for c in amfi] == [("9.9.9-Mod", "9.9.9-Mod")]


@needs_bash4
def test_the_amd_record_of_a_real_bash_update_is_the_python_record(host):
    """The AMD update from the plan's run to the record: both kexts replaced, the earlier versions kept for the undo."""
    import hashlib
    pve = disks(host)
    bash_run = amd_run(host, 'driver_oc_kext() { echo \'{}\'; }\n')
    host.run(bash_run)
    bash_rec = json.loads((host.state / "109.json").read_text())
    versions = {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}
    target = {n: {"version": v, "tag": v, "sha256": hashlib.sha256(n.encode()).hexdigest(), "source": "pinned"} for n, v in versions.items()}
    before = {"Lilu": "1.6.8", "WhateverGreen": "1.6.0"}
    (host.state / "109.json").unlink()
    write_record(host, "amd", Lilu="1.6.8", WhateverGreen="1.6.0")
    python_rec = python_update_record(host, "amd", target, before, previous=list(before.items()), pve=pve,
                                      snapshot=bash_rec["snapshot"], card=bash_rec["snapshot_card"])
    assert timed(bash_rec) and timed(python_rec) and bash_rec["fingerprint"]
    assert shape(bash_rec) == shape(python_rec)
    assert engine.installed_versions(bash_rec) == versions
