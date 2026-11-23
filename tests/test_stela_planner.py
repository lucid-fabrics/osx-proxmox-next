"""The Stela VM plan and config: qm and shell steps, the VM's QEMU arguments, and nothing spliced into a shell."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from osx_proxmox_next.stela import (
    STELA_COMMIT,
    STELA_FORKS,
    STELA_VM_TAG,
    StelaConfig,
    mac_for,
    tap_for,
    validate_stela_config,
)
from osx_proxmox_next.stela_planner import (
    BUILD_SCRIPT,
    BUILD_TIMEOUT,
    STICK_DIR,
    STICK_GB,
    build_stela_destroy_plan,
    build_stela_finish_plan,
    build_stela_plan,
    hook_path,
    hook_script,
    stela_binary,
    stela_dir,
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


def test_the_pin_is_a_commit_sha_and_the_forks_are_plain_github_urls_in_order():
    assert STELA_COMMIT == "cd732c12f6c4d0fdfb5fffba2f68c09f2bbf21fc"
    assert [u.split("/")[3] for u in STELA_FORKS] == ["InSnh-Gd", "xiloe85", "AnthoDingo", "rschulzgit"]
    assert all(u.startswith("https://github.com/") and u.endswith("/Stela.git") for u in STELA_FORKS)
    assert not any("tylertms" in u for u in STELA_FORKS)     # the author withdrew it on 2026-11-22


def test_the_plan_checks_builds_makes_the_stick_then_the_vm_and_starts_it_last():
    steps = build_stela_plan(cfg())
    assert [s.title for s in steps] == [
        "Check the VMID is free", "Check snippet storage", "Fetch and build Stela", "Make the boot stick", "Write the VM's hook script",
        "Create the VM", "Start the VM"]
    assert steps[-1].argv == ["qm", "start", "478"]


def test_the_build_step_carries_the_commit_the_cache_the_storage_and_the_forks_in_order():
    step = by_title(build_stela_plan(cfg()), "Fetch and build Stela")
    assert step.argv == ["bash", BUILD_SCRIPT, "host", STELA_COMMIT, stela_dir(STELA_COMMIT), "local-lvm", "vmbr0", *STELA_FORKS]
    assert step.timeout == BUILD_TIMEOUT and BUILD_TIMEOUT >= 4 * 3600
    assert stela_dir(STELA_COMMIT) == f"{STICK_DIR}/stela-cd732c12f6c4"
    assert stela_binary(STELA_COMMIT) == f"{STICK_DIR}/stela-cd732c12f6c4/stela"
    assert Path(BUILD_SCRIPT).is_file() and os.access(BUILD_SCRIPT, os.X_OK)


def test_a_mirror_is_tried_first_and_the_pin_stays():
    argv = by_title(build_stela_plan(cfg(stela_url="http://127.0.0.1:8915/Stela.git")), "Fetch and build Stela").argv
    assert argv[3] == STELA_COMMIT and argv[7:] == ["http://127.0.0.1:8915/Stela.git", *STELA_FORKS]


def test_the_stick_is_a_32_gb_image_written_by_stela_online_with_no_wifi():
    argv = by_title(build_stela_plan(cfg()), "Make the boot stick").argv
    assert argv[4:] == [stela_binary(STELA_COMMIT), stick_path(478), str(STICK_GB)]
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
    (dict(stela_commit="abc"), "commit"), (dict(stela_commit="A" * 40), "commit"),
    (dict(stela_url="file:///etc/passwd"), "mirror"), (dict(stela_url="http://h/a b"), "mirror"),
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

import subprocess


def run_script(step, env_dir, *, bash_args=None):
    argv = bash_args or step.argv
    return subprocess.run(argv, capture_output=True, text=True, env={"PATH": f"{env_dir}:{os.environ['PATH']}"})


def fake_pve(tmp_path, *, exec_rc=0):
    """pct, pvesh and pveam that record what they are asked and build nothing: the host flow runs with no Proxmox."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.txt"
    (bindir / "pvesh").write_text('#!/bin/bash\necho \'"4242"\'\n')
    (bindir / "pveam").write_text("#!/bin/bash\n[ \"$1\" = list ] && echo 'local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst 120MB'\nexit 0\n")
    (bindir / "pct").write_text(
        f'#!/bin/bash\necho "$@" >> {calls}\n'
        'case $1 in\n'
        f'  exec) case "$*" in *build-stela.sh*) exit {exec_rc};; esac;;\n'
        f'  pull) case $3 in */BUILD-INFO) echo "commit {STELA_COMMIT}" > $4;; *) echo built > $4;; esac;;\n'
        'esac\nexit 0\n')
    for f in bindir.iterdir():
        f.chmod(0o755)
    return bindir, calls


