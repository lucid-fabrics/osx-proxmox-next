"""The bash `driver-status --json` and the driver record it writes are the Python ones: the same keys, the same values in every
state, and the same record fields after a finish, an update and an undo. Python is the reference."""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from driver_fakes import DPve, foreign_lock, mac
from osx_proxmox_next import cli, driver_cli, driver_engine as engine, driver_plan, driver_run, driver_targets, igpu_engine
from osx_proxmox_next import nullmoth_finish
from osx_proxmox_next.driver_targets import Target
from osx_proxmox_next.infrastructure import CommandResult
from test_driver_parity import BASH, HOLD_LOCK, STUB_NULLMOTH, host, needs_bash4  # noqa: F401 - fixtures and helpers

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


def python_status(monkeypatch, capsys, host, cfg, target, status="running", snaps=(), error="", gone=False, disk=None):
    """What `osx-next-cli driver-status --vmid 109 --json` prints on the Mac the bash host stub describes, and its exit code."""
    monkeypatch.setenv("DRIVER_STATE_DIR", str(host.state))
    monkeypatch.setenv("IGPU_STATE_DIR", host.env["IGPU_STATE_DIR"])
    pve = DPve(cfg=cfg, status=status, snapshots=list(snaps))
    for module in (driver_plan, driver_cli):
        monkeypatch.setattr(module, "get_proxmox_adapter", lambda: pve)
    if gone:
        monkeypatch.setattr(driver_plan, "fetch_vm_info", lambda *_a, **_k: None)

    def resolve(kind, _choice, get=None):
        if error:
            raise igpu_engine.ReleaseError(error)
        return Target(kind, target)
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


