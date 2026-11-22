"""Python/bash parity for "update the driver": the record file, the plan lines, the refusal texts and the commands."""
from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from driver_fakes import comps, mac
from osx_proxmox_next import cli, driver_engine as engine, oc_builder
from osx_proxmox_next.driver_plan import DriverPlan
from osx_proxmox_next.driver_targets import Target

REPO = Path(__file__).resolve().parent.parent
BASH = (REPO / "scripts/bash/osx-proxmox-next.sh").read_text()
BLOCK = BASH[BASH.index("# ── Vendor-neutral GPU helpers begin ──"):BASH.index("# ── GPU walkthrough end ──")]
DRIVER = BASH[BASH.index("# ── Driver update (BETA) begin ──"):BASH.index("# ── Driver update (BETA) end ──")]
STUBS = ("BOLD='';CL='';CROSS='';INFO='';YW='';RD='';GN='';BFR='';CM='';TAB='';HOLD='';"
         "msg_ok(){ echo \"OK: $1\"; }; msg_error(){ echo \"ERR: $1\"; }; msg_info(){ echo \"INFO: $1\"; };\n")
BASH_MAJOR = int(subprocess.run(["bash", "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True).stdout.strip() or 0)
needs_bash4 = pytest.mark.skipif(BASH_MAJOR < 4, reason="the installer needs bash 4 or newer (CI runs it on Linux)")

QM = """#!/bin/bash
echo "qm $*" >> "$QM_LOG"
st() { cat "$QM_DIR/status" 2>/dev/null || echo stopped; }
case "$1" in
  config) if [ "${3:-}" = "--snapshot" ] && [ -f "$QM_DIR/snapconfig" ]; then cat "$QM_DIR/snapconfig"; else cat "$QM_DIR/config"; fi ;;
  set) shift 2
       while [ $# -gt 0 ]; do
         case "$1" in
           --delete) grep -v "^$2:" "$QM_DIR/config" > "$QM_DIR/config.new"; mv "$QM_DIR/config.new" "$QM_DIR/config"; shift 2 ;;
           --*) k="${1#--}"; grep -v "^$k:" "$QM_DIR/config" > "$QM_DIR/config.new"; echo "$k: $2" >> "$QM_DIR/config.new"; mv "$QM_DIR/config.new" "$QM_DIR/config"; shift 2 ;;
           *) shift ;;
         esac
       done ;;
  status) echo "status: $(st)" ;;
  shutdown|stop) echo stopped > "$QM_DIR/status" ;;
  rollback) [ -f "$QM_DIR/rollback_fails" ] && exit 1 ;;
  start) echo running > "$QM_DIR/status" ;;
  listsnapshot) { while read -r n; do echo "\\`-> $n  2026-11-20 10:00:00  before driver update to v2"; done < "$QM_DIR/snaps"; echo "\\`-> current  You are here!"; } 2>/dev/null ;;
  snapshot) echo "$3" >> "$QM_DIR/snaps" ;;
  delsnapshot) grep -vx "$3" "$QM_DIR/snaps" > "$QM_DIR/snaps.new"; mv "$QM_DIR/snaps.new" "$QM_DIR/snaps" ;;
esac
exit 0
"""


PVESM = """#!/bin/sh
case "$1" in
  path) echo "/dev/pve/${2#*:}" ;;
  *) echo 'Name Type Status'; echo 'local-lvm lvmthin active' ;;
esac
"""
# what udev and kpartx leave on a Mac's logical volumes: $QM_DIR/maps lists the partition maps, `stuck` makes them stay,
# loops_<lv> lists loop devices on a volume, uuids lists "<path> <lv uuid>"
DMSETUP = """#!/bin/sh
echo "dmsetup $*" >> "$QM_DIR/dm.log"
case "$1" in
  ls) if [ -s "$QM_DIR/maps" ]; then cat "$QM_DIR/maps"; else echo "No devices found"; fi ;;
  remove) [ -f "$QM_DIR/stuck" ] && exit 1; grep -v "^$2	" "$QM_DIR/maps" > "$QM_DIR/maps.new"; mv "$QM_DIR/maps.new" "$QM_DIR/maps" ;;
esac
exit 0
"""
LOSETUP = """#!/bin/sh
echo "losetup $*" >> "$QM_DIR/dm.log"
case "$1" in
  -j) f="$QM_DIR/loops_$(basename "$2")"; [ -f "$f" ] && cat "$f"; exit 0 ;;
esac
exit 0
"""
LVS = """#!/bin/sh
for last; do :; done
[ -f "$QM_DIR/uuids" ] && grep "^$last " "$QM_DIR/uuids" | sed 's/^[^ ]* /  /' | grep . && exit 0
exit 5
"""
KPARTX = """#!/bin/sh
echo "kpartx $*" >> "$QM_DIR/dm.log"
exit 0
"""


@pytest.fixture
def host(tmp_path):
    qm_dir = tmp_path / "qm"
    qm_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("qm", QM), ("pvesm", PVESM), ("dmsetup", DMSETUP), ("losetup", LOSETUP), ("lvs", LVS), ("kpartx", KPARTX), ("lvchange", "#!/bin/sh\nexit 0\n")):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755 | stat.S_IXUSR)
    (qm_dir / "snaps").write_text("")
    cache = tmp_path / "cache"
    cache.mkdir()
    state = tmp_path / "drivers"
    env = {"PATH": f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path), "TEMP_DIR": str(tmp_path),
           "GPU_STATE_DIR": str(tmp_path / "gpu"), "DRIVER_STATE_DIR": str(state), "IGPU_STATE_DIR": str(tmp_path / "igpu"),
           "QM_DIR": str(qm_dir), "QM_LOG": str(tmp_path / "qm.log"), "IGPU_CACHE": str(cache), "PYTHONPATH": str(REPO / "src"),
           "SYSFS_ROOT": str(tmp_path / "no-sysfs"), "IGPU_DEVICES_OFFLINE": "1", "IGPU_DEVICES_CACHE": str(tmp_path / "dc")}
    body = STUBS + "declare -A OC_COMPONENT_URLS OC_COMPONENT_SHA256\n" + BLOCK
    script = tmp_path / "block.sh"
    script.write_text(body)

    def config(**over):
        cfg = mac(**over)
        (qm_dir / "config").write_text("\n".join(f"{k}: {v}" for k, v in cfg.items()) + "\n")

    def run(snippet, expect=0):
        res = subprocess.run(["bash", "-c", f"set -u; source {script}\n{snippet}"], capture_output=True, text=True, env=env, timeout=120)
        if expect is not None:
            assert res.returncode == expect, (res.returncode, res.stdout, res.stderr)
        return res

    def calls():
        return [l[3:] for l in (tmp_path / "qm.log").read_text().splitlines()] if (tmp_path / "qm.log").exists() else []

    config()
    return type("Host", (), {"run": staticmethod(run), "config": staticmethod(config), "calls": staticmethod(calls), "dir": tmp_path,
                             "state": state, "qm_dir": qm_dir, "cache": cache, "env": env})


