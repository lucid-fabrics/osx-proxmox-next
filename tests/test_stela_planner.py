"""The Stela VM plan and config: qm and shell steps, the VM's QEMU arguments, and nothing spliced into a shell."""
from __future__ import annotations

import pytest

from osx_proxmox_next.stela import (
    STELA_ASSET,
    STELA_RELEASE_URL,
    STELA_SHA256,
    STELA_VM_TAG,
    StelaConfig,
    mac_for,
    tap_for,
    validate_stela_config,
)
from osx_proxmox_next.stela_planner import (
    STICK_DIR,
    STICK_GB,
    build_stela_destroy_plan,
    build_stela_finish_plan,
    build_stela_plan,
    hook_path,
    hook_script,
    stela_binary,
    stela_qemu_args,
    stela_vm_options,
    stick_path,
)


def cfg(**kw) -> StelaConfig:
    base = dict(vmid=478, name="macos27-stela", storage="local-lvm")
    base.update(kw)
    return StelaConfig(**base)


def by_title(steps, title):
    return next(s for s in steps if s.title == title)


def opt(argv, name):
    return argv[argv.index(name) + 1]


def test_the_pinned_release_is_the_one_the_live_run_checked():
    assert STELA_SHA256 == "6236ddffb39e52ffd6ea71ec3a763788e75497493f4de19ad9e8a35a3722ed6a"
    assert STELA_ASSET == "stela-v0.5.0-linux-x86_64.tar.gz"
    assert STELA_RELEASE_URL == "https://github.com/tylertms/Stela/releases/download/v0.5.0"


def test_the_plan_checks_downloads_makes_the_stick_then_the_vm_and_starts_it_last():
    steps = build_stela_plan(cfg())
    assert [s.title for s in steps] == [
        "Check the VMID is free", "Check snippet storage", "Download and check Stela", "Make the boot stick", "Write the VM's hook script",
        "Create the VM", "Start the VM"]
    assert steps[-1].argv == ["qm", "start", "478"]


def test_the_download_step_carries_the_pin_and_the_release_folder_as_arguments():
    argv = by_title(build_stela_plan(cfg()), "Download and check Stela").argv
    assert argv[:4] == ["bash", "-c", argv[2], "_"]
    assert argv[4:7] == [STELA_RELEASE_URL, STELA_ASSET, STELA_SHA256]
    assert argv[8] == stela_binary()
    script = argv[2]
    assert "listed" in script and "sha256sum -c" in script and STELA_SHA256 not in script


def test_a_mirror_folder_is_used_but_the_pin_stays():
    steps = build_stela_plan(cfg(stela_url="http://127.0.0.1:8915"))
    argv = by_title(steps, "Download and check Stela").argv
    assert argv[4] == "http://127.0.0.1:8915" and argv[6] == STELA_SHA256


def test_the_stick_is_a_32_gb_image_written_by_stela_online_with_no_wifi():
    argv = by_title(build_stela_plan(cfg()), "Make the boot stick").argv
    assert argv[4:] == [stela_binary(), stick_path(478), str(STICK_GB)]
    assert stick_path(478) == f"{STICK_DIR}/stick-478.img" and STICK_GB == 32
    for word in ("--installer online", "--wifi none", "--macos latest", "--yes", "losetup -d"):
        assert word in argv[2]


def test_the_vm_is_ovmf_q35_with_a_sata_disk_and_no_proxmox_nic():
    argv = by_title(build_stela_plan(cfg()), "Create the VM").argv
    assert argv[:5] == ["qm", "create", "478", "--name", "macos27-stela"]
    assert opt(argv, "--machine") == "q35" and opt(argv, "--bios") == "ovmf"
    assert opt(argv, "--efidisk0") == "local-lvm:1,efitype=4m,pre-enrolled-keys=0"
    assert opt(argv, "--sata0") == "local-lvm:100" and opt(argv, "--serial0") == "socket"
    assert opt(argv, "--cores") == "8" and opt(argv, "--memory") == "16384" and opt(argv, "--cpu") == "host"
    assert opt(argv, "--tags") == STELA_VM_TAG and "--net0" not in argv
    assert opt(argv, "--hookscript") == "local:snippets/osx-next-stela-478.sh"


def test_without_a_gpu_the_display_is_std_or_the_chosen_one():
    assert opt(stela_vm_options(cfg()), "--vga") == "std"
    assert opt(stela_vm_options(cfg(vga="none")), "--vga") == "none"


