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
  config) cat "$QM_DIR/config" ;;
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
  start) echo running > "$QM_DIR/status" ;;
  listsnapshot) { while read -r n; do echo "\\`-> $n  2026-11-20 10:00:00  before driver update to v2"; done < "$QM_DIR/snaps"; echo "\\`-> current  You are here!"; } 2>/dev/null ;;
  snapshot) echo "$3" >> "$QM_DIR/snaps" ;;
  delsnapshot) grep -vx "$3" "$QM_DIR/snaps" > "$QM_DIR/snaps.new"; mv "$QM_DIR/snaps.new" "$QM_DIR/snaps" ;;
esac
exit 0
"""


@pytest.fixture
def host(tmp_path):
    qm_dir = tmp_path / "qm"
    qm_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("qm", QM), ("pvesm", "#!/bin/sh\necho 'Name Type Status'\necho 'local-lvm lvmthin active'\n")):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755 | stat.S_IXUSR)
    (qm_dir / "snaps").write_text("")
    cache = tmp_path / "cache"
    cache.mkdir()
    state = tmp_path / "drivers"
    env = {"PATH": f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path), "TEMP_DIR": str(tmp_path),
           "GPU_STATE_DIR": str(tmp_path / "gpu"), "DRIVER_STATE_DIR": str(state), "IGPU_STATE_DIR": str(tmp_path / "igpu"),
           "QM_DIR": str(qm_dir), "QM_LOG": str(tmp_path / "qm.log"), "IGPU_CACHE": str(cache), "PYTHONPATH": str(REPO / "src"),
           "IGPU_DEVICES_OFFLINE": "1", "IGPU_DEVICES_CACHE": str(tmp_path / "dc")}
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


def py_lines(kind, installed, target, snapshot, ok=True):
    plan = DriverPlan(109, "macos-sequoia", kind, "sequoia", False, installed, True, Target(kind, target), "update", snapshot=snapshot,
                      snapshot_ok=ok)
    return plan.lines()


NV = {"NullMoth": {"version": "v2.9", "tag": "v2.9", "sha256": "", "source": "latest"}}
STUB_NULLMOTH = ("nullmoth_engine() { echo '{\"tag\": \"v2.9\", \"digest\": \"\", \"source\": \"latest\"}'; }\n"
                 "nullmoth_detect_macos() { echo sequoia; }\ngpu_record_load() { return 1; }\n")


# ── the embedded copies and the shared pins ──────────────────────────

def test_the_embedded_engines_are_the_files():
    for label, var, source in (("driver_engine.py", "GPU_EMBED_DRIVER_ENGINE", "driver_engine.py"),
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
               f"driver_print_plan 109 macos-sequoia nvidia '{{\"NullMoth\": \"v2.8.1\"}}' \"$DRIVER_TARGET\" {snap} yes\n")
    out = host.run(snippet).stdout.splitlines()
    assert out == py_lines("nvidia", {"NullMoth": "v2.8.1"}, NV, snap)
    out = host.run(snippet.replace(f"{snap} yes", f"{snap} no")).stdout.splitlines()
    assert out == py_lines("nvidia", {"NullMoth": "v2.8.1"}, NV, snap, ok=False)


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
    (host.state / "109.lock").write_text("1\n")
    assert "Refused: " + engine.REFUSE_LOCKED in host.run(STUB_NULLMOTH + "driver_update_command 109 --dry-run\n", expect=2).stdout
    (host.state / "109.lock").unlink()
    (host.dir / "gpu").mkdir(exist_ok=True)
    (host.dir / "gpu" / "gpu-setup.json").write_text('{"vm": 109, "gpu": {"bdf": "x"}, "finished": false}')
    assert "Refused: " + engine.REFUSE_CARD_SETUP in host.run(STUB_NULLMOTH + "driver_update_command 109 --dry-run\n", expect=2).stdout
    (host.dir / "gpu" / "gpu-setup.json").write_text('{"vm": 109, "gpu": {"bdf": "x"}, "finished": true}')
    assert "Refused: " + engine.REFUSE_CARD_SETUP not in host.run(STUB_NULLMOTH + "driver_update_command 109 --dry-run\n", expect=None).stdout


@needs_bash4
def test_bash_status_matches_the_python_status(host, capsys, monkeypatch):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    out = host.run(STUB_NULLMOTH + "driver_status_command 109\n").stdout.splitlines()
    assert out[0] == "VM 109 macos-sequoia: NVIDIA card" and out[1] == "Installed: NullMoth v2.8.1" and out[2] == "Safety snapshots: none"
    (host.qm_dir / "snaps").write_text("osx-next-driver-20261120-0900\n")
    data = json.loads(host.run(STUB_NULLMOTH + "driver_status_command 109 --json\n").stdout)
    assert data == {"vmid": 109, "kind": "nvidia", "installed": {"NullMoth": "v2.8.1"}, "snapshots": ["osx-next-driver-20261120-0900"]}
    (host.state / "109.json").unlink()
    out = host.run(STUB_NULLMOTH + "gpu_record_load() { REC_VENDOR=nvidia; return 0; }\ndriver_status_command 109\n").stdout
    assert "Installed: installed version unknown" in out


@needs_bash4
def test_the_bash_record_is_the_python_record(host):
    out = host.run(STUB_NULLMOTH + "DRIVER_TARGET=$(cat <<'J'\n" + json.dumps(NV) + "\nJ\n)\n"
                   "driver_write_record 109 nvidia '{\"NullMoth\": \"v2.8.1\"}' ''\n").stdout
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


@needs_bash4
def test_a_bash_amd_update_replaces_the_kexts_with_a_snapshot_and_records_it(host):
    write_record(host, "amd", Lilu="1.6.8", WhateverGreen="1.7.0")
    (host.state / "109.json").write_text(json.dumps({"vmid": 109, "kind": "amd", "components": comps(Lilu="1.6.8", WhateverGreen="1.7.0")}))
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
               f'driver_target amd "" "" ""\ndriver_apply 109 amd "$cfg" sequoia \'{{"Lilu": "1.6.8", "WhateverGreen": "1.7.0"}}\' {SNAP} yes ""\n')
    out = host.run(snippet).stdout
    assert "Done. The driver of VM 109 is updated." in out and f"Safety snapshot {SNAP} is kept" in out
    calls = host.calls()
    assert [c for c in calls if c.split()[0] in ("shutdown", "snapshot", "delsnapshot", "start", "stop")] == [
        f"snapshot 109 {SNAP} --description before driver update to 1.7.2 1.7.0",
        "start 109", "delsnapshot 109 osx-next-driver-20261101-0900"]
    oc = (host.qm_dir / "oc.log").read_text().splitlines()
    assert oc[0].startswith("oc_kext 109 replace Lilu.kext ") and oc[1].startswith("oc_kext 109 replace WhateverGreen.kext ")
    rec = engine.read_record(109, str(host.state))
    assert engine.installed_versions(rec) == {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"} and rec["previous"][0]["version"] in ("1.6.8", "1.7.0")
    assert not (host.state / "109.lock").exists()


@needs_bash4
def test_a_bash_amd_update_refuses_a_download_that_does_not_match_its_pin(host):
    (host.cache / "Lilu.zip").write_bytes(b"tampered")
    snippet = ('OC_COMPONENT_URLS=([Lilu]="https://x/releases/download/1.7.2/Lilu.zip" [WhateverGreen]="https://x/releases/download/1.7.0/WG.zip")\n'
               'OC_COMPONENT_SHA256=([Lilu]="' + "0" * 64 + '" [WhateverGreen]="' + "1" * 64 + '")\n'
               'curl() { echo "curl $*" >> "$QM_DIR/curl.log"; return 0; }\n'
               'DRIVER_WANT_SNAPSHOT=""\ndriver_run_amd 109\n')
    res = host.run(snippet, expect=1)
    assert "checksum mismatch, refused" in res.stdout and not any(c.split()[0] in ("shutdown", "stop", "start", "snapshot") for c in host.calls())


@needs_bash4
def test_a_bash_nvidia_update_takes_the_card_off_and_puts_it_back_as_it_was(host):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    stubs = (STUB_NULLMOTH + 'nullmoth_stage() { echo "stage $*" >> "$QM_DIR/stage.log"; }\n'
             'nullmoth_first_boot() { echo "boot $*" >> "$QM_DIR/stage.log"; }\n'
             "DRIVER_VERSION_TEXT=v2.9\n")
    snippet = (stubs + "DRIVER_TARGET=$(cat <<'J'\n" + json.dumps(NV) + "\nJ\n)\ncfg=$(qm config 109)\n"
               f"DRIVER_VERSIONS='{{\"NullMoth\": \"v2.9\"}}'\ndriver_apply 109 nvidia \"$cfg\" sequoia '{{\"NullMoth\": \"v2.8.1\"}}' {SNAP} yes v2.9\n")
    out = host.run(snippet).stdout
    assert "Done. The driver of VM 109 is updated." in out
    sets = [c for c in host.calls() if c.startswith("set 109")]
    assert sets[0] == "set 109 --delete hostpci0"
    assert "set 109 --hostpci0 0000:01:00,pcie=0" in sets and "set 109 --vga none" in sets and "set 109 --boot order=ide0;virtio0" in sets
    assert any(c.startswith("set 109 --args ") and "isa-applesmc" in c for c in sets)
    assert (host.qm_dir / "stage.log").read_text().splitlines() == ["stage 109 v2.9 sequoia", "boot 109"]
    assert engine.read_record(109, str(host.state))["components"][0]["version"] == "v2.9"
    assert not (host.state / "109.lock").exists() and engine.read_update(109, str(host.state)) == {}


@needs_bash4
def test_a_bash_update_that_fails_puts_the_card_back_and_names_the_snapshot(host):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    stubs = STUB_NULLMOTH + "nullmoth_stage() { return 1; }\nDRIVER_VERSION_TEXT=v2.9\n"
    snippet = (stubs + "DRIVER_TARGET=$(cat <<'J'\n" + json.dumps(NV) + "\nJ\n)\ncfg=$(qm config 109)\n"
               f"driver_apply 109 nvidia \"$cfg\" sequoia '{{}}' {SNAP} yes v2.9\n")
    res = host.run(snippet, expect=1)
    assert f"The update did not finish. The Mac is as it was. driver-undo puts it back to the snapshot ({SNAP})." in res.stdout
    assert "set 109 --hostpci0 0000:01:00,pcie=0" in host.calls() and not (host.state / "109.lock").exists()
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
    assert f"goes back to {SNAP}" in out and f"Done. VM 109 is back to {SNAP}." in out
    calls = [c for c in host.calls() if c.split()[0] in ("shutdown", "rollback", "start")]
    assert calls == ["shutdown 109 --timeout 120", f"rollback 109 {SNAP}", "start 109"]
    assert engine.installed_versions(engine.read_record(109, str(host.state))) == {"NullMoth": "v2.8.1"}
    (host.qm_dir / "snaps").write_text("")
    assert "nothing to put back" in host.run("driver_undo_command 109 --yes\n", expect=2).stdout
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