def write_record(host, kind, **versions):
    host.state.mkdir(parents=True, exist_ok=True)
    (host.state / "109.json").write_text(json.dumps({"vmid": 109, "kind": kind, "components": comps(**versions)}))


def py_lines(kind, installed, target, snapshot, ok=True, older=(), running=False):
    plan = DriverPlan(109, "macos-sequoia", kind, "sequoia", running, installed, True, Target(kind, target), "update", snapshot=snapshot,
                      snapshot_ok=ok, older_snapshots=list(older))
    return plan.lines()


NV = {"NullMoth": {"version": "v2.9", "tag": "v2.9", "sha256": "", "source": "latest"}}
HOLD_LOCK = ('lock="$DRIVER_STATE_DIR/109.lock"; mkdir -p "$DRIVER_STATE_DIR"\n'
             '( exec 9>"$lock"; flock -n 9 && echo 4242 > "$lock" && sleep 20 ) & holder=$!\nsleep 0.5\n')
STUB_NULLMOTH = ("nullmoth_engine() { echo '{\"tag\": \"v2.9\", \"digest\": \"\", \"source\": \"latest\"}'; }\n"
                 "nullmoth_detect_macos() { echo sequoia; }\ngpu_record_load() { return 1; }\n")


# ── the embedded copies and the shared pins ──────────────────────────

def test_the_embedded_engines_are_the_files():
    for label, var, source in (("driver_engine.py", "GPU_EMBED_DRIVER_ENGINE", "driver_engine.py"),
                               ("driver_state.py", "GPU_EMBED_DRIVER_STATE", "driver_state.py"),
                               ("oc_kext.py", "GPU_EMBED_OC_KEXT", "data/gpu/oc_kext.py")):
        m = re.search(rf"# ── GPU embedded: {re.escape(label)} begin ──\nIFS= read -r -d '' {var} <<'{var}_EOF' \|\| true\n(.*?){var}_EOF\n", BASH, re.S)
        assert m and m.group(1) == (REPO / "src/osx_proxmox_next" / source).read_text()


def test_the_amd_pins_in_bash_are_the_ones_python_pins():
    for name in ("Lilu", "WhateverGreen"):
        url = re.search(rf'\["{name}"\]="\$OC_ACID/(.*?)"', BASH).group(1)
        assert url.endswith(oc_builder.OC_COMPONENTS[name].url.split("/acidanthera/")[1])
        sha = re.search(rf'\["{name}"\]="([0-9a-f]{{64}})"', BASH.split("OC_COMPONENT_SHA256=(")[1]).group(1)
        assert sha == oc_builder.OC_COMPONENTS[name].sha256


def test_the_bash_commands_are_wired_and_documented_in_the_dispatch():
    for cmd, fn in (("driver-status", "driver_status_command"), ("driver-update", "driver_update_command"), ("driver-undo", "driver_undo_command")):
        assert f'[ "${{1:-}}" == "{cmd}" ]' in BASH and fn in BASH
    assert "driver_record_amd" in BASH and "driver_record_component" in BASH and "from-igpu" in BASH
    assert subprocess.run(["bash", "-n", str(REPO / "scripts/bash/osx-proxmox-next.sh")]).returncode == 0
    p = cli.build_parser()
    assert p.parse_args(["driver-update", "--vmid", "109", "--version", "v2.9", "--dry-run"]).version == "v2.9"


# ── the same plan, the same texts ────────────────────────────────────

@needs_bash4
def test_the_bash_plan_lines_are_the_python_plan_lines(host):
    snap = "osx-next-driver-20261121-1030"
    snippet = (STUB_NULLMOTH + "DRIVER_VERSIONS='{\"NullMoth\": \"v2.9\"}'\nDRIVER_TARGET=$(cat <<'J'\n" + json.dumps(NV) + "\nJ\n)\n"
               f"driver_print_plan 109 macos-sequoia nvidia '{{\"NullMoth\": \"v2.8.1\"}}' \"$DRIVER_TARGET\" {snap} yes no\n")
    out = host.run(snippet).stdout.splitlines()
    assert out == py_lines("nvidia", {"NullMoth": "v2.8.1"}, NV, snap)
    assert not any("Stop the Mac" in l for l in out)                      # a stopped Mac has nothing to stop
    running = host.run(snippet.replace(f"{snap} yes no", f"{snap} yes yes")).stdout.splitlines()
    assert running == py_lines("nvidia", {"NullMoth": "v2.8.1"}, NV, snap, running=True) and "  1. Stop the Mac" in running
    out = host.run(snippet.replace(f"{snap} yes no", f"{snap} no no")).stdout.splitlines()
    assert out == py_lines("nvidia", {"NullMoth": "v2.8.1"}, NV, snap, ok=False)
    older = "osx-next-driver-20261101-0900"
    (host.qm_dir / "snaps").write_text(older + "\n")
    out = host.run(snippet).stdout.splitlines()
    assert out == py_lines("nvidia", {"NullMoth": "v2.8.1"}, NV, snap, older=[older])
    assert out[-1] == f"The previous driver snapshot {older} is removed after a successful update."


@needs_bash4
def test_a_bash_dry_run_prints_the_python_plan(host):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    out = host.run(STUB_NULLMOTH + "driver_update_command 109 --dry-run\n").stdout.splitlines()
    snapshot = next(l for l in out if l.startswith("A snapshot called ")).split()[3]
    assert out[:-1] == py_lines("nvidia", {"NullMoth": "v2.8.1"}, NV, snapshot) and out[-1] == "DRY RUN - nothing is changed."
    assert host.calls() == [c for c in host.calls() if c.split()[0] in ("config", "status", "listsnapshot")]   # nothing changed