def test_a_gpu_is_passed_with_every_function_and_the_display_is_off():
    steps = build_stela_plan(cfg(gpu_pci="0000:0a:00"))
    assert opt(by_title(steps, "Create the VM").argv, "--vga") == "none"
    gpu = by_title(steps, "Pass the GPU to the VM")
    assert gpu.argv == ["qm", "set", "478", "--hostpci0", "0000:0a:00,pcie=1,x-vga=1"] and gpu.risk == "warn"
    assert [s.title for s in steps][-2:] == ["Pass the GPU to the VM", "Start the VM"]


def test_the_qemu_args_give_a_usb_keyboard_the_stick_and_a_pcie_nic_on_its_own_tap():
    args = stela_qemu_args(cfg())
    assert "-device qemu-xhci,id=xhc1 -device usb-kbd,bus=xhc1.0" in args
    assert f"-drive if=none,id=stick,format=raw,file={stick_path(478)}" in args
    assert "-device usb-storage,bus=xhc1.0,drive=stick,bootindex=0" in args
    assert "-netdev tap,id=sn0,ifname=tap478x,script=no,downscript=no" in args
    assert "-device pcie-root-port,id=rpn,chassis=20,slot=20,bus=pcie.0" in args
    assert f"-device e1000e,netdev=sn0,bus=rpn,mac={mac_for(478)}" in args
    assert args.startswith("-cpu host,+invtsc")


def test_the_nic_mac_and_tap_come_from_the_vmid():
    assert mac_for(478) == "BC:24:11:00:01:DE" and mac_for(999999) == "BC:24:11:0F:42:3F"
    assert tap_for(478) == "tap478x" and len(tap_for(999999)) <= 15


def test_the_hook_script_makes_the_tap_before_start_and_removes_it_after_stop():
    script = hook_script(478, "vmbr1")
    assert script.startswith("#!/bin/bash")
    assert "pre-start)" in script and "ip tuntap add dev tap478x mode tap" in script
    assert "ip link set tap478x master vmbr1" in script
    assert "post-stop)" in script and "ip link del tap478x" in script
    write = by_title(build_stela_plan(cfg(bridge="vmbr1")), "Write the VM's hook script").argv
    assert write[4] == script and write[5] == hook_path(478) and "chmod 755" in write[2]


def test_an_invalid_config_is_refused_before_any_step_is_built():
    with pytest.raises(ValueError, match="VMID"):
        build_stela_plan(cfg(vmid=5))


@pytest.mark.parametrize("change,word", [
    (dict(vmid=5), "VMID"), (dict(name="a;b"), "VM name"), (dict(storage="x y"), "Storage"),
    (dict(cores=2), "CPU cores"), (dict(memory_mb=4096), "RAM"), (dict(disk_gb=10), "disk"),
    (dict(bridge="br0"), "Bridge"), (dict(gpu_pci="0a;00"), "GPU"), (dict(vga="qxl"), "Display"),
    (dict(stela_sha256="abc"), "checksum"), (dict(stela_url="file:///etc/passwd"), "download folder"),
    (dict(stela_url="http://h/a b"), "download folder"),
])
def test_every_value_that_reaches_qm_or_a_shell_is_checked(change, word):
    issues = validate_stela_config(cfg(**change))
    assert len(issues) == 1 and word in issues[0]


@pytest.mark.parametrize("pci", ["0a:00", "0000:0a:00", "0000:0a:00.0", "0a:00.1"])
def test_gpu_addresses_with_or_without_a_function_pass(pci):
    assert validate_stela_config(cfg(gpu_pci=pci)) == []


def test_the_default_config_is_valid():
    assert validate_stela_config(cfg()) == []


def test_the_destroy_plan_checks_the_tag_purges_and_removes_every_file_and_the_tap():
    steps = build_stela_destroy_plan(478)
    assert [s.title for s in steps] == [
        "Check the VM was made by this wizard", "Stop VM", "Destroy VM", "Remove the boot stick and hook script",
        "Remove the VM's tap"]
    assert steps[0].argv[-2:] == ["478", STELA_VM_TAG]
    assert steps[2].argv == ["qm", "destroy", "478", "--purge"]
    assert steps[3].argv == ["rm", "-f", stick_path(478), hook_path(478)]
    assert steps[4].argv[-1] == "tap478x"


def test_the_destroy_plan_refuses_a_bad_vmid():
    with pytest.raises(ValueError, match="VMID"):
        build_stela_destroy_plan(1)


