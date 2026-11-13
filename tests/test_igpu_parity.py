"""Python/bash parity for the Intel iGPU: the same engine runs in both, the bash glue must agree with it."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gpu_fakes import FakeRun, make_host
from nullmoth_fakes import add_dev
from osx_proxmox_next import gpu, gpu_setup, igpu_attach, igpu_engine
from test_gpu_parity import BASH, BLOCK, QM, bash, block_file, vm  # noqa: F401  (fixtures)
from test_igpu_flow import host as flow_host, session as flow_session

VM_CFG = ('name: macos-sequoia\nargs: -device isa-applesmc,osk="x" -cpu host\nmachine: pc\n'
          'ide0: local-lvm:vm-100-disk-0,media=disk\nvirtio0: local-lvm:vm-100-disk-1,size=64G\nvga: std\n')
SETPCI = "#!/bin/sh\nprintf '4b800001\\n02c1\\n'\n"


def igd_env(tmp_path: Path, bash, driver: str | None = "vfio-pci", device: int = 0x46A6, **extra) -> dict[str, str]:
    root = make_host(tmp_path / "root")
    add_dev(root / "sys", igpu_engine.IGD_BDF, 0x8086, device, driver=driver, group=1, boot_vga=True)
    (bash.stub_bin / "setpci").write_text(SETPCI)
    (bash.stub_bin / "setpci").chmod(0o755)
    return {"GPU_ROOT": str(root), "GPU_SYSFS": str(root / "sys"), "GPU_SNAPSHOT_BASE": str(tmp_path / "snap"),
            "IGPU_CACHE": str(tmp_path / "cache"), **extra}


def test_the_bash_installer_embeds_and_dispatches_the_igpu_commands():
    assert 'if [ "${1:-}" == "igpu-check" ]' in BASH and 'if [ "${1:-}" == "igpu-finish" ]' in BASH
    assert BASH.index('"${1:-}" == "igpu-check"') < BASH.index("This will create a new macOS VM")
    assert "igpu_convert_fresh \"$VMID\"" in BASH and 'unattended_install "$VMID" "$DISK_GB_NUM" "$IGPU_SLOT"' in BASH


def test_pins_through_bash_are_the_engines(bash):
    for name, (file, url, sha) in igpu_engine.PINS.items():
        assert [bash(f"igpu_pin {name} {i}").stdout.strip() for i in range(3)] == [file, url, sha]


@pytest.mark.parametrize("device", [0x46A6, 0x9A49, 0x4C8A, 0x56A0, 0x0000])
def test_verdicts_match_python(bash, device):
    kind, head, detail = igpu_engine.verdict(device)
    assert bash(f"gpu_verdict intel {device}").stdout.strip() == f"{kind}|{head}|{detail}"
    assert gpu_setup.verdict("intel", device) == (kind, head, detail)


@pytest.mark.parametrize("driver,device,ok", [("vfio-pci", 0x46A6, True), ("i915", 0x46A6, False), ("vfio-pci", 0x4C8A, False)])
def test_check_matches_python(bash, tmp_path, driver, device, ok):
    env = igd_env(tmp_path, bash, driver, device)
    py = igpu_engine.check(env["GPU_ROOT"], FakeRun({"setpci": (0, "4b800001\n02c1\n")}))
    out = bash('GPU_ISSUES=(); GPU_WARNS=(); igpu_check; printf "%s\\n" "${GPU_ISSUES[@]}"; echo "MB=${IGPU_MB} ROM=${IGPU_ROM_FILE}"',
               env=env).stdout.splitlines()
    assert out[-1] == f"MB={py['layout']['max_ram_below_4g_mb'] if py['layout'] else ''} ROM={py['info']['rom_file'] if py['info'] else ''}"
    assert [ln for ln in out[:-1] if ln] == py["issues"] and (not py["issues"]) == ok


def test_check_command_prints_the_layout(bash, tmp_path):
    res = bash("igpu_check_command", env=igd_env(tmp_path, bash))
    assert "Intel Iris Xe Graphics (8086:46a6)" in res.stdout and "max-ram-below-4g=1342M and ROM ADL-H_RPL-H_GOPv21_igd.rom" in res.stdout
    bad = bash("igpu_check_command", env=igd_env(tmp_path / "b", bash, "i915"), expect=2)
    assert "REFUSED" in bad.stdout and "still uses the iGPU" in bad.stdout


@pytest.mark.parametrize("change,text", [
    (("machine: pc\n", "machine: q35\n"), "needs i440fx"),
    (("machine: pc\n", ""), "default machine type"),
    (("vga: std\n", "vga: std\nsata1: x:vm-1-disk-3,media=disk\n"), "recovery disk"),
    (("-device isa-applesmc", "-device other"), "not a macOS VM"),
    (("name: macos-sequoia", "name: macos-monterey"), "not supported on macOS Monterey 12"),
])
def test_vm_issues_match_python(bash, change, text):
    cfg = VM_CFG.replace(*change)
    py = igpu_attach.vm_issues(100, gpu_parse(cfg), False, False)
    sh = bash(f"GPU_ISSUES=(); igpu_vm_issues 100 \"$CFG\" 'status: stopped' no; printf '%s\\n' \"${{GPU_ISSUES[@]}}\"", env={"CFG": cfg}).stdout.splitlines()
    assert text in py[-1 if len(py) == 1 else 0] + " ".join(py)
    # the bash hint names the installer where Python names the CLI; everything else is word for word
    norm = lambda lines: [re.sub(r"Create a new VM with: .*", "", ln) for ln in lines]
    assert norm(sh) == norm(py)


def gpu_parse(cfg: str) -> dict[str, str]:
    from osx_proxmox_next.gpu import parse_config
    return parse_config(cfg)


def test_attach_dry_run_matches_the_python_plan(bash, vm, tmp_path):
    vm(100, "stopped", VM_CFG)
    env = {**vm.env, **igd_env(tmp_path, bash)}
    out = bash("igpu_attach 100 --dry-run", env=env).stdout
    settings = igpu_engine.vm_settings(1342, "ADL-H_RPL-H_GOPv21_igd.rom")
    assert f"qm set 100 --hostpci0 {settings['hostpci0']}" in out
    assert f"qm set 100 --args '{igpu_engine.merge_args(chr(45) + 'device isa-applesmc,osk=' + chr(34) + 'x' + chr(34) + ' -cpu host', settings['args_add'])}'" in out
    assert "qm set 100 --vga none" in out and not vm.calls()


def test_attach_applies_in_the_same_order_and_records_the_vm(bash, vm, tmp_path):
    vm(100, "stopped", VM_CFG)
    env = {**vm.env, **igd_env(tmp_path, bash)}
    script = ('igpu_fetch_assets() { IGPU_WEX_ZIP=w; IGPU_AMFI_ZIP=a; IGPU_ROM_PATH=r; }; '
              'igpu_oc_patch() { echo "OC $*"; }; install() { echo "INSTALL $*"; }; igpu_attach 100')
    res = bash(script, env=env)
    assert res.stdout.index("OC 100 attach") < res.stdout.index("INSTALL -m 644 r /usr/share/kvm/ADL-H_RPL-H_GOPv21_igd.rom")
    settings = igpu_engine.vm_settings(1342, "ADL-H_RPL-H_GOPv21_igd.rom")
    assert vm.calls() == [f"qm set 100 --hostpci0 {settings['hostpci0']}",
                          "qm set 100 --args " + igpu_engine.merge_args('-device isa-applesmc,osk="x" -cpu host', settings["args_add"]),
                          "qm set 100 --vga none"]
    rec = json.loads((tmp_path / "state/gpu-attach-100.json").read_text())
    assert rec["oc"] == "igpu" and rec["vendor"] == "intel" and rec["vga"] == "std" and rec["hostpci0"] is None
    assert rec["after"]["added_args"] == settings["args_add"]
    assert list((tmp_path / "snap/generated/snapshots").glob("vm-100-*.conf"))


def test_attach_refuses_without_changing_anything(bash, vm, tmp_path):
    vm(100, "stopped", VM_CFG.replace("machine: pc\n", "machine: q35\n"))
    res = bash("igpu_attach 100", env={**vm.env, **igd_env(tmp_path, bash)}, expect=1)
    assert "REFUSED" in res.stdout and vm.calls() == []


def test_fresh_vm_conversion_matches_python(bash, vm, tmp_path):
    vm(100, "stopped", VM_CFG.replace("machine: pc\n", "machine: q35\n") + "ide2: local-lvm:vm-100-disk-5,media=disk\n")
    res = bash("igpu_oc_patch() { echo \"OC $*\"; }; igpu_convert_fresh 100", env=vm.env)
    assert "OC 100 fadt" in res.stdout
    calls = vm.calls()
    assert calls[0] == "qm set 100 --machine pc" and calls[1] == "qm set 100 --delete ide2"
    assert calls[2] == "qm set 100 --sata1 local-lvm:vm-100-disk-5,media=disk" and calls[3] == "qm set 100 --boot order=sata1;virtio0;ide0"
    py = igpu_attach.build_fresh_vm_steps(100)
    assert "order=sata1;virtio0;ide0" in py[1].argv[2] and "--sata1" in py[1].argv[2]


def test_the_recommendation_line_is_the_same(bash):
    line = "0000:00:02|intel|%d|Intel|intel|h|d|1|%s|||" 
    for driver, prepared in (("i915", False), ("vfio-pci", True)):
        card = gpu_setup.GpuCard(bdf="0000:00:02", vendor="intel", device_id=0x46A6, name="n", kind="intel", headline="", detail="",
                                 driver=driver, model="Intel Iris Xe Graphics")
        out = bash(f"igpu_detected_line '{line % (0x46A6, driver)}'").stdout
        assert out == card.detected_line and card.prepared is prepared


SCENARIOS = {
    "igpu_only": dict(igpu="i915"),
    "igpu_amd": dict(amd=True),
    "igpu_nvidia": dict(nvidia=True),
    "nvidia_only": dict(igpu=None, nvidia=True),
    "nothing": dict(igpu=None),
    "igpu_amd_nvidia": dict(amd=True, nvidia=True),
}


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
@pytest.mark.parametrize("macos", ["ventura", "sequoia", "tahoe"])
def test_scan_order_status_and_recommendation_match_python(bash, vm, tmp_path, scenario, macos):
    vm(120, "stopped", VM_CFG)
    root = flow_host(tmp_path / "root", **SCENARIOS[scenario])
    lspci = bash.stub_bin / "lspci"
    lspci.write_text("#!/bin/sh\nexit 1\n")
    lspci.chmod(0o755)
    out = bash('GPU_MACOS=%s; gpu_scan; for l in "${GPU_CARDS[@]}"; do echo "CARD $l"; IFS="|" read -r _ st <<<"$(gpu_version_status "$l" "$GPU_MACOS")"; '
               'echo "STATUS $st"; gpu_card_recommended "$l" && echo RECOMMENDED; done; true' % macos,
               env={**vm.env, "GPU_SYSFS": str(root / "sys")}).stdout.splitlines()
    sh_cards = [ln.split("|")[0][5:] for ln in out if ln.startswith("CARD ")]
    sh_status = [ln[7:] for ln in out if ln.startswith("STATUS ")]
    sh_rec = [sh_cards[i] for i, ln in enumerate([l for l in out if l.startswith(("CARD ", "RECOMMENDED"))]) if False]
    sh_rec = [ln for ln in out if ln == "RECOMMENDED"]
    py_cards = flow_session(root).scan(macos=macos)
    assert sh_cards == [c.bdf for c in py_cards]
    assert sh_status == [c.status for c in py_cards]
    assert len(sh_rec) == sum(c.recommended for c in py_cards)
    if sh_rec:
        idx = [i for i, ln in enumerate(out) if ln == "RECOMMENDED"][0]
        assert out[idx - 2].split("|")[0][5:] == next(c.bdf for c in py_cards if c.recommended)


@pytest.mark.parametrize("ids", [[7], [7, 8], [990, 991, 996]])
def test_the_shared_note_matches_python(bash, ids):
    out = bash("gpu_shared_note " + " ".join(map(str, ids))).stdout.strip()
    assert out == gpu.shared_note(ids)
    users = ",".join(f"{i}:stopped" for i in ids)
    card = f"0000:00:02|intel|{0x46A6}|n|intel|h|d|0|vfio-pci|{users}|||0"
    notes = [ln for ln in bash(f"gpu_card_notes '{card}'").stdout.splitlines() if ln.startswith("Also assigned")]
    assert notes == [gpu.shared_note(ids)]


def shell_functions(*names: str) -> str:
    """The text of bash functions from the installer, for tests that run them with stubbed commands."""
    out = []
    for name in names:
        start = BASH.index(f"function {name}() {{")
        out.append(BASH[start:BASH.index("\n}\n", start) + 3])
    return "\n".join(out) + "\n"


def run_shell(snippet: str, env: dict[str, str], expect: int | None = 0):
    import subprocess
    res = subprocess.run(["bash", "-c", "set -u\n" + snippet], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **env}, timeout=60)
    if expect is not None:
        assert res.returncode == expect, (res.returncode, res.stdout, res.stderr)
    return res


def write_ppms(tmp_path):
    from test_unattended import ppm
    for name, window in (("plain", False), ("window", True)):
        (tmp_path / f"{name}.ppm").write_bytes(ppm(window))
    return tmp_path


@pytest.mark.parametrize("before,after", [("plain", "window"), ("plain", "plain"), ("window", "plain"), ("window", "window")])
def test_the_bash_region_check_matches_python(tmp_path, before, after):
    from osx_proxmox_next.unattended import region_changed
    d = write_ppms(tmp_path)
    expect = region_changed((d / f"{before}.ppm").read_bytes(), (d / f"{after}.ppm").read_bytes())
    fn = shell_functions("unattended_region_changed")
    assert (run_shell(fn + f"unattended_region_changed {d}/{before}.ppm {d}/{after}.ppm", {}, None).returncode == 0) is expect
    assert run_shell(fn + f"unattended_region_changed {d}/{before}.ppm /nope", {}, None).returncode == 1


TERMINAL_STUBS = (
    'UNATTENDED_PICKER_TIMEOUT=1; UNATTENDED_RECOVERY_TIMEOUT=1; UNATTENDED_MAX_RECOVERY_RESETS=2; '
    'unattended_wait_frame() { return 0; }; unattended_settle_recovery() { return 0; }; unattended_wait_still() { :; }; '
    'sleep() { :; }; date() { n=$(cat "$KEYS.clock" 2>/dev/null || echo 0); n=$((n+3)); echo $n >"$KEYS.clock"; echo $n; }; '
    'msg_info() { echo "INFO $*"; }; msg_ok() { :; }; msg_error() { echo "ERR $*"; }; '
    'qm() { [ "$1" == sendkey ] && echo "$3" >>"$KEYS"; return 0; }; '
    'unattended_frame_size() { n=$(grep -c ctrl-f2 "$KEYS" 2>/dev/null || true); if [ "${n:-0}" -ge "$OPENS_ON" ]; then cp "$D/window.ppm" "/tmp/osx-next-unattended-$1.ppm"; '
    'else cp "$D/plain.ppm" "/tmp/osx-next-unattended-$1.ppm"; fi; echo 1; }; ')


@pytest.mark.parametrize("opens_on,rc,navs", [(1, 0, 1), (3, 0, 3), (99, 1, 3)])
def test_the_bash_terminal_is_confirmed_before_typing(tmp_path, opens_on, rc, navs):
    d = write_ppms(tmp_path)
    keys = tmp_path / "keys"
    env = {"KEYS": str(keys), "D": str(d), "OPENS_ON": str(opens_on)}
    fns = shell_functions("unattended_region_changed", "unattended_terminal_open", "unattended_open_recovery_terminal")
    out = run_shell(TERMINAL_STUBS + fns + "unattended_open_recovery_terminal 9777", env, expect=rc).stdout
    assert keys.read_text().split().count("ctrl-f2") == navs
    assert ("Terminal never opened" in out) is (rc == 1)
    assert out.count("opening it from the Utilities menu again") == navs - 1 if rc == 0 else out.count("again") == 2


STAGE_STUBS = (
    'igpu_oc_patch() { :; }; unattended_open_recovery_terminal() { :; }; sleep() { :; }; msg_info() { echo "INFO $*"; }; '
    'msg_error() { echo "ERR $*"; }; msg_ok() { :; }; pvesm() { echo /dev/payload; }; IGPU_START_WAIT=0; '
    'qm() { case "$1" in sendkey) echo "KEY $3" >>"$STATE.keys";; status) echo "status: ${QMSTATUS:-running}";; esac; }; '
    'unattended_type() { echo "TYPE"; N=$((N+1)); echo $N >"$STATE"; }; '
    'igpu_engine() { case "$1" in stage-command) echo CMD;; result) if [ "$(cat "$STATE" 2>/dev/null || echo 0)" -ge "${STARTS_ON:-99}" ]; '
    'then echo "${RESULT:-started}"; fi;; esac; }; ')


def test_the_bash_stage_retypes_until_the_script_starts(bash, tmp_path):
    state = tmp_path / "state"
    env = {"STATE": str(state), "STARTS_ON": "2", "RESULT": "staged"}
    out = bash(STAGE_STUBS + "N=0; igpu_wait_stopped() { return 0; }; igpu_stage 9 vol", env=env, expect=0).stdout.splitlines()
    keys = (tmp_path / "state.keys").read_text().split("\n")
    assert out.count("TYPE") == 2 and keys.count("KEY ret") == 2 and keys.count("KEY ctrl-c") == 1
    assert any("typing it again" in ln for ln in out)


def test_the_bash_stage_gives_up_after_three_tries(bash, tmp_path):
    env = {"STATE": str(tmp_path / "state"), "STARTS_ON": "99"}
    out = bash(STAGE_STUBS + "N=0; igpu_wait_stopped() { return 0; }; igpu_stage 9 vol", env=env, expect=1).stdout.splitlines()
    assert out.count("TYPE") == 3 and (tmp_path / "state.keys").read_text().split("\n").count("KEY ctrl-c") == 2
    assert any("did not start after 3 tries" in ln for ln in out)


def test_the_bash_install_tells_igpu_users_to_use_the_monitor():
    tail = BASH[BASH.index("unattended_install \"$VMID\" \"$DISK_GB_NUM\" \"$IGPU_SLOT\""):]
    tail = tail[:tail.index("else\n    SHOT=")]
    assert 'if [ "${IGPU:-no}" == "yes" ]; then' in tail and "${IGPU_SETUP_NOTE}" in tail
    assert tail.index("${IGPU_SETUP_NOTE}") < tail.index("Finish Setup Assistant in the VM console")


LAPTOP = "AD107M [GeForce RTX 4060 Max-Q / Mobile]"
DESKTOP = "AD107 [GeForce RTX 4060]"


@pytest.mark.parametrize("name", [LAPTOP, "GA104 [GeForce RTX 3070 Laptop GPU]", "TU117M [GeForce GTX 1650 Mobile]", DESKTOP, ""])
def test_the_laptop_nvidia_check_matches_python(bash, name):
    expect = "laptop" if gpu_setup.is_laptop_nvidia("nvidia", name) else "desktop"
    out = bash(f"gpu_is_laptop_nvidia nvidia '{name}' && echo laptop || echo desktop").stdout.strip()
    assert out == expect
    assert bash(f"gpu_is_laptop_nvidia amd '{name}' && echo laptop || echo desktop").stdout.strip() == "desktop"
    kind, head, detail = gpu_setup.verdict("nvidia", 0x28E0, name)
    assert bash(f"gpu_verdict nvidia {0x28E0} '{name}'").stdout.strip() == f"{kind}|{head}|{detail}"


@pytest.mark.parametrize("name,rec", [(LAPTOP, "intel"), (DESKTOP, "intel")])
def test_a_laptop_nvidia_scan_matches_python(bash, vm, tmp_path, name, rec):
    vm(120, "stopped", VM_CFG)
    root = flow_host(tmp_path / "root", nvidia=True)
    lspci = bash.stub_bin / "lspci"
    lspci.write_text(f"#!/bin/sh\ncase \"$*\" in *01:00.0*) echo '0000:01:00.0 VGA compatible controller [0300]: NVIDIA Corporation {name} [10de:2504]';; *) exit 1;; esac\n")
    lspci.chmod(0o755)
    out = bash('GPU_MACOS=sequoia; gpu_scan; for l in "${GPU_CARDS[@]}"; do echo "CARD $l"; IFS="|" read -r _ st <<<"$(gpu_version_status "$l" "$GPU_MACOS")"; '
               'echo "STATUS $st"; done; true', env={**vm.env, "GPU_SYSFS": str(root / "sys")}).stdout.splitlines()
    sh_cards = [ln.split("|")[0][5:] for ln in out if ln.startswith("CARD ")]
    sh_status = [ln[7:] for ln in out if ln.startswith("STATUS ")]
    sess = flow_session(root)
    sess.run = FakeRun({"lspci -nn -s 0000:01:00.0": (0, f"0000:01:00.0 VGA compatible controller [0300]: NVIDIA Corporation {name} [10de:2504]\n")}, default=(1, ""))
    py = sess.scan(macos="sequoia")
    assert sh_cards == [c.bdf for c in py] and sh_status == [c.status for c in py]
    assert next(c for c in py if c.recommended).vendor == rec


def test_the_bash_card_text_carries_the_status_and_the_recommendation_line(bash, vm, tmp_path):
    vm(120, "stopped", VM_CFG)
    root = flow_host(tmp_path / "root", amd=True)
    lspci = bash.stub_bin / "lspci"
    lspci.write_text("#!/bin/sh\nexit 1\n")
    lspci.chmod(0o755)
    out = bash('gpu_scan; for l in "${GPU_CARDS[@]}"; do gpu_card_text "$l"; done', env={**vm.env, "GPU_SYSFS": str(root / "sys")}).stdout
    assert "works with macOS Sequoia 15 (recommended)" in out and "works with macOS Sequoia 15 " in out
    assert out.count("(recommended)") == 1 and "Recommended: Intel" not in out  # the AMD card wins


def test_the_bash_choice_asks_and_can_switch(bash, vm, tmp_path):
    vm(120, "stopped", VM_CFG)
    root = flow_host(tmp_path / "root", amd=True)
    lspci = bash.stub_bin / "lspci"
    lspci.write_text("#!/bin/sh\nexit 1\n")
    lspci.chmod(0o755)
    env = {**vm.env, "GPU_SYSFS": str(root / "sys"), "GPU_ROOT": str(root)}
    stubs = ("gpu_summary_line() { :; }; gpu_state_save() { echo \"SAVE $1\" | cut -c1-80; }; gpu_step_complete() { :; }; "
             "gpu_host() { echo '{}'; }; ")
    declined = bash(stubs + "gpu_ui_yesno() { return 1; }; gpu_step_choose '' '' gpu no", env=env, expect=None)
    assert "Recommended: " in declined.stdout  # the AMD card is offered, declining it falls to the iGPU, which asks again
    nothing = flow_host(tmp_path / "none", igpu=None)
    res = bash(stubs + "gpu_step_choose '' '' gpu no", env={**env, "GPU_SYSFS": str(nothing / "sys")}, expect=2)
    assert "Nothing here can run macOS graphics" in res.stdout


def test_the_first_boot_text_and_notice_match_python():
    assert igpu_attach.SETUP_ASSISTANT_NOTE == re.search(r'^IGPU_SETUP_NOTE="(.*)"$', BLOCK, re.M).group(1).replace("\\'", "'")
    assert igpu_attach.TAHOE_STAGE_NOTE == re.search(r'^IGPU_TAHOE_STAGE_NOTE="(.*)"$', BLOCK, re.M).group(1)
    assert igpu_attach.TAHOE_NOTE == re.search(r'^IGPU_TAHOE_NOTE="(.*)"$', BLOCK, re.M).group(1)
    for line in igpu_attach.IGPU_NEXT_STEPS:
        assert line.split("\n")[0].replace("'", "'") in BLOCK
    from osx_proxmox_next import igpu_cli
    assert igpu_cli.NOTICE == re.search(r'^IGPU_NOTICE="(.*)"$', BLOCK, re.M).group(1)
    for name in ("STAGE_TIMEOUT", "FIRSTBOOT_TIMEOUT", "RESULT_POLL", "RESULT_GRACE"):
        from osx_proxmox_next import igpu_finish
        key = {"STAGE_TIMEOUT": "IGPU_STAGE_TIMEOUT", "FIRSTBOOT_TIMEOUT": "IGPU_FIRSTBOOT_TIMEOUT",
               "RESULT_POLL": "IGPU_RESULT_POLL", "RESULT_GRACE": "IGPU_RESULT_GRACE"}[name]
        assert int(re.search(rf"^{key}=(\d+)$", BLOCK, re.M).group(1)) == getattr(igpu_finish, name)


def _bar0(tmp_path: Path, **regs: int) -> Path:
    import struct
    sysfs = tmp_path / "sysfs"
    dev = sysfs / "bus/pci/devices" / igpu_engine.IGD_BDF
    dev.mkdir(parents=True)
    buf = bytearray(0x80000)
    for name, value in regs.items():
        struct.pack_into("<I", buf, igpu_engine.ACCEL_REGS[name], value)
    (dev / "resource0").write_bytes(bytes(buf))
    return sysfs


def test_the_accel_check_text_and_timeout_match_python():
    from osx_proxmox_next import igpu_cli
    assert int(re.search(r"^IGPU_ACCEL_TIMEOUT=(\d+)$", BLOCK, re.M).group(1)) == igpu_engine.ACCEL_TIMEOUT
    assert igpu_cli.TROUBLESHOOTING_URL == re.search(r'^IGPU_TROUBLESHOOTING_URL="(.*)"$', BLOCK, re.M).group(1)
    assert 'igpu_confirm_accel "$vmid"' in BLOCK


@pytest.mark.parametrize("regs,ok", [({"RCS_TAIL": 0x1c8, "BCS_TAIL": 0x40, "PLANE_SURF_1_A": 0x2000}, True),
                                     ({"PLANE_SURF_1_A": 0x2000}, False)])
def test_bash_and_python_agree_on_whether_acceleration_is_confirmed(bash, tmp_path, capsys, regs, ok):
    from osx_proxmox_next import igpu_cli, igpu_finish
    sysfs = _bar0(tmp_path, **regs)
    env = {"GPU_SYSFS": str(sysfs)}
    res = bash("IGPU_ACCEL_TIMEOUT=0; igpu_confirm_accel 996", env=env, expect=0 if ok else 1)
    active, detail = igpu_engine.check_accel(0, str(sysfs))
    assert active == ok
    py = igpu_finish.FinishResult("installed", True, [], active, detail)
    assert igpu_cli._report_finish(996, py) == (0 if ok else 1)
    py_out = capsys.readouterr().out
    marker = "Metal acceleration is active" if ok else "acceleration was not confirmed"
    assert marker in res.stdout and marker in py_out
    assert f"({detail})" in res.stdout and f"({detail})" in py_out
    assert ok or igpu_cli.TROUBLESHOOTING_URL in res.stdout


def test_a_missing_bar_is_not_confirmed(bash, tmp_path):
    res = bash("IGPU_ACCEL_TIMEOUT=0; igpu_confirm_accel 996", env={"GPU_SYSFS": str(tmp_path)}, expect=1)
    assert "acceleration was not confirmed" in res.stdout and "cannot read the iGPU registers" in res.stdout


# ── the power watchdog (Tahoe) ───────────────────────────────────────

WD_CFG = VM_CFG.replace("name: macos-sequoia", "name: macos-tahoe") + "hostpci0: 0000:00:02.0,legacy-igd=1,romfile=x.rom\n"


def test_the_watchdog_note_and_files_match_python():
    from osx_proxmox_next import igpu_watchdog
    assert igpu_watchdog.WATCHDOG_NOTE == re.search(r'^IGPU_WD_NOTE="(.*)"$', BLOCK, re.M).group(1)
    assert f"IGPU_WD_DAEMON={igpu_watchdog.DAEMON_PATH}\n" in BLOCK and f"IGPU_WD_UNIT={igpu_watchdog.UNIT_PATH}\n" in BLOCK


@pytest.mark.parametrize("serial", ["", "serial0: socket\n"])
def test_the_bash_watchdog_install_runs_the_python_steps_in_order(bash, vm, serial):
    from osx_proxmox_next import igpu_watchdog
    vm(100, "stopped", WD_CFG + serial)
    fake = ('install() { echo "RUN install $*" >&2; }; systemctl() { echo "RUN systemctl $*" >&2; }; '
            'igpu_oc_patch() { echo "RUN oc $2" >&2; }; ')
    res = bash(fake + 'qm() { if [ "$1" == set ]; then echo "RUN qm $*" >&2; else command qm "$@"; fi; }; igpu_watchdog_enable 100',
               env=vm.env)
    sh = [ln[4:] for ln in res.stderr.splitlines() if ln.startswith("RUN ")]
    py = []
    for step in igpu_watchdog.enable_steps(100, gpu_parse(WD_CFG + serial)):
        if step.argv[0] == "bash":
            py.append("oc serial")
        elif step.argv[0] == "install":
            py.append("install -D -m " + step.argv[3] + " <src> " + step.argv[5])
        else:
            py.append(" ".join(step.argv))
    sh = [re.sub(r"install -D -m (\d+) \S+ ", r"install -D -m \1 <src> ", ln) for ln in sh]
    assert sh == py


def test_the_bash_watchdog_removal_matches_python(bash, vm):
    from osx_proxmox_next import igpu_watchdog
    vm(100, "stopped", WD_CFG)
    res = bash('systemctl() { echo "RUN systemctl $*"; }; igpu_oc_patch() { echo "RUN oc $2"; }; igpu_watchdog_command 100 --remove',
               env=vm.env)
    assert [ln for ln in res.stdout.splitlines() if ln.startswith("RUN ")] == [
        "RUN systemctl disable --now osx-next-power-watchdog@100.service", "RUN oc unserial"]
    steps = igpu_watchdog.disable_steps(100)
    assert "systemctl disable --now osx-next-power-watchdog@100.service" in steps[0].argv[2] and "unserial" in steps[1].argv[2]
    assert "The watchdog of VM 100 is removed." in res.stdout


@pytest.mark.parametrize("cfg,status", [
    (WD_CFG, "running"),
    (WD_CFG.replace("hostpci0: 0000:00:02.0", "hostpci0: 0000:06:00"), "stopped"),
])
def test_the_bash_watchdog_refusals_match_python(bash, vm, monkeypatch, cfg, status):
    from types import SimpleNamespace
    from osx_proxmox_next import igpu_watchdog
    vm(100, status, cfg)
    res = bash("igpu_watchdog_command 100", env=vm.env, expect=2)
    monkeypatch.setattr(igpu_watchdog, "fetch_vm_info", lambda vmid, adapter=None: SimpleNamespace(config_raw=cfg, status=status))
    issues, steps = igpu_watchdog.prepare(100, adapter=object())
    norm = lambda s: s.replace("osx-proxmox-next.sh igpu-finish", "osx-next-cli igpu-finish")
    assert [norm(ln[len("REFUSED: "):]) for ln in res.stdout.splitlines() if ln.startswith("REFUSED: ")] == issues and not steps


def test_the_bash_finish_installs_the_watchdog_between_attach_and_first_boot():
    body = BLOCK[BLOCK.index("function igpu_finish() {"):BLOCK.index("function igpu_after_install()")]
    assert body.index('igpu_attach "$vmid"') < body.index('if igpu_watchdog_enable "$vmid"') < body.index('qm start "$vmid"')
    assert 'if [ "$ver" == "tahoe" ]; then\n    if igpu_watchdog_enable "$vmid"' not in body  # every release records crashes
    assert 'if [ "${1:-}" == "igpu-watchdog" ]' in BASH


# ── latest WhateverXe by default, any release on request, crash recorder ─────────────────────

RELEASES = json.dumps([
    {"tag": "1.0-rc1", "version": "1.0-rc1", "published": "2026-11-09T00:00:00Z", "prerelease": True},
    {"tag": "0.9.0", "version": "0.9.0", "published": "2026-11-01T00:00:00Z", "prerelease": False},
    {"tag": "0.8.0", "version": "0.8.0", "published": "2026-10-01T00:00:00Z", "prerelease": False}])
ASSETS = json.dumps({"whateverxe": {"path": "/c/W.zip", "version": "0.9.0", "tag": "0.9.0", "sha256": "9" * 64, "source": "latest"},
                     "graphics": {"path": "/c/G.pkg", "version": "0.4.1", "tag": "v0.4.1", "sha256": "4" * 64, "source": "latest"}})


def test_the_bash_fetch_takes_the_chosen_releases_and_pins_only_amfipass(bash, tmp_path):
    stub = ("igpu_engine() { case \"$1\" in assets) echo \"ASK $*\" >&2; printf '%s' \"$ASSETS\" ;; "
            "fetch) echo \"/c/$2\" ;; esac; }; msg_info() { :; }; msg_ok() { echo \"OK $*\"; }; ")
    res = bash(stub + 'IGPU_WEX_CHOICE=0.8.0; igpu_fetch_assets adl-h; echo "$IGPU_WEX_ZIP $IGPU_PKG $IGPU_AMFI_ZIP $IGPU_ROM_PATH"',
               env={"ASSETS": ASSETS, "IGPU_CACHE": str(tmp_path / "cache")})
    assert "ASK assets " + str(tmp_path / "cache") + " 0.8.0 latest" in res.stderr
    assert "Using WhateverXe 0.9.0 and WhateverXe Graphics 0.4.1" in res.stdout
    assert res.stdout.splitlines()[-1] == "/c/W.zip /c/G.pkg /c/pin:amfipass /c/rom:adl-h"


def test_the_bash_fetch_fails_with_the_engines_message_when_github_cannot_be_asked(bash, tmp_path):
    stub = 'igpu_engine() { echo "ERROR: Could not reach GitHub (x)." >&2; return 1; }; msg_info() { :; }; '
    res = bash(stub + "igpu_fetch_assets adl-h", env={"IGPU_CACHE": str(tmp_path / "c")}, expect=1)
    assert "Could not reach GitHub" in res.stderr


def test_the_bash_finish_command_takes_the_version_flags(bash):
    stub = 'igpu_finish() { echo "FINISH $IGPU_WEX_CHOICE $IGPU_GFX_CHOICE $*"; }; '
    res = bash(stub + "igpu_finish_command 100 sequoia --wex-version 0.8.0 --graphics-version v0.4.0 --yes",
               env={"GPU_YES": "yes"})
    assert "FINISH 0.8.0 v0.4.0 100 sequoia" in res.stdout
    res = bash(stub + "igpu_finish_command 100 --yes")
    assert "FINISH latest latest 100" in res.stdout and "iGPU shares the CPU's power budget" in res.stdout
    bash(stub + "igpu_finish_command 100 --wex-version", expect=1)


def test_the_bash_versions_list_matches_the_python_list(bash, capsys, monkeypatch):
    from osx_proxmox_next import igpu_cli
    stub = 'igpu_engine() { printf "%s" "$RELEASES"; }; '
    res = bash(stub + "igpu_versions_command", env={"RELEASES": RELEASES})
    assert "WhateverXe releases, newest first:" in res.stdout and "WhateverXe Graphics releases, newest first:" in res.stdout
    assert "  1.0-rc1  2026-11-09 (pre-release)" in res.stdout and "  0.9.0  2026-11-01 (latest)" in res.stdout
    assert "  0.8.0  2026-10-01\n" in res.stdout
    monkeypatch.setattr(igpu_engine, "list_releases", lambda kind, get=None: json.loads(RELEASES))
    assert igpu_cli.run_versions() == 0
    assert capsys.readouterr().out.splitlines()[:4] == res.stdout.splitlines()[:4]


def test_the_bash_release_menu_marks_the_latest_and_answers_latest_for_it(bash, tmp_path):
    log = tmp_path / "whiptail.args"
    stub = ('igpu_engine() { printf "%s" "$RELEASES"; }; '
            'whiptail() { printf "%s\\n" "$*" >"$ARGLOG"; printf "%s" "$PICK" >&2; }; ')
    env = {"RELEASES": RELEASES, "ARGLOG": str(log)}
    res = bash(stub + "igpu_pick_release whateverxe", env={**env, "PICK": "0.9.0"})
    args = log.read_text()
    assert res.stdout.strip() == "latest" and "--default-item 0.9.0" in args and "0.9.0 2026-11-01 (latest)" in args
    assert "1.0-rc1 2026-11-09 (pre-release)" in args
    res = bash(stub + "igpu_pick_release graphics", env={**env, "PICK": "0.8.0"})
    assert res.stdout.strip() == "0.8.0"
    bash(stub.replace('printf "%s" "$PICK" >&2;', "return 1;") + "igpu_pick_release whateverxe", env=env, expect=1)


def test_the_bash_menu_is_skipped_without_a_terminal_and_when_flags_were_given(bash):
    res = bash('igpu_pick_release() { echo asked; }; igpu_choose_versions; echo "$IGPU_WEX_CHOICE $IGPU_GFX_CHOICE"')
    assert res.stdout.strip() == "latest latest"  # no terminal: no menu
    res = bash('gpu_use_ui() { return 0; }; igpu_pick_release() { echo "t-$1"; }; igpu_choose_versions; echo "$IGPU_WEX_CHOICE $IGPU_GFX_CHOICE"')
    assert res.stdout.strip() == "t-whateverxe t-graphics"
    res = bash('gpu_use_ui() { return 0; }; igpu_pick_release() { echo asked; }; IGPU_VERSIONS_CHOSEN=yes; igpu_choose_versions; echo "$IGPU_WEX_CHOICE"')
    assert res.stdout.strip() == "latest"


def test_the_bash_report_runs_the_embedded_recorder(bash, tmp_path):
    env = {"IGPU_LOG_DIR": str(tmp_path / "log"), "IGPU_STATE_DIR": str(tmp_path / "state"), "GPU_SYSFS": str(tmp_path / "sys")}
    res = bash("igpu_report_command 100 --out " + str(tmp_path / "out"), env=env)
    assert "iGPU report for VM 100" in res.stdout and "Nothing was sent anywhere" in res.stdout
    assert list((tmp_path / "out").glob("igpu-report-100-*.tar.gz"))
    bash("igpu_report_command abc", expect=2)


def test_the_bash_installer_dispatches_the_new_commands_and_installs_the_recorder():
    assert 'if [ "${1:-}" == "igpu-versions" ]' in BASH and 'if [ "${1:-}" == "igpu-report" ]' in BASH
    assert 'install -D -m 644 "$GPU_ENGINE_DIR/igpu_diag.py" "$IGPU_WD_DIAG"' in BASH
    from osx_proxmox_next import igpu_watchdog
    assert f"IGPU_WD_DIAG={igpu_watchdog.DIAG_PATH}\n" in BASH


def test_the_bash_records_the_chosen_versions_per_vm_and_shows_them_in_the_status():
    body = BLOCK[BLOCK.index("function igpu_finish() {"):BLOCK.index("function igpu_after_install()")]
    assert body.index("state-record") > body.index('igpu_attach "$vmid"') and 'state-write "$vmid"' in body
    assert 'igpu_engine state-show' in BASH and "Installed: " in BASH


def test_every_bash_pin_lookup_names_a_pin_that_exists():
    names = set(re.findall(r"igpu_pin (\w+) \d", BASH))
    assert names and names <= set(igpu_engine.PINS)  # WhateverXe and Graphics are not pins: they come from the releases


def test_the_bash_attach_checks_the_kext_against_the_release_digest():
    assert 'IGPU_WEX_SHA256="$(gpu_json \'d["whateverxe"]["sha256"]\' <<<"$IGPU_ASSETS_JSON")"' in BASH


def test_a_release_tag_with_quotes_never_reaches_python_source_in_the_bash_menu(bash, tmp_path):
    evil = 'x"+__import__("os").system("touch ' + str(tmp_path / "pwned") + '")+"'
    releases = json.dumps([{"tag": evil, "version": evil, "published": "2026-11-09T00:00:00Z", "prerelease": False},
                           {"tag": "0.8.0", "version": "0.8.0", "published": "2026-10-01T00:00:00Z", "prerelease": False}])
    stub = ('igpu_engine() { printf "%s" "$RELEASES"; }; whiptail() { printf "%s" "$PICK" >&2; }; ')
    res = bash(stub + "igpu_pick_release whateverxe", env={"RELEASES": releases, "PICK": "0.8.0"})
    assert res.stdout.strip() == "0.8.0" and not (tmp_path / "pwned").exists()