@needs_bash4
def test_the_bash_refusal_texts_are_the_engine_texts(host):
    host.config(hostpci0=None)
    assert host.run("driver_status_command 109\n", expect=2).stdout == "REFUSED: " + engine.REFUSE_NO_CARD + "\n"
    out = host.run("driver_update_command 109 --dry-run\n", expect=2).stdout
    assert "Refused: " + engine.REFUSE_NO_CARD in out
    host.config(args="-smbios x")
    assert "Refused: " + engine.REFUSE_NOT_MAC % 109 in host.run("driver_update_command 109 --dry-run\n", expect=2).stdout
    host.config(ide2="local:iso/rec.img,media=disk")
    assert "Refused: " + engine.REFUSE_INSTALLING in host.run(STUB_NULLMOTH + "driver_update_command 109 --dry-run\n", expect=2).stdout
    host.config()
    host.state.mkdir(parents=True, exist_ok=True)
    held = host.run(HOLD_LOCK + STUB_NULLMOTH + "driver_update_command 109 --dry-run; rc=$?; kill $holder; exit $rc\n", expect=2).stdout
    assert "Refused: " + engine.REFUSE_LOCKED in held
    (host.state / "109.lock").write_text("%d\n" % 1)                       # a lock file nobody holds (a stale pid) is free
    assert "Refused: " + engine.REFUSE_LOCKED not in host.run(STUB_NULLMOTH + "driver_update_command 109 --dry-run\n", expect=None).stdout
    (host.dir / "gpu").mkdir(exist_ok=True)
    (host.dir / "gpu" / "gpu-setup.json").write_text('{"vm": 109, "gpu": {"bdf": "x"}, "finished": false}')
    assert "Refused: " + engine.REFUSE_CARD_SETUP in host.run(STUB_NULLMOTH + "driver_update_command 109 --dry-run\n", expect=2).stdout
    (host.dir / "gpu" / "gpu-setup.json").write_text('{"vm": 109, "gpu": {"bdf": "x"}, "finished": true}')
    assert "Refused: " + engine.REFUSE_CARD_SETUP not in host.run(STUB_NULLMOTH + "driver_update_command 109 --dry-run\n", expect=None).stdout


@needs_bash4
def test_bash_status_matches_the_python_status(host, capsys, monkeypatch):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    out = host.run(STUB_NULLMOTH + "driver_status_command 109\n").stdout.splitlines()
    assert out == ["VM 109 macos-sequoia: NVIDIA card", "Installed: NullMoth v2.8.1", "Latest:    NullMoth v2.9",
                   "A newer driver is available.", "Driver snapshots: none"]                       # the lines the Python status prints
    (host.qm_dir / "snaps").write_text("osx-next-driver-20261120-0900\n")
    data = json.loads(host.run(STUB_NULLMOTH + "driver_status_command 109 --json\n").stdout)
    assert data == {"vmid": 109, "kind": "nvidia", "installed": {"NullMoth": "v2.8.1"}, "latest": {"NullMoth": "v2.9"}, "state": "update",
                    "snapshots": ["osx-next-driver-20261120-0900"], "unfinished": False, "updating": False, "step": ""}
    (host.state / "109.json").unlink()
    out = host.run(STUB_NULLMOTH + "gpu_record_load() { REC_VENDOR=nvidia; return 0; }\ndriver_status_command 109\n").stdout
    assert "Installed: installed version unknown" in out and "The installed version is not known." in out
    write_record(host, "nvidia", NullMoth="v2.9")
    assert "Up to date." in host.run(STUB_NULLMOTH + "driver_status_command 109\n").stdout


@needs_bash4
def test_the_bash_record_is_the_python_record(host):
    out = host.run(STUB_NULLMOTH + "DRIVER_TARGET=$(cat <<'J'\n" + json.dumps(NV) + "\nJ\n)\n"
                   "driver_write_record 109 nvidia '{\"NullMoth\": \"v2.8.1\"}' '' ''\n").stdout
    assert out == ""
    rec = json.loads((host.state / "109.json").read_text())
    assert rec["kind"] == "nvidia" and rec["previous"][0]["version"] == "v2.8.1"
    assert set(rec["components"][0]) == set(engine.component("x", "1")) and rec["components"][0]["name"] == "NullMoth"
    assert engine.read_record(109, str(host.state)) == rec                      # Python reads what bash wrote
    engine.write_record(110, "amd", comps(Lilu="1.7.2"), str(host.state))
    assert host.run("driver_engine record 110 | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"kind\"])'\n").stdout == "amd\n"
    host.run("driver_record_component 111 nvidia NullMoth v2.9\n")
    assert engine.read_record(111, str(host.state))["components"][0]["tag"] == "v2.9"
    host.run('driver_oc_kext() { echo \'{"Lilu.kext": "1.7.2", "WhateverGreen.kext": ""}\'; }\ndriver_record_amd 112\n')
    assert engine.installed_versions(engine.read_record(112, str(host.state))) == {"Lilu": "1.7.2"}
    host.run('driver_oc_kext() { echo \'{"Lilu.kext": ""}\'; }\ndriver_record_amd 113\n', expect=1)
    assert engine.read_record(113, str(host.state)) == {}


# ── the run ──────────────────────────────────────────────────────────

SNAP = "osx-next-driver-20261121-1030"
NV_STUBS = (STUB_NULLMOTH + 'nullmoth_stage() { echo "stage $* card=$(qm config 109 | grep -q \'^hostpci0:\' && echo on || echo off) status=$(qm status 109 | sed \'s/status: //\')" >> "$QM_DIR/stage.log"; }\n'
            'nullmoth_first_boot() { echo "boot $*" >> "$QM_DIR/stage.log"; }\n'
            'nullmoth_fetch_amfipass() { echo "$TEMP_DIR/amfi.zip"; }\ndownload_recovery() { echo "recovery $*" >> "$QM_DIR/stage.log"; }\n'
            'NULLMOTH_AMFIPASS_URL=https://x/AMFIPass-v1.4.1-RELEASE.zip\nNULLMOTH_AMFIPASS_SHA256=' + "7" * 64 + '\n'
            'driver_oc_kext() { echo "oc_kext $*" >> "$QM_DIR/oc.log"; echo \'{"AMFIPass.kext": "1.4.1"}\'; }\n'
            "DRIVER_VERSION_TEXT=v2.9\n")


