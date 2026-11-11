"""The bash twin of nullmoth_finish: staging, waiting for the guest's report, the address lookup and the closing report."""
from __future__ import annotations

from test_gpu_parity import bash, block_file, vm  # noqa: F401  (fixtures)

COMMON = r'''
LOGF="$TMPDIR/calls.log"; : >"$LOGF"
sleep() { :; }
msg_error() { echo "ERR: $1"; }
qm() { echo "qm $*" >>"$LOGF"; case "$1" in config) echo "$QM_CONFIG";; status) echo "status: ${QM_STATUS:-stopped}";; esac; return 0; }
igpu_wait_stopped() { echo "wait_stopped $*" >>"$LOGF"; return 0; }
igpu_drop_disk() { echo "drop $*" >>"$LOGF"; }
igpu_drop_disk_ref() { echo "drop_ref $*" >>"$LOGF"; }
igpu_put_back() { echo "put_back $*" >>"$LOGF"; }
igpu_oc_patch() { echo "oc_patch $*" >>"$LOGF"; return 0; }
restore_picker_timeout() { echo "restore_picker $*" >>"$LOGF"; return "${PICKER_RC:-0}"; }
download_recovery() { echo "download_recovery $*" >>"$LOGF"; return 0; }
igpu_import_disk() { echo "import $*" >>"$LOGF"; echo "local-lvm:vm-$1-disk-$RANDOM"; }
igpu_stage() { echo "stage $*" >>"$LOGF"; return "${STAGE_RC:-0}"; }
TEMP_DIR="$TMPDIR"
'''
CONFIG = "vga: std\nargs: -device isa-applesmc\nvirtio0: local-lvm:vm-100-disk-1,size=64G\nnet0: vmxnet3=BC:24:11:AA:BB:CC,bridge=vmbr0\n"
ENGINE = r'''
nullmoth_engine() {
  echo "engine $*" >>"$LOGF"
  case "$1" in
    fetch) echo '{"tag": "v2.9", "path": "/c/nullmoth-nvidia-2.9.tar.gz", "sha256": "aa"}' ;;
    fw-fetch) [ -z "${NO_FW:-}" ] || return 1; echo '{"path": "/c/fw.run", "sha256": "bb"}' ;;
    payload) return "${PAYLOAD_RC:-0}" ;;
    vm-mac) cat >/dev/null; echo "bc:24:11:aa:bb:cc" ;;
    find-ip) echo "${FOUND_IP:-}" ;;
    port) [ "$3" == "${OPEN_PORT:-22}" ] ;;
    parse-result) python3 "$PARSER" "$2" ;;
    result) echo "${RESULTS%%|*}" ;;
  esac
}
'''


def run(bash, snippet, env=None, expect=0):
    base = {"QM_CONFIG": CONFIG}
    return bash(COMMON + ENGINE + snippet, env={**base, **(env or {})}, expect=expect)


def calls(tmp_path):
    return (tmp_path / "calls.log").read_text().splitlines()


def test_stage_downloads_builds_the_payload_stages_and_cleans_up(bash, tmp_path):
    res = run(bash, 'nullmoth_stage 100 latest sequoia; echo "TAG=$NM_TAG REF=$NM_PAYLOAD_REF"')
    log = calls(tmp_path)
    assert "TAG=v2.9" in res.stdout and "REF=local-lvm:vm-100-disk-" in res.stdout
    assert any(c.startswith("engine fetch latest ") and c.endswith("/nullmoth") for c in log)
    assert f"engine payload {tmp_path}/nullmoth-payload-100.img /c/nullmoth-nvidia-2.9.tar.gz aa /c/fw.run bb" in log
    assert any(c.startswith("download_recovery sequoia") for c in log)
    sets = [c for c in log if c.startswith("qm set 100 --sata1")]
    assert sets and "--sata2" in sets[0] and "order=sata1;virtio0;ide0" in sets[0]
    assert "stage 100 " in " ".join(log) and "drop 100 sata1" in log and "oc_patch 100 unstage" in log
    assert "restore_picker 100" in log and "drop 100 sata2" not in log  # the payload stays for the first boot


def test_stage_without_the_firmware_package_and_with_a_note(bash, tmp_path):
    res = run(bash, "nullmoth_stage 100 v2.8 sequoia", env={"NO_FW": "1"})
    assert "guest downloads it itself" in res.stdout
    assert f"engine payload {tmp_path}/nullmoth-payload-100.img /c/nullmoth-nvidia-2.9.tar.gz aa" in calls(tmp_path)