def test_the_finish_plan_checks_the_tag_stops_adds_the_gpu_with_no_display_and_starts():
    steps = build_stela_finish_plan(478, "0000:0a:00")
    assert [s.title for s in steps] == [
        "Check the VM was made by this wizard", "Stop the VM", "Pass the GPU to the VM", "Start the VM"]
    assert steps[0].argv[-2:] == ["478", STELA_VM_TAG]
    assert steps[2].argv == ["qm", "set", "478", "--vga", "none", "--hostpci0", "0000:0a:00,pcie=1,x-vga=1"]
    assert steps[2].risk == "warn" and steps[3].argv == ["qm", "start", "478"]


@pytest.mark.parametrize("vmid,gpu", [(5, "0a:00"), (478, "0a;00"), (478, "")])
def test_the_finish_plan_refuses_a_bad_vmid_or_gpu(vmid, gpu):
    with pytest.raises(ValueError, match="Invalid finish request"):
        build_stela_finish_plan(vmid, gpu)


# ── the scripts themselves, run against fakes ─────────────────────────────────────────────────────────────

import hashlib
import os
import subprocess
import tarfile


def run_script(step, env_dir, *, bash_args=None):
    argv = bash_args or step.argv
    return subprocess.run(argv, capture_output=True, text=True, env={"PATH": f"{env_dir}:{os.environ['PATH']}"})


def fake_curl(tmp_path, sums_text, tarball: bytes):
    """A curl that serves SHA256SUMS and the tarball from memory, so no network is touched."""
    (tmp_path / "SUMS").write_text(sums_text)
    (tmp_path / "TAR").write_bytes(tarball)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text("#!/bin/bash\nout=\"\"\nwhile [ $# -gt 0 ]; do case $1 in -o) out=$2; shift;; http*) url=$1;; esac; shift; done\n"
                    f"case $url in */SHA256SUMS) cp {tmp_path}/SUMS $out;; *) cp {tmp_path}/TAR $out;; esac\n")
    curl.chmod(0o755)
    return bindir


def make_tarball(tmp_path):
    root = tmp_path / "src" / "stela-v0.5.0-linux-x86_64"
    root.mkdir(parents=True)
    (root / "stela").write_text("#!/bin/sh\n")
    (root / "stela").chmod(0o755)
    tar = tmp_path / "t.tar.gz"
    with tarfile.open(tar, "w:gz") as t:
        t.add(root, arcname="stela-v0.5.0-linux-x86_64")
    return tar.read_bytes()


def download_argv(tmp_path, sha, listed):
    data = make_tarball(tmp_path)
    real = hashlib.sha256(data).hexdigest()
    bindir = fake_curl(tmp_path, f"{listed or real}  {STELA_ASSET}\n", data)
    step = by_title(build_stela_plan(cfg(stela_sha256=sha or real)), "Download and check Stela")
    argv = list(step.argv)
    argv[7] = str(tmp_path / "dl")                        # the folder it works in
    argv[8] = str(tmp_path / "dl" / "x" / "stela-v0.5.0-linux-x86_64" / "stela")
    return bindir, argv, real


def test_the_download_script_accepts_the_pinned_tarball_and_unpacks_it(tmp_path):
    bindir, argv, real = download_argv(tmp_path, None, None)
    result = run_script(None, bindir, bash_args=argv)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "dl" / "x" / "stela-v0.5.0-linux-x86_64" / "stela").exists()


def test_the_download_script_refuses_a_release_whose_sums_list_another_checksum(tmp_path):
    bindir, argv, real = download_argv(tmp_path, None, "f" * 64)
    result = run_script(None, bindir, bash_args=argv)
    assert result.returncode == 1 and "refusing it" in result.stderr
    assert not (tmp_path / "dl" / "x").exists()


def test_the_download_script_refuses_a_tarball_that_does_not_hash_to_the_pin(tmp_path):
    bindir, argv, real = download_argv(tmp_path, "e" * 64, "e" * 64)     # the sums agree with the pin, the file does not
    result = run_script(None, bindir, bash_args=argv)
    assert result.returncode != 0 and not (tmp_path / "dl" / "x").exists()


def test_the_free_vmid_check_refuses_an_existing_vm(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    qm = bindir / "qm"
    qm.write_text("#!/bin/bash\nexit 0\n")
    qm.chmod(0o755)
    step = by_title(build_stela_plan(cfg()), "Check the VMID is free")
    result = run_script(step, bindir)
    assert result.returncode == 1 and "already exists" in result.stderr
    qm.write_text("#!/bin/bash\nexit 2\n")
    assert run_script(step, bindir).returncode == 0