def host_argv(cache, *extra):
    return ["bash", BUILD_SCRIPT, "host", STELA_COMMIT, str(cache), "local-lvm", "vmbr0", *extra, *STELA_FORKS]


def test_the_host_build_makes_a_container_builds_in_it_copies_the_result_and_destroys_the_container(tmp_path):
    bindir, calls = fake_pve(tmp_path)
    cache = tmp_path / "cache"
    result = run_script(None, bindir, bash_args=host_argv(cache))
    assert result.returncode == 0, result.stderr
    assert (cache / "stela").read_text() == "built\n" and os.access(cache / "stela", os.X_OK)
    assert (cache / "stela.efi").exists() and f"commit {STELA_COMMIT}" in (cache / "BUILD-INFO").read_text()
    log = calls.read_text().splitlines()
    create = next(line for line in log if line.startswith("create"))
    assert "--unprivileged 1" in create and "--rootfs local-lvm:90" in create and "bridge=vmbr0" in create
    run = next(line for line in log if line.startswith("exec 4242") and "build-stela.sh" in line).split()
    assert run[-len(STELA_FORKS) - 2:] == [STELA_COMMIT, "/out", *STELA_FORKS]
    assert log[-1].startswith("destroy 4242") and (cache / "build.log").exists()


def test_a_failed_build_still_destroys_the_container_and_caches_nothing(tmp_path):
    bindir, calls = fake_pve(tmp_path, exec_rc=1)
    cache = tmp_path / "cache"
    result = run_script(None, bindir, bash_args=host_argv(cache))
    assert result.returncode != 0 and not (cache / "stela").exists()
    assert calls.read_text().splitlines()[-1].startswith("destroy 4242")


def test_a_cached_build_of_the_same_commit_is_used_without_touching_proxmox(tmp_path):
    bindir, calls = fake_pve(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "stela").write_text("x")
    (cache / "stela").chmod(0o755)
    (cache / "stela.efi").write_text("x")
    (cache / "BUILD-INFO").write_text(f"commit {STELA_COMMIT}\n")
    result = run_script(None, bindir, bash_args=host_argv(cache))
    assert result.returncode == 0 and "already built" in result.stdout and not calls.exists()
    (cache / "BUILD-INFO").write_text("commit " + "0" * 40 + "\n")       # another commit's build is not reused
    assert run_script(None, bindir, bash_args=host_argv(cache)).returncode == 0 and calls.exists()


@pytest.mark.parametrize("mode,commit,storage,bridge,forks", [
    ("host", "abc", "local-lvm", "vmbr0", ["https://x/y.git"]),
    ("host", STELA_COMMIT, "a b", "vmbr0", ["https://x/y.git"]),
    ("host", STELA_COMMIT, "local-lvm", "br0", ["https://x/y.git"]),
    ("host", STELA_COMMIT, "local-lvm", "vmbr0", []),
    ("both", STELA_COMMIT, "local-lvm", "vmbr0", ["https://x/y.git"]),
])
def test_the_build_script_refuses_bad_arguments_before_doing_anything(tmp_path, mode, commit, storage, bridge, forks):
    bindir, calls = fake_pve(tmp_path)
    result = run_script(None, bindir, bash_args=["bash", BUILD_SCRIPT, mode, commit, str(tmp_path / "c"), storage, bridge, *forks])
    assert result.returncode == 2 and not calls.exists()


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