@needs_bash4
def test_the_status_json_of_a_failed_release_lookup_is_the_python_one(host, monkeypatch, capsys):
    record(host, "nvidia", NullMoth="v2.8.1")
    down = 'nullmoth_engine() { echo "ERROR: GitHub could not be reached" >&2; return 1; }\nnullmoth_detect_macos() { echo sequoia; }\ngpu_record_load() { return 1; }\n'
    data = both(monkeypatch, capsys, host, {}, down, NV, expect_rc=2, error="GitHub could not be reached")
    assert data["refused"] == ["GitHub could not be reached"] and data["kind"] == "nvidia" and data["installed"] is None
    assert data["latest"] == {} and data["state"] == "" and data["latest_error"] == ""
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
    fetch) echo '{"tag": "v3.0", "path": "/c/nullmoth.tar.gz", "sha256": "SHA", "source": "chosen", "digest": "SHA"}' ;;
    fw-fetch) return 1 ;;
    payload) return 0 ;;
    parse-result) python3 -c 'print("{\"state\": \"verified\", \"detail\": \"\", \"kexts\": 4, \"display\": \"no\", \"ip\": \"10.0.0.9\"}")' ;;
  esac
}
'''.replace("SHA", SHA)


@needs_bash4
def test_the_nvidia_record_of_a_bash_finish_is_the_python_record(host, monkeypatch):
    monkeypatch.setenv("DRIVER_STATE_DIR", str(host.state))
    pve = disks(host)
    host.run(FINISH + 'nullmoth_stage 109 v3.0 sequoia >/dev/null\nNM_PAYLOAD_REF=ref; NM_PAYLOAD_FILE="$TMPDIR/p.img"; : >"$NM_PAYLOAD_FILE"\n'
             "nullmoth_first_boot 109 >/dev/null\n")
    bash_rec = json.loads((host.state / "109.json").read_text())
    nullmoth_finish._record_driver(110, {"tag": "v3.0", "sha": SHA, "source": "chosen"}, SimpleNamespace(notes=[]), pve)
    python_rec = json.loads((host.state / "110.json").read_text())
    python_rec["vmid"] = 109
    assert timed(bash_rec) and timed(python_rec)
    assert shape(bash_rec) == shape(python_rec)
    comp = bash_rec["components"][0]
    assert comp["sha256"] == SHA and comp["source"] == "chosen" and comp["tag"] == "v3.0" and bash_rec["fingerprint"]


@needs_bash4
def test_the_intel_record_of_a_bash_finish_is_the_python_record(host, monkeypatch):
    pve = disks(host)
    igpu = Path(host.env["IGPU_STATE_DIR"])
    igpu.mkdir(parents=True, exist_ok=True)
    state = {"whateverxe_version": "0.8.3", "whateverxe_tag": "v0.8.3", "whateverxe_sha256": SHA, "whateverxe_source": "latest",
             "graphics_version": "0.4.4", "graphics_tag": "v0.4.4", "graphics_sha256": "cd" * 32, "graphics_source": "chosen",
             "amfipass_version": igpu_engine.AMFIPASS_VERSION}
    monkeypatch.setenv("IGPU_STATE_DIR", str(igpu))
    monkeypatch.setenv("DRIVER_STATE_DIR", str(host.state))
    from osx_proxmox_next import igpu_finish
    igpu_engine.write_state(110, state)
    igpu_finish._record_state(110, {"package_installed": False}, SimpleNamespace(notes=[]), pve)
    python_rec = json.loads((host.state / "110.json").read_text())
    python_rec["vmid"] = 109
    igpu_engine.write_state(109, state)
    host.run('driver_record_igpu 109\n')
    bash_rec = json.loads((host.state / "109.json").read_text())
    assert timed(bash_rec) and timed(python_rec) and bash_rec["fingerprint"]
    assert shape(bash_rec) == shape(python_rec)
    assert [c["sha256"] for c in bash_rec["components"]][:2] == [SHA, "cd" * 32]


@needs_bash4
def test_the_amd_record_of_a_bash_attach_has_a_time_like_the_python_one(host):
    host.run('driver_oc_kext() { echo \'{"Lilu.kext": "1.7.2", "WhateverGreen.kext": "1.7.0"}\'; }\ndriver_record_amd 109\n')
    rec = json.loads((host.state / "109.json").read_text())
    assert timed(rec) and [(c["name"], c["tag"], c["sha256"], c["source"]) for c in rec["components"]] == [
        ("Lilu", "1.7.2", "", "disk"), ("WhateverGreen", "1.7.0", "", "disk")]


# ── the record after an update ───────────────────────────────────────────────

def python_update_record(host, kind, target, installed, extra=None, previous=(), pve=None):
    """driver_run._record for VM 109 (the same Python function that writes the record after an update)."""
    pve = pve or RecordPve(mac())
    u = SimpleNamespace(
        vmid=109, env=SimpleNamespace(base=str(host.state)), pve=pve, live_cfg=lambda: mac(),
        result=SimpleNamespace(installed=dict(extra or {}), kept=[]),
        plan=SimpleNamespace(kind=kind, installed=installed, target=Target(kind, target)),
        state={"previous": [{"name": n, "version": v} for n, v in previous], "snapshot": "osx-next-driver-20261121-1030", "card": "0000:01:00,pcie=0"})
    driver_run._record(u)
    return json.loads((host.state / "109.json").read_text())


@needs_bash4
def test_the_nvidia_record_of_a_bash_update_is_the_python_record(host):
    pve = disks(host)
    record(host, "nvidia", NullMoth="v2.8.1")
    resolved = {"NullMoth": {"version": "v2.9", "tag": "v2.9", "sha256": "", "source": "latest"}}
    tarball = {"NullMoth": dict(resolved["NullMoth"], sha256=SHA)}                  # driver_nvidia._assets stores the verified tarball hash
    python_rec = python_update_record(host, "nvidia", tarball, {"NullMoth": "v2.8.1"}, previous=[("NullMoth", "v2.8.1")], pve=pve)
    (host.state / "109.json").unlink()
    record(host, "nvidia", NullMoth="v2.8.1")
    stubs = ('nullmoth_engine() { case "$1" in resolve|fetch) echo \'{"tag": "v2.9", "path": "/c/x.tgz", "sha256": "' + SHA
             + '", "digest": "", "source": "latest"}\' ;; esac; }\nnullmoth_fetch_amfipass() { echo "$TMPDIR/amfi.zip"; }\n'
             "download_recovery() { return 0; }\ngpu_record_load() { return 1; }\nnullmoth_detect_macos() { echo sequoia; }\n")
    host.run(stubs + 'driver_target nvidia "" "" ""\ndriver_prefetch_nvidia 109 sequoia v2.9\n'
             "driver_write_record 109 nvidia '{\"NullMoth\": \"v2.8.1\"}' osx-next-driver-20261121-1030 '0000:01:00,pcie=0'\n")
    bash_rec = json.loads((host.state / "109.json").read_text())
    assert timed(bash_rec) and timed(python_rec)
    assert shape(bash_rec) == shape(python_rec)
    assert bash_rec["components"][0]["sha256"] == SHA and bash_rec["previous"][0]["installed_at"]


@needs_bash4
def test_the_intel_record_of_a_bash_update_has_the_amfipass_the_python_one_has(host):
    pve = disks(host)
    amfipass = {"AMFIPass": {"version": igpu_engine.AMFIPASS_VERSION, "tag": igpu_engine.AMFIPASS_VERSION,
                             "sha256": igpu_engine.PINS["amfipass"][2], "source": "pinned"}}
    python_rec = python_update_record(host, "intel", INTEL, {"WhateverXe": "0.8.0"}, extra=amfipass, pve=pve)
    names = [c["name"] for c in python_rec["components"]]
    assert names == ["WhateverXe", "WhateverXe Graphics", "AMFIPass"] or names == ["AMFIPass", "WhateverXe", "WhateverXe Graphics"]
    (host.state / "109.json").unlink()
    body = BASH[BASH.index("function driver_run_intel()"):]
    body = body[:body.index("\n}\n") + 3]
    quiet = "".join(f"{name}() {{ return 0; }}\n" for name in (
        "driver_prefetch_intel", "driver_step_stop", "driver_step_snapshot", "driver_step_takeoff", "driver_begin", "driver_end",
        "driver_need_stopped", "driver_need_card_off", "igpu_drop_disk", "igpu_drop_disk_ref", "driver_igpu_oc_patch", "igpu_stage",
        "restore_picker_timeout", "driver_step_putback", "driver_watch_first_boot", "igpu_wait_stopped", "igpu_confirm_accel"))
    stubs = (quiet + 'igpu_import_disk() { echo "local-lvm:vm-109-disk-5"; }\nigpu_engine() { case "$1" in result) echo installed ;; '
             "pins) cat <<'J'\n" + json.dumps({"pins": {"amfipass": list(igpu_engine.PINS["amfipass"])}}) + "\nJ\n;; settings) echo '{\"args_add\": []}' ;; strip-args) cat >/dev/null; echo ;; esac; }\n"
             "pve_vol_path() { echo /dev/null; }\ndriver_engine() { case \"$1\" in update-set) ;; *) driver_engine_real \"$@\" ;; esac; }\n")
    host.run("driver_engine_real() { gpu_engine_init && python3 \"$GPU_ENGINE_DIR/driver_engine.py\" \"$@\"; }\n" + stubs + body
             + "\nDRIVER_TARGET=$(cat <<'J'\n" + json.dumps(INTEL) + "\nJ\n)\nDRIVER_VERSIONS='{}'; DRIVER_PAYLOAD=/dev/null; DRIVER_RECOVERY=/dev/null\n"
             "driver_run_intel 109 \"$(qm config 109)\" sequoia >/dev/null\n"
             "driver_write_record 109 intel '{\"WhateverXe\": \"0.8.0\"}' osx-next-driver-20261121-1030 '0000:01:00,pcie=0'\n")
    bash_rec = json.loads((host.state / "109.json").read_text())
    amfi = [c for c in bash_rec["components"] if c["name"] == "AMFIPass"]
    assert amfi and (amfi[0]["version"], amfi[0]["sha256"], amfi[0]["source"]) == ("1.4.1-Mod", igpu_engine.PINS["amfipass"][2], "pinned")
    assert sorted(c["name"] for c in bash_rec["components"]) == sorted(names)
