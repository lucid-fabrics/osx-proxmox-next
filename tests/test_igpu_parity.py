"""Python/bash parity for the Intel iGPU: the same engine runs in both, the bash glue must agree with it."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gpu_fakes import FakeRun, make_host
from nullmoth_fakes import add_dev
from osx_proxmox_next import gpu_setup, igpu_attach, igpu_engine
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