def test_stage_failure_puts_everything_back(bash, tmp_path):
    res = run(bash, "nullmoth_stage 100 latest sequoia", env={"STAGE_RC": "1"}, expect=1)
    log = calls(tmp_path)
    assert "drop 100 sata2" in log and "drop 100 sata1" in log and "oc_patch 100 unstage" in log
    assert any(c.startswith("put_back 100 vga std") for c in log) and any(c.startswith("put_back 100 boot") for c in log)
    assert "restore_picker 100" in log
    run(bash, "nullmoth_stage 100 latest sequoia", env={"PAYLOAD_RC": "1"}, expect=1)
    assert "Could not build the payload disk" in run(bash, "nullmoth_stage 100 latest sequoia", env={"PAYLOAD_RC": "1"}, expect=1).stdout
    assert "Could not download or verify" in run(bash, "nullmoth_engine() { return 1; }; nullmoth_stage 100 latest sequoia", expect=1).stdout
    assert res.returncode == 1


PARSER_SNIPPET = r'''
export PARSER="$TMPDIR/parse.py"
cat >"$PARSER" <<'PYEOF'
import json, sys
sys.path.insert(0, "SRC")
from osx_proxmox_next import nullmoth_engine as e
print(json.dumps(e.parse_result(sys.argv[1])))
PYEOF
'''


def parser(tmp_path):
    import osx_proxmox_next
    from pathlib import Path
    src = str(Path(osx_proxmox_next.__file__).resolve().parent.parent)
    return PARSER_SNIPPET.replace("SRC", src)


CLOCK = r'''
CLK="$TMPDIR/clock"; echo 0 >"$CLK"
date() { cat "$CLK"; }
sleep() { echo $(( $(cat "$CLK") + ${1:-1} )) >"$CLK"; }
'''


def test_wait_result_sees_the_final_report_after_the_install_report(bash, tmp_path):
    snippet = CLOCK + r'''
CNT="$TMPDIR/count"; echo 0 >"$CNT"
nullmoth_payload_result() {
  local n; n=$(cat "$CNT"); echo $((n + 1)) >"$CNT"
  case "$n" in 0|1) echo installed ;; *) echo "verified: kexts=5 display=no ip=10.0.0.9" ;; esac
}
nullmoth_wait_result 100 ref
echo "RESULT=$NM_RESULT"
'''
    res = run(bash, snippet)
    assert "RESULT=verified: kexts=5 display=no ip=10.0.0.9" in res.stdout
    assert "qm stop 100" not in calls(tmp_path)


def test_wait_result_kicks_a_hung_restart_once_and_gives_up_after_the_timeout(bash, tmp_path):
    snippet = CLOCK + r'''
NULLMOTH_FIRSTBOOT_TIMEOUT=600; NULLMOTH_REBOOT_GRACE=100
nullmoth_payload_result() { echo installed; }
nullmoth_wait_result 100 ref
echo "RESULT=$NM_RESULT"
'''
    res = run(bash, snippet)
    log = calls(tmp_path)
    assert "RESULT=installed" in res.stdout
    assert log.count("qm stop 100") == 1 and log.count("qm start 100") == 1


def test_guest_address_prefers_the_host_table_and_falls_back_to_the_report(bash, tmp_path):
    res = run(bash, 'nullmoth_guest_address 100 10.0.0.9; echo "IP=$NM_IP SSH=$NM_SSH_UP VNC=$NM_VNC_UP"', env={"FOUND_IP": "10.0.0.7"})
    assert "IP=10.0.0.7 SSH=yes VNC=no" in res.stdout
    res = run(bash, 'nullmoth_guest_address 100 10.0.0.9; echo "IP=$NM_IP SSH=$NM_SSH_UP"')
    assert "IP=10.0.0.9 SSH=yes" in res.stdout
    res = run(bash, 'NULLMOTH_IP_TIMEOUT=0; nullmoth_guest_address 100 ""; echo "IP=[$NM_IP]"')
    assert "IP=[]" in res.stdout
    res = run(bash, 'NULLMOTH_IP_TIMEOUT=0; nullmoth_guest_address 100 10.0.0.9; echo "SSH=$NM_SSH_UP"', env={"OPEN_PORT": "5900"})
    assert "SSH=no" in res.stdout


def first_boot(bash, tmp_path, result, state="stopped", extra=""):
    snippet = parser(tmp_path) + f'''
NM_PAYLOAD_REF=ref; NM_PAYLOAD_FILE="$TMPDIR/p.img"; NM_TAG=v2.9; : >"$NM_PAYLOAD_FILE"
nullmoth_wait_result() {{ NM_RESULT="{result}"; }}
nullmoth_guest_address() {{ NM_IP=10.0.0.9; NM_SSH_UP=yes; NM_VNC_UP=no; }}
{extra}
nullmoth_first_boot 100; echo "RC=$?"
'''
    return run(bash, snippet, env={"QM_STATUS": state}, expect=None)