@needs_bash4
def test_a_bash_amd_update_replaces_the_kexts_with_a_snapshot_and_records_it(host):
    write_record(host, "amd", Lilu="1.6.8", WhateverGreen="1.6.0")
    zips = {}
    for name in ("Lilu", "WhateverGreen"):
        path = host.cache / f"{name}.zip"
        path.write_bytes(name.encode())
        zips[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (host.qm_dir / "snaps").write_text("osx-next-driver-20261101-0900\n")
    snippet = ('OC_COMPONENT_URLS=([Lilu]="https://x/releases/download/1.7.2/Lilu.zip" [WhateverGreen]="https://x/releases/download/1.7.0/WhateverGreen.zip")\n'
               f'OC_COMPONENT_SHA256=([Lilu]="{zips["Lilu"]}" [WhateverGreen]="{zips["WhateverGreen"]}")\n'
               'driver_oc_kext() { echo "oc_kext $*" >> "$QM_DIR/oc.log"; }\n'
               'DRIVER_VERSION_TEXT="1.7.2, 1.7.0"\n'
               'cfg=$(qm config 109)\n'
               'DRIVER_INSTALLED=\'{"Lilu": "1.6.8", "WhateverGreen": "1.6.0"}\'\n'
               f'driver_target amd "" "" ""\ndriver_apply 109 amd "$cfg" sequoia {SNAP} yes "" no macos-sequoia\n')
    out = host.run(snippet).stdout
    assert "Done. The driver of VM 109 is updated." in out and f"Driver snapshot {SNAP} is kept. driver-undo goes back to it." in out
    assert "Removed the previous driver snapshot: osx-next-driver-20261101-0900" in out
    calls = host.calls()
    assert [c for c in calls if c.split()[0] in ("shutdown", "snapshot", "delsnapshot", "start", "stop")] == [
        f"snapshot 109 {SNAP} --description before driver update to 1.7.2 1.7.0",
        "start 109", "delsnapshot 109 osx-next-driver-20261101-0900"]
    oc = (host.qm_dir / "oc.log").read_text().splitlines()
    assert oc[0].startswith("oc_kext 109 replace Lilu.kext ") and oc[1].startswith("oc_kext 109 replace WhateverGreen.kext ")
    rec = engine.read_record(109, str(host.state))
    assert engine.installed_versions(rec) == {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"} and rec["previous"][0]["version"] in ("1.6.8", "1.6.0")
    assert rec["snapshot"] == SNAP and rec["snapshot_card"] == "0000:01:00,pcie=0"       # what undo checks later
    assert not (host.state / "109.lock").exists() and engine.read_update(109, str(host.state)) == {}


@needs_bash4
def test_a_bash_amd_update_refuses_a_download_that_does_not_match_its_pin(host):
    (host.cache / "Lilu.zip").write_bytes(b"tampered")
    snippet = ('OC_COMPONENT_URLS=([Lilu]="https://x/releases/download/1.7.2/Lilu.zip" [WhateverGreen]="https://x/releases/download/1.7.0/WG.zip")\n'
               'OC_COMPONENT_SHA256=([Lilu]="' + "0" * 64 + '" [WhateverGreen]="' + "1" * 64 + '")\n'
               'curl() { echo "curl $*" >> "$QM_DIR/curl.log"; return 0; }\n'
               'DRIVER_WANT_SNAPSHOT=""\nDRIVER_INSTALLED=\'{}\'\nDRIVER_VERSIONS=\'{"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}\'\ndriver_run_amd 109\n')
    res = host.run(snippet, expect=1)
    assert "checksum mismatch, refused" in res.stdout and not any(c.split()[0] in ("shutdown", "stop", "start", "snapshot") for c in host.calls())


@needs_bash4
def test_a_bash_nvidia_update_takes_the_card_off_and_puts_it_back_as_it_was(host):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    snippet = (NV_STUBS + "DRIVER_TARGET=$(cat <<'J'\n" + json.dumps(NV) + "\nJ\n)\ncfg=$(qm config 109)\n"
               f"DRIVER_VERSIONS='{{\"NullMoth\": \"v2.9\"}}'\nDRIVER_INSTALLED='{{\"NullMoth\": \"v2.8.1\"}}'\n"
               f"driver_apply 109 nvidia \"$cfg\" sequoia {SNAP} yes v2.9 no macos-sequoia\n")
    out = host.run(snippet).stdout
    assert "Done. The driver of VM 109 is updated." in out
    sets = [c for c in host.calls() if c.startswith("set 109")]
    assert sets[0] == "set 109 --delete hostpci0"
    assert "set 109 --hostpci0 0000:01:00,pcie=0" in sets and "set 109 --vga none" in sets and "set 109 --boot order=ide0;virtio0" in sets
    assert any(c.startswith("set 109 --args ") and "isa-applesmc" in c for c in sets)
    log = (host.qm_dir / "stage.log").read_text().splitlines()
    assert log[0].startswith("recovery sequoia ") and log[1:] == ["stage 109 v2.9 sequoia card=off status=stopped", "boot 109"]   # downloads come first
    assert (host.qm_dir / "oc.log").read_text().splitlines() == ["oc_kext 109 versions AMFIPass.kext"]    # the pinned AMFIPass is already there
    assert engine.read_record(109, str(host.state))["components"][0]["version"] == "v2.9"
    assert not (host.state / "109.lock").exists() and engine.read_update(109, str(host.state)) == {}


@needs_bash4
def test_a_bash_update_that_fails_puts_the_card_back_and_names_the_snapshot(host):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    stubs = NV_STUBS + "nullmoth_stage() { return 1; }\n"
    snippet = (stubs + "DRIVER_TARGET=$(cat <<'J'\n" + json.dumps(NV) + "\nJ\n)\ncfg=$(qm config 109)\n"
               f"driver_apply 109 nvidia \"$cfg\" sequoia {SNAP} yes v2.9 no macos-sequoia\n")
    res = host.run(snippet, expect=1)
    assert ("The update did not finish. The driver was only partly staged. The Mac is stopped. Finish continues it "
            f"(osx-proxmox-next.sh driver-update 109 --finish). driver-undo goes back to the snapshot of 2026-11-21 10:30 ({SNAP}).") in res.stdout
    assert "set 109 --hostpci0 0000:01:00,pcie=0" in host.calls() and not (host.state / "109.lock").exists()
    state = engine.read_update(109, str(host.state))        # the same state file the Python twin leaves: steps, phase, what was remembered
    assert state["phase"] == "failed" and state["snapshot"] == SNAP and state["original"]["hostpci0"] == "0000:01:00,pcie=0"
    assert [r["id"] for r in state["steps"]] == [i for i, _t in engine.step_titles("nvidia")]
    assert [r["state"] for r in state["steps"]] == ["done", "done", "done", "fail", "pending", "pending", "pending"]
    assert [r["title"] for r in state["steps"]] == [t for _i, t in engine.step_titles("nvidia", "NullMoth v2.9")]
    assert engine.read_record(109, str(host.state))["components"][0]["version"] == "v2.8.1"      # the record did not move


@needs_bash4
def test_bash_asks_for_the_risk_the_phrase_and_the_confirmation(host):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    out = host.run(STUB_NULLMOTH + "driver_update_command 109 --yes\n", expect=2).stdout
    assert "Nothing was changed. Read the notice above, then re-run with --accept-nullmoth-risk to apply." in out
    assert not any(c.startswith(("shutdown", "snapshot")) for c in host.calls())
    out = host.run(STUB_NULLMOTH + "driver_update_command 109 --accept-nullmoth-risk </dev/null\n", expect=2).stdout
    assert "Nothing was changed." in out
    pvesm = host.dir / "bin" / "pvesm"
    pvesm.write_text("#!/bin/sh\necho 'Name Type Status'\necho 'local-lvm lvm active'\n")
    out = host.run(STUB_NULLMOTH + "driver_update_command 109 --accept-nullmoth-risk --yes </dev/null\n", expect=2).stdout
    assert engine.NO_SNAPSHOT_WARNING in out and "Re-run with --no-snapshot to go on without a snapshot." in out


@needs_bash4
def test_bash_says_up_to_date_and_reinstalls_on_request(host):
    write_record(host, "nvidia", NullMoth="v2.9")
    out = host.run(STUB_NULLMOTH + "driver_update_command 109 --dry-run\n").stdout
    assert out.rstrip().endswith(engine.UP_TO_DATE)
    out = host.run(STUB_NULLMOTH + "driver_update_command 109 --dry-run --reinstall\n").stdout
    assert "NullMoth: v2.9, reinstalled" in out and "DRY RUN" in out


@needs_bash4
def test_bash_undo_rolls_back_to_the_newest_snapshot_and_reverts_the_record(host):
    write_record(host, "nvidia", NullMoth="v2.9")
    rec = json.loads((host.state / "109.json").read_text())
    rec["previous"] = comps(NullMoth="v2.8.1")
    (host.state / "109.json").write_text(json.dumps(rec))
    (host.qm_dir / "snaps").write_text("osx-next-driver-20261101-0900\n" + SNAP + "\n")
    (host.qm_dir / "status").write_text("running\n")
    out = host.run("driver_undo_command 109 --yes\n").stdout
    assert f"its disks go back to the snapshot of 2026-11-21 10:30 ({SNAP})" in out and f"Done. VM 109 is back to {SNAP}." in out
    calls = [c for c in host.calls() if c.split()[0] in ("shutdown", "rollback", "start")]
    assert calls == ["shutdown 109 --timeout 120", f"rollback 109 {SNAP}", "start 109"]
    assert engine.installed_versions(engine.read_record(109, str(host.state))) == {"NullMoth": "v2.8.1"}
    (host.qm_dir / "snaps").write_text("")
    assert "nothing to undo" in host.run("driver_undo_command 109 --yes\n", expect=2).stdout
    (host.qm_dir / "snaps").write_text(SNAP + "\n")
    assert "Nothing was changed." in host.run("driver_undo_command 109 </dev/null\n", expect=2).stdout


@needs_bash4
def test_bash_stops_the_mac_by_force_only_after_the_request_timed_out(host):
    (host.qm_dir / "status").write_text("running\n")
    stubs = ('qm() { echo "qm $*" >> "$QM_LOG"; case "$1" in status) echo "status: running" ;; stop) echo stopped > "$QM_DIR/status" ;; esac; }\n'
             "igpu_wait_stopped() { return 1; }\n")
    res = host.run(stubs + "driver_stop_mac 109 && echo stopped-ok\n", expect=1)
    assert "INFO: Asking the Mac to shut down" in res.stdout and "macOS did not shut down within 2 minutes. Stopping the Mac by force" in res.stdout
    assert "ERR: VM 109 would not stop." in res.stdout
    calls = host.calls()
    assert calls.index("shutdown 109 --timeout 120") < calls.index("stop 109")


# ── Finish, the lock, the undo checks, the AMD pair and the card on the host (the same rules as the Python twin) ──

def nv_apply(extra=""):
    return (NV_STUBS + extra + "DRIVER_TARGET=$(cat <<'J'\n" + json.dumps(NV) + "\nJ\n)\ncfg=$(qm config 109)\n"
            "DRIVER_VERSIONS='{\"NullMoth\": \"v2.9\"}'\nDRIVER_INSTALLED='{\"NullMoth\": \"v2.8.1\"}'\n")


@needs_bash4
def test_bash_finish_after_a_staging_failure_stops_the_mac_and_stages_with_the_card_off(host):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    first = nv_apply("nullmoth_stage() { return 1; }\n") + f"driver_apply 109 nvidia \"$cfg\" sequoia {SNAP} yes v2.9 no macos-sequoia\n"
    host.run(first, expect=1)
    assert engine.read_update(109, str(host.state))["phase"] == "failed"
    (host.qm_dir / "status").write_text("running\n")                       # the user started the Mac meanwhile
    host.run(NV_STUBS + "driver_update_command 109 --finish --yes --accept-nullmoth-risk\n")
    log = (host.qm_dir / "stage.log").read_text().splitlines()
    assert [l for l in log if l.startswith("stage ")] == ["stage 109 v2.9 sequoia card=off status=stopped"]   # never on a running Mac, never with the card
    calls = host.calls()
    assert calls.count(f"snapshot 109 {SNAP} --description before driver update to v2.9") == 1 or len([c for c in calls if c.startswith("snapshot")]) == 1
    assert "shutdown 109 --timeout 120" in calls and "set 109 --hostpci0 0000:01:00,pcie=0" in calls
    assert engine.read_record(109, str(host.state))["components"][0]["version"] == "v2.9" and engine.read_update(109, str(host.state)) == {}


@needs_bash4
def test_bash_finish_after_a_kill_following_the_staging_does_not_stage_again(host):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    host.config(hostpci0=None, vga="std", boot="order=sata1;virtio0;ide0")
    steps = [{"id": i, "title": t, "done": d, "state": st} for (i, t), (_i, d), st in zip(
        engine.step_titles("nvidia", "NullMoth v2.9"), engine.step_titles("nvidia", "NullMoth v2.9", done=True),
        ["done", "done", "done", "done", "pending", "pending", "pending"])]
    original = {"hostpci0": "0000:01:00,pcie=0", "vga": "none", "args": mac()["args"], "boot": "order=ide0;virtio0"}
    engine.write_update(109, {"kind": "nvidia", "phase": "running", "snapshot": SNAP, "original": original, "steps": steps, "card": original["hostpci0"],
                              "choice": {"NullMoth": "v2.9"}, "previous": [], "payload_ref": "local-lvm:vm-109-disk-11", "staged": True}, str(host.state))
    host.run(NV_STUBS + "driver_update_command 109 --finish --yes --accept-nullmoth-risk\n")
    log = (host.qm_dir / "stage.log").read_text().splitlines()
    assert not [l for l in log if l.startswith("stage ")] and "boot 109" in log
    assert "set 109 --hostpci0 0000:01:00,pcie=0" in host.calls() and not [c for c in host.calls() if c.startswith("snapshot")]
    assert host.run("driver_update_command 109 --finish --yes\n", expect=2).stdout.strip() == "ERROR: There is no unfinished driver update for this Mac."


@needs_bash4
def test_bash_takes_the_lock_with_flock_and_undo_goes_through_it_too(host):
    write_record(host, "nvidia", NullMoth="v2.9")
    (host.qm_dir / "snaps").write_text(SNAP + "\n")
    out = host.run(HOLD_LOCK + nv_apply() + f"driver_apply 109 nvidia \"$cfg\" sequoia {SNAP} yes v2.9 no m; rc=$?; kill $holder; exit $rc\n", expect=2).stdout
    assert out.strip() == "Refused: " + engine.REFUSE_LOCKED and not [c for c in host.calls() if c.split()[0] in ("shutdown", "snapshot", "set")]
    out = host.run(HOLD_LOCK + "driver_undo_command 109 --yes; rc=$?; kill $holder; exit $rc\n", expect=2).stdout
    assert "ERROR: " + engine.REFUSE_LOCKED in out and not [c for c in host.calls() if c.split()[0] in ("shutdown", "rollback")]
    host.run("driver_undo_command 109 --yes\n")                             # nobody holds it: the undo runs and frees the lock
    assert "rollback 109 " + SNAP in host.calls() and not (host.state / "109.lock").exists()


@needs_bash4
def test_bash_undo_refuses_before_it_touches_the_mac(host):
    write_record(host, "nvidia", NullMoth="v2.9")
    (host.qm_dir / "snaps").write_text(SNAP + "\nmine\n")
    (host.qm_dir / "status").write_text("running\n")
    # the listing the fake prints holds the tool snapshots only: a newer snapshot of somebody else comes from a real listing
    out = host.run("qm() { if [ \"$1\" = listsnapshot ]; then printf '`-> %s  2026  x\\n `-> mine  2026  y\\n  `-> current  You are here!\\n' " + SNAP
                   + "; else command qm \"$@\"; fi; }\ndriver_undo_command 109 --yes\n", expect=2).stdout
    assert "ERROR: " + engine.REFUSE_NEWER_SNAPSHOTS % "mine" in out
    assert not [c for c in host.calls() if c.split()[0] in ("shutdown", "stop", "rollback")]
    rec = json.loads((host.state / "109.json").read_text())
    rec.update(snapshot=SNAP, snapshot_card="0000:09:00")                   # the card setup is not the one the snapshot was taken with
    (host.state / "109.json").write_text(json.dumps(rec))
    (host.qm_dir / "snaps").write_text(SNAP + "\n")
    assert "ERROR: " + engine.REFUSE_CARD_CHANGED in host.run("driver_undo_command 109 --yes\n", expect=2).stdout
    assert not [c for c in host.calls() if c.split()[0] in ("shutdown", "stop", "rollback")]


@needs_bash4
def test_bash_undo_starts_the_mac_again_when_the_rollback_fails_and_drops_leftover_disks(host):
    write_record(host, "nvidia", NullMoth="v2.9")
    (host.qm_dir / "snaps").write_text(SNAP + "\n")
    (host.qm_dir / "status").write_text("running\n")
    (host.qm_dir / "rollback_fails").write_text("")
    host.run("driver_undo_command 109 --yes\n", expect=1)
    assert host.calls()[-1] == "start 109" and not (host.state / "109.lock").exists()
    (host.qm_dir / "rollback_fails").unlink()
    host.config(sata1="local-lvm:vm-109-disk-20,media=disk", sata2="local-lvm:vm-109-disk-21,media=disk")
    (host.qm_dir / "snapconfig").write_text("\n".join(f"{k}: {v}" for k, v in mac().items()) + "\n")       # the snapshot has neither disk
    before = len(host.calls())
    host.run("driver_undo_command 109 --yes\n")
    calls = host.calls()[before:]
    assert "set 109 --delete sata1" in calls and "set 109 --delete sata2" in calls
    assert calls.index("set 109 --delete sata2") < calls.index("rollback 109 " + SNAP)


@needs_bash4
def test_a_bash_amd_update_replaces_only_the_kext_that_is_behind(host):
    write_record(host, "amd", Lilu="1.7.9", WhateverGreen="1.6.9")
    zips = {}
    for name in ("Lilu", "WhateverGreen"):
        path = host.cache / f"{name}.zip"
        path.write_bytes(name.encode())
        zips[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    snippet = ('OC_COMPONENT_URLS=([Lilu]="https://x/releases/download/1.7.2/Lilu.zip" [WhateverGreen]="https://x/releases/download/1.7.0/WhateverGreen.zip")\n'
               f'OC_COMPONENT_SHA256=([Lilu]="{zips["Lilu"]}" [WhateverGreen]="{zips["WhateverGreen"]}")\n'
               'driver_oc_kext() { echo "oc_kext $*" >> "$QM_DIR/oc.log"; }\n'
               'DRIVER_VERSION_TEXT="1.7.2, 1.7.0"\ncfg=$(qm config 109)\n'
               'DRIVER_INSTALLED=\'{"Lilu": "1.7.9", "WhateverGreen": "1.6.9"}\'\n'
               f'driver_target amd "" "" ""\ndriver_apply 109 amd "$cfg" sequoia {SNAP} yes "" no m\n')
    host.run(snippet)
    oc = (host.qm_dir / "oc.log").read_text().splitlines()
    assert len(oc) == 1 and oc[0].startswith("oc_kext 109 replace WhateverGreen.kext ")       # Lilu is newer than the pin: left alone
    assert engine.installed_versions(engine.read_record(109, str(host.state))) == {"Lilu": "1.7.9", "WhateverGreen": "1.7.0"}


def device(root, bdf, vendor, pci_class):
    folder = root / "bus/pci/devices" / bdf
    folder.mkdir(parents=True)
    (folder / "vendor").write_text(vendor + "\n")
    (folder / "class").write_text(pci_class + "\n")


@needs_bash4
def test_bash_takes_the_kind_from_the_graphics_card_on_the_host(host, tmp_path):
    sysfs = tmp_path / "sys"
    device(sysfs, "0000:00:14.0", "0x8086", "0x0c0330")
    host.config(hostpci0="0000:00:14.0")
    write_record(host, "intel", WhateverXe="1.2")
    env = f"export SYSFS_ROOT={sysfs}\n"
    assert host.run(env + "driver_status_command 109\n", expect=2).stdout == "REFUSED: " + engine.REFUSE_NO_CARD + "\n"      # a USB controller is no card
    device(sysfs, "0000:01:00.0", "0x1002", "0x030000")
    host.config(hostpci0="0000:01:00,pcie=0")
    write_record(host, "nvidia", NullMoth="v2.8.1")                                                                           # the record is for another card
    assert host.run(STUB_NULLMOTH + env + "driver_status_command 109\n", expect=2).stdout == "REFUSED: " + engine.REFUSE_KIND_CHANGED + "\n"
    out = host.run(STUB_NULLMOTH + env + "driver_update_command 109 --dry-run\n", expect=2).stdout
    assert "Refused: " + engine.REFUSE_KIND_CHANGED in out


@needs_bash4
def test_bash_shows_an_update_that_stopped_with_the_card_off(host):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    host.config(hostpci0=None)
    engine.write_update(109, {"kind": "nvidia", "phase": "failed", "snapshot": SNAP, "steps": []}, str(host.state))
    out = host.run(STUB_NULLMOTH + "driver_status_command 109\n").stdout
    assert "REFUSED" not in out and "VM 109 macos-sequoia: NVIDIA card" in out
    assert "The driver update stopped before it finished. Finish it: osx-proxmox-next.sh driver-update 109 --finish --yes" in out
    assert json.loads(host.run(STUB_NULLMOTH + "driver_status_command 109 --json\n").stdout)["unfinished"] is True


@needs_bash4
def test_bash_refuses_a_release_name_that_is_not_one_and_the_oc_disk_of_a_running_mac(host):
    assert "--macos must be one of" in host.run("driver_update_command 109 --dry-run --macos 'x;rm'\n", expect=2).stdout
    (host.qm_dir / "status").write_text("running\n")
    res = host.run("driver_oc_kext 109 versions Lilu.kext\n", expect=1)
    assert res.stderr.strip() == engine.REFUSE_NOT_STOPPED and not [c for c in host.calls() if c.startswith(("losetup",))]
    body = DRIVER[DRIVER.index("function driver_oc_kext()"):DRIVER.index("# ── the lock")]
    assert "umount -l" in body and "EXIT" in body and "INT TERM" in body and "-o ro" in body and "python3 -I -" in body
    assert body.index("trap '") < body.index("losetup -fP")                  # the trap is set before the loop device exists


def test_undoing_the_card_setup_forgets_the_driver_record_in_both_twins():
    from osx_proxmox_next import gpu
    steps = gpu.build_hostpci_detach_steps(109, "", True, record=gpu.new_record(109, "0000:01:00", "nvidia", {}, gpu.NVIDIA_PROFILE,
                                                                               "0000:01:00,pcie=0", vga_none=False))
    assert [s.argv for s in steps][-2:] == [["rm", "-f", str(gpu.record_path(109))], ["rm", "-f", str(engine.record_path(109))]]
    assert ('rm -f "$(gpu_record_path "$vmid")"\n  # the card is gone, so is the record of its driver (a later card may be another kind)\n'
            '  rm -f "${DRIVER_STATE_DIR:-/var/lib/osx-proxmox-next/drivers}/${vmid}.json"') in BASH


# ── fix wave 2: the same rules in bash ───────────────────────────────

def maps(host, *names, stuck=False):
    (host.qm_dir / "maps").write_text("".join(f"{n}\t(253:5)\n" for n in names))
    if stuck:
        (host.qm_dir / "stuck").write_text("1")


def dm_log(host):
    path = host.qm_dir / "dm.log"
    return path.read_text().splitlines() if path.exists() else []


MAP2, MAP1 = "pve-vm--9--disk--2p1", "pve-vm--9--disk--1p1"


@needs_bash4
def test_bash_releases_the_maps_and_loops_of_a_macs_volumes_and_refuses_with_the_exact_command(host):
    maps(host, MAP2, MAP1)
    (host.qm_dir / "loops_vm-9-disk-2").write_text("/dev/loop4\n")
    out = host.run("driver_release_disks 109\n").stdout
    log = dm_log(host)
    assert out == "" and f"dmsetup remove {MAP2}" in log and f"dmsetup remove {MAP1}" in log and "losetup -d /dev/loop4" in log
    assert "kpartx -d /dev/pve/vm-9-disk-2" in log
    maps(host, MAP2, stuck=True)
    out = host.run("driver_release_disks 109\n", expect=1).stdout
    assert out == engine.maps_held_text(109, [MAP2]) + "\n"              # the text and the command are the Python twin's
    assert f"dmsetup remove {MAP2}" in out and "Nothing was changed" in out


@needs_bash4
def test_bash_undo_releases_before_the_rollback_and_a_map_that_stays_stops_it(host):
    write_record(host, "nvidia", NullMoth="v2.9")
    (host.qm_dir / "snaps").write_text(SNAP + "\n")
    (host.qm_dir / "status").write_text("running\n")
    maps(host, MAP2)
    out = host.run("driver_undo_command 109 --yes\n").stdout
    assert f"dmsetup remove {MAP2}" in dm_log(host) and "  Released the disks" in out and "  Put the snapshot back" in out
    assert "Done. VM 109 is back to " + SNAP + "." in out
    assert host.calls().index("rollback 109 " + SNAP) > 0
    host.run("true")
    (host.qm_dir / "snaps").write_text(SNAP + "\n")
    maps(host, MAP2, stuck=True)
    (host.qm_dir / "status").write_text("stopped\n")
    before = len([c for c in host.calls() if c.startswith("rollback")])
    out = host.run("driver_undo_command 109 --yes\n", expect=1).stdout
    assert f"ERROR: {engine.maps_held_text(109, [MAP2])}" in out
    assert len([c for c in host.calls() if c.startswith("rollback")]) == before          # qm rollback never ran: no lock: rollback
    assert not (host.state / "109.lock").exists()


@needs_bash4
def test_bash_names_the_unlock_command_when_proxmox_left_the_rollback_lock(host):
    write_record(host, "nvidia", NullMoth="v2.9")
    (host.qm_dir / "snaps").write_text(SNAP + "\n")
    (host.qm_dir / "rollback_fails").write_text("1")
    with open(host.qm_dir / "config", "a") as f:
        f.write("lock: rollback\n")
    out = host.run("driver_undo_command 109 --yes\n", expect=1).stdout
    assert "The rollback lock is still set, so the Mac stays stopped. Check its disks, then run: qm unlock 109" in out
    assert "start 109" not in host.calls() and not (host.state / "109.lock").exists()


@needs_bash4
def test_bash_drops_a_disk_after_releasing_its_maps_and_strict_mode_stops_when_one_stays(host):
    host.config(sata1="local-lvm:vm-9-disk-2,media=disk")
    maps(host, MAP2)
    host.run("igpu_drop_disk 109 sata1\n")
    assert f"dmsetup remove {MAP2}" in dm_log(host) and "set 109 --delete sata1" in host.calls()
    host.config(sata1="local-lvm:vm-9-disk-2,media=disk")
    maps(host, MAP2, stuck=True)
    assert host.run("igpu_drop_disk 109 sata1\n").stdout == ""                 # best effort: an error is being handled
    host.config(sata1="local-lvm:vm-9-disk-2,media=disk")
    assert host.run("igpu_drop_disk 109 sata1 strict\n", expect=1).stdout == engine.maps_held_text(109, [MAP2]) + "\n"


@needs_bash4
def test_bash_trusts_the_opencore_disk_over_the_record_and_the_disks_over_a_record_of_other_disks(host):
    write_record(host, "amd", Lilu="1.7.2", WhateverGreen="1.7.0")
    disk = 'driver_oc_kext() { echo \'{"Lilu.kext": "1.6.8", "WhateverGreen.kext": "1.6.7"}\'; }\n'
    out = host.run(disk + 'driver_installed 109 amd no\n').stdout
    assert json.loads(out) == {"Lilu": "1.6.8", "WhateverGreen": "1.6.7"}
    out = host.run(disk + 'driver_installed 109 amd yes\n').stdout              # a running Mac's disk is not mounted: the record
    assert json.loads(out) == {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}
    (host.state / "109.json").unlink()
    out = host.run('driver_oc_kext() { echo \'{"WhateverXe.kext": "0.8.0"}\'; }\ndriver_installed 109 intel no\n').stdout
    assert json.loads(out) == {"WhateverXe": "0.8.0"}                           # Intel without a record: WhateverXe from the disk
    write_record(host, "intel", WhateverXe="0.8.3", WhateverXe_Graphics="0.4.4")
    out = host.run('driver_oc_kext() { echo \'{"WhateverXe.kext": "0.8.0"}\'; }\ndriver_installed 109 intel no\n').stdout
    assert json.loads(out) == {"WhateverXe": "0.8.0"}                           # the disk disagrees: the record is dropped whole
    uuids = host.qm_dir / "uuids"
    uuids.write_text("/dev/pve/vm-9-disk-1 AAA\n")
    fp = host.run("driver_fingerprint 109\n").stdout.strip()
    assert fp == engine.volume_fingerprint(["AAA"])
    write_record(host, "nvidia", NullMoth="v3.0")
    rec = json.loads((host.state / "109.json").read_text())
    rec["fingerprint"] = fp
    (host.state / "109.json").write_text(json.dumps(rec))
    assert json.loads(host.run("driver_installed 109 nvidia yes\n").stdout) == {"NullMoth": "v3.0"}
    uuids.write_text("/dev/pve/vm-9-disk-1 ZZZ\n")                              # a rollback made a new volume
    assert json.loads(host.run("driver_installed 109 nvidia yes\n").stdout) == {}


@needs_bash4
def test_bash_status_while_an_update_runs_says_updating_with_the_step(host):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    steps = [{"id": i, "title": t, "done": d, "state": st} for (i, t), (_i, d), st in zip(
        engine.step_titles("nvidia", "NullMoth v2.9"), engine.step_titles("nvidia", "NullMoth v2.9", done=True),
        ["done", "done", "active", "pending", "pending", "pending", "pending"])]
    engine.write_update(109, {"kind": "nvidia", "phase": "running", "steps": steps}, str(host.state))
    out = host.run(HOLD_LOCK + STUB_NULLMOTH + "driver_status_command 109; rc=$?; kill $holder; exit $rc\n").stdout
    assert "Updating: the driver is being updated now (step 3 of 7: Take the card off)." in out
    assert "Installed" not in out and "Latest" not in out and "Up to date" not in out
    data = json.loads(host.run(HOLD_LOCK + STUB_NULLMOTH + "driver_status_command 109 --json; rc=$?; kill $holder; exit $rc\n").stdout)
    assert data["updating"] is True and data["step"] == "3 of 7: Take the card off"


@needs_bash4
def test_a_bash_amd_update_names_the_versions_it_replaced_and_the_one_it_kept(host):
    write_record(host, "amd", Lilu="1.7.9", WhateverGreen="1.6.0")
    zips = {}
    for name in ("Lilu", "WhateverGreen"):
        path = host.cache / f"{name}.zip"
        path.write_bytes(name.encode())
        zips[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    snippet = ('OC_COMPONENT_URLS=([Lilu]="https://x/releases/download/1.7.2/Lilu.zip" [WhateverGreen]="https://x/releases/download/1.7.0/WhateverGreen.zip")\n'
               f'OC_COMPONENT_SHA256=([Lilu]="{zips["Lilu"]}" [WhateverGreen]="{zips["WhateverGreen"]}")\n'
               'driver_oc_kext() { echo "oc_kext $*" >> "$QM_DIR/oc.log"; }\nDRIVER_VERSION_TEXT="1.7.2, 1.7.0"\ncfg=$(qm config 109)\n'
               'DRIVER_INSTALLED=\'{"Lilu": "1.7.9", "WhateverGreen": "1.6.0"}\'\n'
               f'driver_target amd "" "" ""\ndriver_apply 109 amd "$cfg" sequoia {SNAP} yes "" no macos-sequoia\n')
    out = host.run(snippet).stdout
    assert "OK: Replaced WhateverGreen 1.7.0. Lilu 1.7.9 is newer than the pin and was kept" in out     # the Python twin's line
    rec = engine.read_record(109, str(host.state))
    assert rec["latest"] == {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"} and "fingerprint" not in rec      # no LV: nothing to tell