def test_first_boot_report_of_a_verified_driver(bash, tmp_path):
    res = first_boot(bash, tmp_path, "verified: kexts=5 display=no ip=10.0.0.9")
    out = res.stdout
    assert "RC=0" in out and "NullMoth v2.9 is installed and loaded (5 extensions)" in out
    assert "ssh <user>@10.0.0.9\n" in out and "vnc://10.0.0.9   (not answering yet)" in out
    assert "The card has no display yet" in out  # no monitor was seen
    assert "Escape hatch" in out and "1401 app" in out
    log = calls(tmp_path)
    assert "drop 100 sata2" in log and log[-1] == "qm start 100"
    assert not (tmp_path / "p.img").exists()


def test_first_boot_report_hides_the_monitor_line_when_a_display_was_seen(bash, tmp_path):
    out = first_boot(bash, tmp_path, "verified: kexts=5 display=yes ip=10.0.0.9").stdout
    assert "The card has no display yet" not in out and "RC=0" in out


def test_first_boot_report_of_a_failure_and_of_silence(bash, tmp_path):
    out = first_boot(bash, tmp_path, "failed: the driver installer stopped: STOP").stdout
    assert "RC=1" in out and "not confirmed in VM 100: the driver installer stopped: STOP" in out and "nullmoth.log" in out
    out = first_boot(bash, tmp_path, "installed").stdout
    assert "no final report within 45 minutes (last report: installed)" in out and "RC=1" in out
    out = first_boot(bash, tmp_path, "").stdout
    assert "the guest reported nothing" in out


def test_first_boot_stops_a_guest_that_does_not_power_off(bash, tmp_path):
    first_boot(bash, tmp_path, "verified: kexts=5 display=no ip=1.2.3.4", state="running")
    assert "qm stop 100" in calls(tmp_path)


def test_the_walkthrough_flags_reach_nvidia_attach():
    from test_gpu_parity import BASH
    assert "--nullmoth-version) GPU_NULLMOTH_VERSION" in BASH and "${GPU_NULLMOTH_VERSION:+--nullmoth-version" in BASH


def test_wait_result_never_kicks_a_driver_that_is_loading(bash, tmp_path):
    snippet = CLOCK + r'''
NULLMOTH_FIRSTBOOT_TIMEOUT=900; NULLMOTH_REBOOT_GRACE=100
CNT="$TMPDIR/count"; echo 0 >"$CNT"
nullmoth_payload_result() {
  local n; n=$(cat "$CNT"); echo $((n + 1)) >"$CNT"
  case "$n" in 0) echo installed ;; *) echo verifying ;; esac
}
nullmoth_wait_result 100 ref
echo "RESULT=$NM_RESULT"
'''
    res = run(bash, snippet)
    assert "RESULT=verifying" in res.stdout and "qm stop 100" not in calls(tmp_path)


def test_cleanup_stops_a_running_vm_drops_the_payload_and_deletes_the_image(bash, tmp_path):
    (tmp_path / "p.img").write_text("x")
    run(bash, 'NM_PAYLOAD_FILE="$TMPDIR/p.img"; nullmoth_cleanup 100', env={"QM_STATUS": "running"})
    log = calls(tmp_path)
    assert "qm stop 100" in log and "drop 100 sata2" in log and not (tmp_path / "p.img").exists()
    run(bash, 'NM_PAYLOAD_FILE="$TMPDIR/p.img"; nullmoth_cleanup 100')
    assert "qm stop 100" not in calls(tmp_path)


def test_a_vm_that_will_not_start_is_cleaned_up(bash, tmp_path):
    snippet = r'''
qm() { echo "qm $*" >>"$LOGF"; [ "$1" != start ]; }
NM_PAYLOAD_REF=ref; NM_PAYLOAD_FILE="$TMPDIR/p.img"
nullmoth_first_boot 100; echo "RC=$?"
'''
    res = run(bash, snippet)
    assert "RC=1" in res.stdout and "Could not start VM 100" in res.stdout and "drop 100 sata2" in calls(tmp_path)


def test_an_interrupt_while_waiting_cleans_up(bash, tmp_path):
    snippet = r'''
nullmoth_wait_result() { kill -INT $$; sleep 1; }
NM_PAYLOAD_REF=ref; NM_PAYLOAD_FILE="$TMPDIR/p.img"
nullmoth_first_boot 100
'''
    res = run(bash, snippet, env={"QM_STATUS": "running"}, expect=130)
    log = calls(tmp_path)
    assert "qm stop 100" in log and "drop 100 sata2" in log
