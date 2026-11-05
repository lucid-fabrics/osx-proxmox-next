from __future__ import annotations

import io
import json
import shutil
import sys
from pathlib import Path

import pytest

from gpu_fakes import EFI_GRUB, EFI_SDBOOT, FakeRun, add_card, add_other, make_host, write
from osx_proxmox_next import host_setup as hs


def host(tmp_path: Path, **kw) -> str:
    return str(make_host(tmp_path, **kw))


def target(root: str, reset: str = "early-bind", vendor: str = "amd", bdf: str = "0000:06:00") -> dict:
    return hs.build_target(root, bdf, vendor, reset)


def plan_for(root: str, run=None, tgt=None, opts=None, bootloader: str = "grub") -> dict:
    run = run or FakeRun({"efibootmgr -v": (0, EFI_GRUB if bootloader == "grub" else EFI_SDBOOT)})
    return hs.plan_host(hs.detect_facts(root, run, env={}), tgt or target(root), opts, root, run)


# ── boot loader and facts ────────────────────────────────────────────

def test_bootloader_comes_from_what_booted_not_from_files(tmp_path):
    root = host(tmp_path)
    # pve-ai case: a stale /etc/kernel/cmdline exists, but the host boots GRUB through shim
    write(Path(root), "/etc/kernel/cmdline", "root=ZFS=rpool/ROOT/pve-1 boot=zfs\n")
    write(Path(root), "/etc/kernel/proxmox-boot-uuids", "ABCD-1234\n")
    assert hs.detect_bootloader(root, FakeRun({"efibootmgr -v": (0, EFI_GRUB)})) == "grub"
    assert hs.detect_bootloader(root, FakeRun({"efibootmgr -v": (0, EFI_SDBOOT)})) == "systemd-boot"


def test_bootloader_fallbacks(tmp_path):
    root = host(tmp_path)
    assert hs.detect_bootloader(root, FakeRun()) == "grub"  # BOOT_IMAGE= in /proc/cmdline
    write(Path(root), "/proc/cmdline", "initrd=\\EFI\\proxmox\\6.8\\initrd root=ZFS=x\n")
    assert hs.detect_bootloader(root, FakeRun()) == "unknown"
    assert hs.detect_bootloader(root, FakeRun({"proxmox-boot-tool status": (0, "configured with: uefi (versions)")})) == "systemd-boot"
    assert hs.detect_bootloader(root, FakeRun({"proxmox-boot-tool status": (0, "configured with: grub")})) == "grub"
    assert hs.detect_bootloader(root, FakeRun({"proxmox-boot-tool status": (0, "configured with:\n")})) == "unknown"
    assert hs.detect_bootloader(host(tmp_path / "bios", efi=False), FakeRun()) == "grub"


def test_facts(tmp_path):
    root = host(tmp_path, cpu="amd", virt=False, tables=("IVRS",), groups=2)
    add_card(Path(root), boot_vga=True)
    f = hs.detect_facts(root, FakeRun({"mokutil --sb-state": (0, "SecureBoot enabled"), "pveversion": (0, "pve-manager/9.2.24/x\nmore")}),
                        env={"SSH_CONNECTION": "1.2.3.4 1 5.6.7.8 22"})
    assert (f["cpu_vendor"], f["virtualization"], f["ivrs"], f["dmar"], f["iommu_groups"]) == ("amd", False, True, False, 3)
    assert f["secure_boot"] and f["ssh"] and f["pve"] == "pve-manager/9.2.24/x"
    assert f["board_vendor"] == "ASUSTeK COMPUTER INC." and f["efi"]
    assert [d["bdf"] for d in f["displays"]] == ["0000:06:00.0"] and f["displays"][0]["boot_vga"] is True
    odd = hs.detect_facts(host(tmp_path / "x", cpu="intel"), FakeRun(), env={})
    assert odd["secure_boot"] is False and odd["ssh"] is False and odd["pve"] == ""
    (Path(root) / "proc/cpuinfo").write_text("model name: x\n")
    assert hs.detect_facts(root, FakeRun(), env={})["cpu_vendor"] == "unknown"
    assert hs.pci_devices(str(tmp_path / "nowhere")) == []


def test_facts_report_bars_the_kernel_could_not_place(tmp_path):
    root = host(tmp_path)
    dmesg = ("[    1.2] pci 0000:06:00.0: BAR 1: no space for [mem size 0x200000000 64bit pref]\n"
             "[    1.2] pci 0000:06:00.0: BAR 1: failed to assign [mem size 0x200000000 64bit pref]\n"
             "[    1.3] pci 0000:07:00.0: BAR 0: assigned [mem 0xfb000000-0xfb00ffff]\n"
             "[    1.4] pci 0000:41:00.1: BAR 0: no space for [mem size 0x4000]\n")
    facts = hs.detect_facts(root, FakeRun({"dmesg": (0, dmesg)}), env={})
    assert facts["bar_errors"] == ["0000:06:00.0", "0000:41:00.1"]
    assert hs.detect_facts(root, FakeRun(), env={})["bar_errors"] == []


# ── kernel arguments ─────────────────────────────────────────────────

def test_grub_args_intel_and_amd_never_write_amd_iommu(tmp_path):
    root = host(tmp_path, cpu="intel")
    add_card(Path(root))
    plan = plan_for(root)
    ch = next(c for c in plan["changes"] if c["path"] == hs.GRUB_DROPIN)
    assert 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT intel_iommu=on iommu=pt"' in ch["new"]
    assert ["update-grub"] in [c["argv"] for c in plan["commands"]] and plan["reboot_required"]
    amd_root = host(tmp_path / "amd", cpu="amd")
    add_card(Path(amd_root))
    ch = next(c for c in plan_for(amd_root)["changes"] if c["path"] == hs.GRUB_DROPIN)
    assert "iommu=pt" in ch["new"] and "amd_iommu" not in ch["new"] and "intel_iommu" not in ch["new"]


def test_sysfb_only_when_the_card_is_the_console_gpu(tmp_path):
    root = host(tmp_path)
    add_card(Path(root), boot_vga=True)
    add_other(Path(root), "0000:00:02.0", 0x8086, 0x1234, 0x030000, 3, driver="i915")
    plan = plan_for(root)
    assert "initcall_blacklist=sysfb_init" in next(c for c in plan["changes"] if c["path"] == hs.GRUB_DROPIN)["new"]
    assert any("console GPU" in n for n in plan["notes"]) and any("goes dark" in w for w in plan["warnings"])
    root2 = host(tmp_path / "b")
    add_card(Path(root2))
    assert "sysfb" not in next(c for c in plan_for(root2)["changes"] if c["path"] == hs.GRUB_DROPIN)["new"]


def test_only_display_adapter_is_refused_until_confirmed(tmp_path):
    root = host(tmp_path)
    add_card(Path(root), boot_vga=True)
    plan = plan_for(root)
    assert any("only display adapter" in r for r in plan["refusals"])
    ok = plan_for(root, opts={"allow_no_console": True})
    assert not ok["refusals"] and any("Rescue" in w for w in ok["warnings"])
    assert any("SSH" in w for w in ok["warnings"])  # no SSH_CONNECTION in the fake environment
    facts = hs.detect_facts(root, FakeRun(), env={"SSH_CONNECTION": "x"})
    quiet = hs.plan_host(facts, target(root), {"allow_no_console": True}, root, FakeRun())
    assert not any("not on an SSH" in w for w in quiet["warnings"])


def test_args_already_set_or_pending(tmp_path):
    root = host(tmp_path, cmdline="BOOT_IMAGE=/x ro intel_iommu=on iommu=pt")
    add_card(Path(root))
    assert "Kernel arguments are already set" in plan_for(root)["skipped"]
    # configured in the grub file but not live: a reboot note, nothing to write
    root2 = host(tmp_path / "p")
    add_card(Path(root2))
    write(Path(root2), "/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt"\n')
    plan = plan_for(root2)
    assert not [c for c in plan["changes"] if c["path"] == hs.GRUB_DROPIN]
    assert any("not active until you reboot" in n for n in plan["notes"])


def test_existing_own_dropin_tokens_are_kept_and_bad_args_noted(tmp_path):
    root = host(tmp_path, cmdline="BOOT_IMAGE=/x ro amd_iommu=on pcie_acs_override=downstream")
    add_card(Path(root))
    write(Path(root), hs.GRUB_DROPIN, 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT video=efifb:off"\n')
    plan = plan_for(root)
    new = next(c for c in plan["changes"] if c["path"] == hs.GRUB_DROPIN)["new"]
    assert "video=efifb:off intel_iommu=on iommu=pt" in new
    assert any("amd_iommu=on is not a valid value" in n for n in plan["notes"])
    assert any("pcie_acs_override" in n and "weakens isolation" in n for n in plan["notes"])
    assert "amd_iommu" not in new and "acs_override" not in new


def test_systemd_boot_edits_the_one_line_file(tmp_path):
    root = host(tmp_path, cmdline="initrd=\\EFI\\proxmox\\x root=ZFS=rpool/ROOT/pve-1 boot=zfs")
    add_card(Path(root))
    write(Path(root), "/etc/kernel/cmdline", "root=ZFS=rpool/ROOT/pve-1 boot=zfs\n")
    plan = plan_for(root, bootloader="sdboot")
    ch = next(c for c in plan["changes"] if c["path"] == hs.KERNEL_CMDLINE)
    assert ch["new"] == "root=ZFS=rpool/ROOT/pve-1 boot=zfs intel_iommu=on iommu=pt\n"
    assert ["proxmox-boot-tool", "refresh"] in [c["argv"] for c in plan["commands"]]
    (Path(root) / "etc/kernel/cmdline").unlink()
    refused = plan_for(root, bootloader="sdboot")
    assert any("/etc/kernel/cmdline is missing" in r for r in refused["refusals"])


def test_unknown_bootloader_gives_manual_instructions(tmp_path):
    root = host(tmp_path, cmdline="initrd=\\x root=/dev/x")
    add_card(Path(root))
    plan = plan_for(root, run=FakeRun())
    assert any("Add these arguments" in m for m in plan["manual"]) and plan["reboot_required"]
    assert not [c for c in plan["changes"] if c["path"] in (hs.GRUB_DROPIN, hs.KERNEL_CMDLINE)]


# ── modules and binding ──────────────────────────────────────────────

def test_vfio_modules(tmp_path):
    root = host(tmp_path)
    add_card(Path(root))
    plan = plan_for(root)
    ch = next(c for c in plan["changes"] if c["path"] == hs.MODULES_FILE)
    assert [ln for ln in ch["new"].splitlines() if not ln.startswith("#")] == ["vfio", "vfio_iommu_type1", "vfio_pci"]
    assert "vfio_virqfd" not in ch["new"]
    write(Path(root), "/etc/modules", "vfio\nvfio_iommu_type1\nvfio_pci\nvfio_virqfd\n")
    done = plan_for(root)
    assert "VFIO modules are already listed" in done["skipped"]
    assert any("vfio_virqfd" in n and "harmless" in n for n in done["notes"])
    write(Path(root), "/etc/modules", "")
    write(Path(root), "/etc/modules-load.d/vfio.conf", "vfio-pci\nvfio\nvfio_iommu_type1  # comment\n")
    assert "VFIO modules are already listed" in plan_for(root)["skipped"]


def test_binding_by_id_writes_ids_softdeps_and_initramfs(tmp_path):
    root = host(tmp_path)
    add_card(Path(root))
    plan = plan_for(root)
    body = next(c for c in plan["changes"] if c["path"] == hs.MODPROBE_FILE)["new"]
    assert "options vfio-pci ids=1002:73ff,1002:ab28 disable_vga=1" in body
    assert "softdep amdgpu pre: vfio-pci" in body and "softdep radeon pre: vfio-pci" in body
    assert plan["binding"] == "ids" and ["update-initramfs", "-u", "-k", "all"] in [c["argv"] for c in plan["commands"]]
    assert plan["commands"][-1]["argv"][0] == "update-initramfs"


def test_binding_merges_into_the_existing_ids_line(tmp_path):
    root = host(tmp_path)
    add_card(Path(root))
    write(Path(root), "/etc/modprobe.d/vfio.conf", "options vfio-pci ids=10de:2504,10de:228e disable_vga=1\n")
    plan = plan_for(root)
    merged = next(c for c in plan["changes"] if c["path"] == "/etc/modprobe.d/vfio.conf")["new"]
    assert merged == "options vfio-pci ids=10de:2504,10de:228e,1002:73ff,1002:ab28 disable_vga=1\n"
    ours = next(c for c in plan["changes"] if c["path"] == hs.MODPROBE_FILE)["new"]
    assert "ids=" not in ours and "softdep amdgpu" in ours
    write(Path(root), "/etc/modprobe.d/vfio.conf", "options vfio-pci ids=1002:73ff,1002:ab28\n")
    again = plan_for(root)
    assert any("already in your vfio-pci options" in n for n in again["notes"])
    assert not [c for c in again["changes"] if c["path"] == "/etc/modprobe.d/vfio.conf"]


def test_identical_card_switches_to_per_device_binding(tmp_path):
    root = host(tmp_path)
    add_card(Path(root), bdf="0000:06:00", group=7)
    add_card(Path(root), bdf="0000:41:00", group=9)
    plan = plan_for(root)
    assert plan["binding"] == "per-device" and any("0000:41:00.0" in n for n in plan["notes"])
    script = next(c for c in plan["changes"] if c["path"] == hs.BIND_SCRIPT)
    assert script["mode"] == 0o755
    assert "bind 0000:06:00.0 0x1002 0x73ff 0x03" in script["new"] and "0000:41:00" not in script["new"]
    assert "ids=" not in next(c for c in plan["changes"] if c["path"] == hs.MODPROBE_FILE)["new"]
    write(Path(root), "/etc/modprobe.d/vfio.conf", "options vfio-pci ids=1002:73ff\n")
    refused = plan_for(root)
    assert any("binds the id" in r for r in refused["refusals"])


def test_usb_c_function_adds_xhci_softdep_and_nvidia_names(tmp_path):
    root = host(tmp_path)
    add_card(Path(root), vendor=0x10DE, device=0x2504, audio=0x228E)
    add_other(Path(root), "0000:06:00.2", 0x10DE, 0x1AD8, 0x0C0330, 7, driver="vfio-pci")
    body = next(c for c in plan_for(root, tgt=target(root, "none", "nvidia"))["changes"] if c["path"] == hs.MODPROBE_FILE)["new"]
    assert "softdep nouveau pre: vfio-pci" in body and "softdep xhci_pci pre: vfio-pci" in body
    assert "softdep i2c_nvidia_gpu pre: vfio-pci" in body and "amdgpu" not in body


# ── vendor-reset ─────────────────────────────────────────────────────

def test_vendor_reset_plan(tmp_path):
    root = host(tmp_path)
    add_card(Path(root), device=0x67DF)
    tgt = target(root, "vendor-reset")
    plan = plan_for(root, tgt=tgt)
    argvs = [c["argv"] for c in plan["commands"]]
    apt = next(a for a in argvs if a[0] == "apt-get")
    assert apt[:4] == ["apt-get", "install", "-y", "dkms"] and any(a.startswith("proxmox-headers-") for a in apt)
    shell = [a[2] for a in argvs if a[0] == "bash"]
    assert hs.VR_SHA in shell[0] and "dkms install -m vendor-reset" in shell[1]
    assert argvs.index(apt) < argvs.index(["update-initramfs", "-u", "-k", "all"])
    paths = {c["path"]: c["new"] for c in plan["changes"]}
    assert paths[hs.VR_MODULES_FILE].endswith("vendor-reset\n")
    assert paths[hs.INITRAMFS_MODULES] == "vendor-reset\n"
    assert 'ATTR{device}=="0x67df"' in paths[hs.VR_UDEV_FILE] and "device_specific" in paths[hs.VR_UDEV_FILE]
    assert not plan["hard_warnings"]


def test_vendor_reset_already_built_and_secure_boot(tmp_path):
    root = host(tmp_path)
    add_card(Path(root), device=0x67DF)
    kernel = hs.detect_facts(root, FakeRun(), env={})["kernel"]
    built = FakeRun({"efibootmgr -v": (0, EFI_GRUB), "dkms status": (0, f"vendor-reset/0.1.1, {kernel}, x86_64: installed")})
    plan = plan_for(root, run=built, tgt=target(root, "vendor-reset"))
    assert "vendor-reset is already built for this kernel" in plan["skipped"]
    assert not [c for c in plan["commands"] if c["argv"][0] in ("apt-get", "bash")]
    write(Path(root), hs.INITRAMFS_MODULES, "# mods\nvendor-reset\n")
    assert hs.INITRAMFS_MODULES not in [c["path"] for c in plan_for(root, run=built, tgt=target(root, "vendor-reset"))["changes"]]
    sb = FakeRun({"efibootmgr -v": (0, EFI_GRUB), "mokutil --sb-state": (0, "SecureBoot enabled")})
    plan = plan_for(root, run=sb, tgt=target(root, "vendor-reset"))
    assert any("mokutil --import" in m for m in plan["manual"])
    assert not [c for c in plan["commands"] if c["argv"][0] == "apt-get"]
    assert hs.VR_MODULES_FILE not in [c["path"] for c in plan["changes"]]


def test_radeon_vii_needs_a_hard_warning_confirmation(tmp_path):
    root = host(tmp_path)
    add_card(Path(root), device=0x66AF)
    plan = plan_for(root, tgt=target(root, "vendor-reset-warn"))
    assert plan["hard_warnings"] and "issue 112" in plan["hard_warnings"][0]
    state = hs.new_state()
    res = hs.apply_plan(plan, state, root, FakeRun(), confirmed=False)
    assert not res["ok"] and "typed confirmation" in res["error"] and not (Path(root) / hs.GRUB_DROPIN.lstrip("/")).exists()


def test_early_bind_family_skips_vendor_reset(tmp_path):
    root = host(tmp_path)
    add_card(Path(root))
    assert not [c for c in plan_for(root)["commands"] if c["argv"][0] == "apt-get"]


# ── apply, rollback, undo ────────────────────────────────────────────

def test_apply_writes_backs_up_and_records(tmp_path):
    root = host(tmp_path)
    add_card(Path(root))
    write(Path(root), "/etc/modprobe.d/vfio.conf", "options vfio-pci ids=10de:2504\n")
    plan = plan_for(root)
    state, run = hs.new_state(), FakeRun(default=(0, ""))
    res = hs.apply_plan(plan, state, root, run)
    assert res["ok"] and res["reboot_required"]
    assert (Path(root) / "etc/default/grub.d/osx-proxmox-next.cfg").read_text().startswith(hs.HEADER)
    assert (Path(root) / hs.BIND_SCRIPT.lstrip("/")).exists() is False  # ids mode, no per-device script
    changed = {e["path"]: e for e in state["host_changes"]}
    old = changed["/etc/modprobe.d/vfio.conf"]
    assert old["existed"] and Path(old["backup"]).read_text() == "options vfio-pci ids=10de:2504\n"
    assert old["sha_before"] != old["sha_after"] and not changed[hs.GRUB_DROPIN]["existed"]
    assert json.loads((Path(root) / hs.STATE_FILE.lstrip("/")).read_text())["host_changes"]
    assert run.ran("update-grub") and run.calls[-1] == ["update-initramfs", "-u", "-k", "all"]
    assert Path(res["backup_dir"]).is_dir()


def test_per_device_script_is_executable(tmp_path):
    root = host(tmp_path)
    add_card(Path(root), bdf="0000:06:00", group=7)
    add_card(Path(root), bdf="0000:41:00", group=9)
    assert hs.apply_plan(plan_for(root), hs.new_state(), root, FakeRun(default=(0, "")))["ok"]
    script = Path(root) / hs.BIND_SCRIPT.lstrip("/")
    assert script.stat().st_mode & 0o111 and script.read_text().startswith("#!/bin/sh")


def test_failed_command_rolls_every_file_back(tmp_path):
    root = host(tmp_path)
    add_card(Path(root))
    write(Path(root), "/etc/modprobe.d/vfio.conf", "options vfio-pci ids=10de:2504\n")
    plan = plan_for(root)
    run = FakeRun({"update-grub": (1, "grub exploded")})
    state = hs.new_state()
    res = hs.apply_plan(plan, state, root, run)
    assert not res["ok"] and res["rolled_back"] and "grub exploded" in res["error"]
    assert (Path(root) / "etc/modprobe.d/vfio.conf").read_text() == "options vfio-pci ids=10de:2504\n"
    assert not (Path(root) / "etc/default/grub.d/osx-proxmox-next.cfg").exists()
    assert not (Path(root) / hs.MODPROBE_FILE.lstrip("/")).exists()
    assert run.calls[-3:] == [["update-grub"], ["update-grub"], ["update-initramfs", "-u", "-k", "all"]] or run.ran("update-initramfs")
    assert all(e["reverted"] for e in state["host_changes"])


def test_failed_write_rolls_back(tmp_path, monkeypatch):
    root = host(tmp_path)
    add_card(Path(root))
    plan = plan_for(root)
    real, calls = hs._write_file, []

    def flaky(r, path, content, mode):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("disk full")
        real(r, path, content, mode)

    monkeypatch.setattr(hs, "_write_file", flaky)
    res = hs.apply_plan(plan, hs.new_state(), root, FakeRun())
    assert not res["ok"] and res["rolled_back"] and "disk full" in res["error"]
    assert not (Path(root) / "etc/default/grub.d/osx-proxmox-next.cfg").exists()


def test_refusals_block_apply(tmp_path):
    root = host(tmp_path)
    add_card(Path(root), boot_vga=True)
    res = hs.apply_plan(plan_for(root), hs.new_state(), root, FakeRun())
    assert not res["ok"] and "only display adapter" in res["error"] and not res["rolled_back"]


def test_undo_restores_in_reverse_and_is_idempotent(tmp_path):
    root = host(tmp_path)
    add_card(Path(root), device=0x67DF)
    write(Path(root), "/etc/modprobe.d/vfio.conf", "options vfio-pci ids=10de:2504\n")
    state = hs.new_state()
    assert hs.apply_plan(plan_for(root, tgt=target(root, "vendor-reset")), state, root, FakeRun(default=(0, "")))["ok"]
    state["vendor_reset_installed"] = True
    run = FakeRun(default=(0, ""))
    res = hs.undo_host(state, root, run)
    assert res["ok"] and res["reboot_required"] and "/etc/default/grub.d/osx-proxmox-next.cfg" in res["restored"]
    assert (Path(root) / "etc/modprobe.d/vfio.conf").read_text() == "options vfio-pci ids=10de:2504\n"
    assert not (Path(root) / hs.VR_UDEV_FILE.lstrip("/")).exists()
    assert run.ran("dkms", "remove") and run.ran("update-grub") and run.ran("update-initramfs")
    again = hs.undo_host(state, root, FakeRun())
    assert again["ok"] and again["restored"] == [] and not again["reboot_required"]


def test_undo_systemd_boot_and_dkms_failure(tmp_path):
    root = host(tmp_path, cmdline="initrd=\\x root=ZFS=r")
    add_card(Path(root))
    write(Path(root), "/etc/kernel/cmdline", "root=ZFS=r\n")
    run = FakeRun({"efibootmgr -v": (0, EFI_SDBOOT)}, default=(0, ""))
    state = hs.new_state()
    assert hs.apply_plan(plan_for(root, run=run), state, root, run)["ok"]
    state["vendor_reset_installed"] = True
    fail = FakeRun({"dkms remove": (1, "no such module"), "proxmox-boot-tool refresh": (1, "no esp")}, default=(0, ""))
    res = hs.undo_host(state, root, fail)
    assert not res["ok"] and len(res["errors"]) == 2 and state["vendor_reset_installed"] is True
    assert (Path(root) / "etc/kernel/cmdline").read_text() == "root=ZFS=r\n"


# ── verify ───────────────────────────────────────────────────────────

def ready_host(tmp_path: Path, **card) -> tuple[str, dict]:
    root = host(tmp_path, cmdline="BOOT_IMAGE=/x ro intel_iommu=on iommu=pt")
    add_card(Path(root), **card)
    write(Path(root), "/proc/modules", "vfio 1 0 - Live 0x0\nvfio_iommu_type1 1 0 - Live 0x0\nvfio_pci 1 0 - Live 0x0\n")
    return root, target(root)


def status(checks: list[dict]) -> dict[str, str]:
    return {c["id"]: c["status"] for c in checks}


def test_verify_all_good(tmp_path):
    root, tgt = ready_host(tmp_path)
    run = FakeRun({"dmesg": (0, "DMAR-IR: Enabled IRQ remapping in x2apic mode")})
    checks = hs.verify_host(tgt, root, run)
    st = status(checks)
    assert {k: v for k, v in st.items() if v == "fail"} == {} and st["irq"] == "ok" and st["bar"] == "info"
    assert "reset" not in st


def test_verify_failures_point_to_a_fix(tmp_path):
    root, tgt = ready_host(tmp_path, driver="amdgpu")
    write(Path(root), "/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="quiet iommu=pt intel_iommu=on"\n')
    write(Path(root), "/proc/cmdline", "BOOT_IMAGE=/x ro\n")
    (Path(root) / "proc/modules").write_text("")
    shutil.rmtree(Path(root) / "sys/kernel/iommu_groups")
    (Path(root) / "sys/kernel/iommu_groups").mkdir()
    checks = hs.verify_host(tgt, root, FakeRun({"dmesg": (0, "nothing")}))
    by = {c["id"]: c for c in checks}
    assert by["cmdline"]["status"] == "fail" and "Reboot the host" in by["cmdline"]["detail"]
    assert by["iommu"]["status"] == "fail" and by["iommu"]["fix"] == "host"  # DMAR table is there
    assert by["driver"]["status"] == "fail" and "amdgpu" in by["driver"]["detail"]
    assert by["modules"]["status"] == "fail" and by["irq"]["status"] == "warn"
    (Path(root) / "sys/firmware/acpi/tables/DMAR").unlink()
    assert {c["id"]: c for c in hs.verify_host(tgt, root, FakeRun())}["iommu"]["fix"] == "bios"


def test_verify_group_and_extras(tmp_path):
    root, tgt = ready_host(tmp_path, bar0=8 * 1024**3)
    add_other(Path(root), "0000:07:00.0", 0x144D, 0xA808, 0x010802, 7)
    add_other(Path(root), "0000:00:1c.0", 0x8086, 0x1111, 0x060400, 7)
    write(Path(root), "/sys/module/vfio_iommu_type1/parameters/allow_unsafe_interrupts", "Y\n")
    write(Path(root), "/sys/bus/pci/devices/0000:06:00.0/reset_method", "bus\n")
    tgt = dict(tgt, reset="vendor-reset")
    by = {c["id"]: c for c in hs.verify_host(tgt, root, FakeRun())}
    assert by["group"]["status"] == "fail" and by["group"]["fix"] == "acs"
    assert "0000:07:00.0" in by["group"]["detail"] and "storage or network" in by["group"]["detail"]
    assert "never applies it" in by["group"]["detail"]
    assert by["unsafe"]["status"] == "warn" and by["reset"]["status"] == "warn" and by["bar"]["status"] == "warn"
    write(Path(root), "/sys/bus/pci/devices/0000:06:00.0/reset_method", "device_specific bus\n")
    assert {c["id"]: c for c in hs.verify_host(tgt, root, FakeRun())}["reset"]["status"] == "ok"


def test_verify_without_iommu_group(tmp_path):
    root, tgt = ready_host(tmp_path)
    (Path(root) / "sys/bus/pci/devices/0000:06:00.0/iommu_group").unlink()
    by = {c["id"]: c for c in hs.verify_host(tgt, root, FakeRun())}
    assert by["group"]["status"] == "fail" and by["group"]["fix"] == "bios"
    assert hs._bar_size(Path(root) / "nope", 0) is None


# ── state, hint, steps ───────────────────────────────────────────────

def test_state_roundtrip_and_hint(tmp_path):
    root = str(tmp_path)
    assert hs.load_state(root) is None
    state = hs.new_state()
    state["step"] = 3
    state["resume_cmd"] = "osx-next-cli gpu-setup"
    hs.save_state(state, root)
    assert hs.load_state(root)["step"] == 3
    hs.set_login_hint(state, root)
    hint = (tmp_path / "etc/profile.d/osx-proxmox-next-gpu.sh").read_text()
    assert "GPU setup paused at step 3 of 8 (BIOS). Continue with: osx-next-cli gpu-setup" in hint
    assert hs.hint_text({**state, "step": 99, "resume_cmd": ""}).endswith("osx-next-cli gpu-setup")
    hs.clear_login_hint(root)
    hs.clear_login_hint(root)
    assert not (tmp_path / "etc/profile.d/osx-proxmox-next-gpu.sh").exists()
    (tmp_path / hs.STATE_FILE.lstrip("/")).write_text("not json")
    assert hs.load_state(root) is None
    (tmp_path / hs.STATE_FILE.lstrip("/")).write_text("[1]")
    assert hs.load_state(root) is None
    hs.delete_state(root)
    hs.delete_state(root)


def test_hint_survives_quotes(tmp_path):
    state = {**hs.new_state(), "resume_cmd": 'bash -c "$(curl -fsSL https://x/y.sh)" -- gpu-setup'}
    hs.set_login_hint(state, str(tmp_path))
    import subprocess
    out = subprocess.run(["bash", "-c", f". {tmp_path}/etc/profile.d/osx-proxmox-next-gpu.sh; true"], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout == ""  # no tty: nothing printed, nothing executed
    script = (tmp_path / "etc/profile.d/osx-proxmox-next-gpu.sh").read_text().replace("[ -t 1 ] && ", "")
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert res.stdout.strip().endswith('gpu-setup') and "$(curl" in res.stdout


def test_step_helpers():
    s = hs.new_state()
    hs.complete_step(s, 1)
    hs.complete_step(s, 2)
    hs.complete_step(s, 3, skipped=True)
    assert (s["step"], s["done"], s["skipped"]) == (4, [1, 2], [3])
    hs.complete_step(s, 3)
    assert s["skipped"] == [] and s["done"] == [1, 2, 3]
    hs.goto_step(s, 2)
    assert (s["step"], s["done"]) == (2, [1])
    hs.complete_step(s, 8)
    assert s["step"] == 8


def test_default_run_handles_missing_commands():
    assert hs.default_run(["definitely-not-a-command-xyz"]) == (127, "")
    code, out = hs.default_run([sys.executable, "-c", "print('hi')"])
    assert (code, out.strip()) == (0, "hi")
    assert hs.default_run(["sleep", "5"], timeout=0.01) == (127, "")


# ── command line ─────────────────────────────────────────────────────

def run_main(monkeypatch, tmp_path, cmd: str, payload: dict | None = None, capsys=None):
    monkeypatch.setenv("GPU_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload) if payload is not None else ""))
    code = hs.main(["host_setup.py", cmd])
    return code, capsys.readouterr().out


def test_cli_commands(monkeypatch, tmp_path, capsys):
    root = Path(make_host(tmp_path))
    add_card(root)
    code, out = run_main(monkeypatch, tmp_path, "facts", None, capsys)
    assert code == 0 and json.loads(out)["cpu_vendor"] == "intel"
    code, out = run_main(monkeypatch, tmp_path, "target", {"bdf": "0000:06:00", "vendor": "amd"}, capsys)
    tgt = json.loads(out)
    assert code == 0 and len(tgt["functions"]) == 2 and tgt["reset"] == "none"
    code, out = run_main(monkeypatch, tmp_path, "plan", {"target": tgt}, capsys)
    plan = json.loads(out)
    assert any(c["path"] == hs.GRUB_DROPIN for c in plan["changes"])
    code, out = run_main(monkeypatch, tmp_path, "preview", plan, capsys)
    assert "--- /etc/default/grub.d/osx-proxmox-next.cfg" in out and "run: update-grub" in out
    code, out = run_main(monkeypatch, tmp_path, "step", {"step": 1}, capsys)
    code, out = run_main(monkeypatch, tmp_path, "state", None, capsys)
    assert json.loads(out)["done"] == [1] and json.loads(out)["step"] == 2
    run_main(monkeypatch, tmp_path, "step", {"step": 1, "goto": True}, capsys)
    run_main(monkeypatch, tmp_path, "state-save", {"vm": 105}, capsys)
    assert hs.load_state(str(tmp_path))["vm"] == 105
    code, out = run_main(monkeypatch, tmp_path, "verify", {"target": tgt}, capsys)
    assert code == 0 and any(c["id"] == "iommu" for c in json.loads(out))
    run_main(monkeypatch, tmp_path, "hint-set", None, capsys)
    assert (tmp_path / "etc/profile.d/osx-proxmox-next-gpu.sh").exists()
    run_main(monkeypatch, tmp_path, "hint-clear", None, capsys)
    assert not (tmp_path / "etc/profile.d/osx-proxmox-next-gpu.sh").exists()
    assert run_main(monkeypatch, tmp_path, "bogus", None, capsys)[0] == 64


def stub_tools(tmp_path: Path, monkeypatch, *names: str) -> None:
    """Executables that succeed, so the CLI can run its commands without touching the real host."""
    bindir = tmp_path / "stubbin"
    bindir.mkdir(exist_ok=True)
    for name in names:
        (bindir / name).write_text("#!/bin/sh\nexit 0\n")
        (bindir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")


def test_cli_apply_and_undo(monkeypatch, tmp_path, capsys):
    stub_tools(tmp_path, monkeypatch, "update-grub", "update-initramfs")
    root = Path(make_host(tmp_path))
    add_card(root)
    assert run_main(monkeypatch, tmp_path, "undo", None, capsys)[0] == 0  # no state: nothing to do
    plan = plan_for(str(tmp_path), run=FakeRun({"efibootmgr -v": (0, EFI_GRUB)}))
    code, out = run_main(monkeypatch, tmp_path, "apply", {"plan": plan, "confirmed": False}, capsys)
    assert code == 0 and json.loads(out)["ok"]
    assert (tmp_path / "etc/default/grub.d/osx-proxmox-next.cfg").exists()
    assert run_main(monkeypatch, tmp_path, "undo", None, capsys)[0] == 0
    assert not (tmp_path / "etc/default/grub.d/osx-proxmox-next.cfg").exists()
    bad = dict(plan, refusals=["no"])
    code, out = run_main(monkeypatch, tmp_path, "apply", {"plan": bad}, capsys)
    assert code == 1 and not json.loads(out)["ok"]


def test_render_preview_shows_new_and_changed_files(tmp_path):
    plan = {"changes": [{"path": "/etc/a", "old": None, "new": "x\n", "reason": "r"},
                        {"path": "/etc/b", "old": "a\n", "new": "b\n", "reason": "r2"}],
            "commands": [{"argv": ["echo", "a b"], "reason": "why"}]}
    text = hs.render_preview(plan)
    assert "(new file)" in text and "-a" in text and "+b" in text and "run: echo 'a b'" in text


# ── regressions from the independent audit ───────────────────────────

def test_boot_commands_get_a_long_timeout(tmp_path):
    root = host(tmp_path)
    add_card(Path(root))
    seen: list[tuple[list[str], int]] = []

    def run(argv, timeout=20):
        seen.append((argv, timeout))
        return (0, "")

    assert hs.apply_plan(plan_for(root), hs.new_state(), root, run)["ok"]
    assert seen and all(t == hs.LONG_TIMEOUT for _a, t in seen)
    # the rollback rebuilds with the same long timeout
    seen.clear()
    root2 = host(tmp_path / "second")
    add_card(Path(root2))
    hs.apply_plan(plan_for(root2), hs.new_state(), root2,
                  lambda argv, timeout=20: seen.append((argv, timeout)) or ((1, "x") if argv[0] == "update-grub" else (0, "")))
    assert seen and {t for _a, t in seen} == {hs.LONG_TIMEOUT}


def test_a_commented_ids_line_is_never_touched(tmp_path):
    root = host(tmp_path)
    add_card(Path(root))
    write(Path(root), "/etc/modprobe.d/vfio.conf",
          "# options vfio-pci ids=10de:1234 disable_vga=1\noptions vfio-pci ids=10de:2504\n")
    plan = plan_for(root)
    merged = next(c for c in plan["changes"] if c["path"] == "/etc/modprobe.d/vfio.conf")["new"]
    assert merged.splitlines()[0] == "# options vfio-pci ids=10de:1234 disable_vga=1"
    assert merged.splitlines()[1] == "options vfio-pci ids=10de:2504,1002:73ff,1002:ab28"
    write(Path(root), "/etc/modprobe.d/vfio.conf", "# options vfio-pci ids=1002:73ff\n")
    only_comment = plan_for(root)
    assert not [c for c in only_comment["changes"] if c["path"] == "/etc/modprobe.d/vfio.conf"]
    assert "ids=1002:73ff,1002:ab28" in next(c for c in only_comment["changes"] if c["path"] == hs.MODPROBE_FILE)["new"]


def test_interrupt_rolls_back_and_reraises(tmp_path):
    root = host(tmp_path)
    add_card(Path(root))

    def run(argv, timeout=20):
        raise KeyboardInterrupt

    state = hs.new_state()
    with pytest.raises(KeyboardInterrupt):
        hs.apply_plan(plan_for(root), state, root, run)
    assert not (Path(root) / "etc/default/grub.d/osx-proxmox-next.cfg").exists()
    assert all(e["reverted"] for e in state["host_changes"])


def test_failed_refresh_is_retried_by_the_next_undo(tmp_path):
    root = host(tmp_path)
    add_card(Path(root))
    state = hs.new_state()
    assert hs.apply_plan(plan_for(root), state, root, FakeRun(default=(0, "")))["ok"]
    res = hs.undo_host(state, root, FakeRun({"update-grub": (1, "locked")}, default=(0, "")))
    assert not res["ok"] and "locked" in res["errors"][0] and state["pending_refresh"] == ["grub"]
    retry = FakeRun(default=(0, ""))
    res = hs.undo_host(state, root, retry)
    assert res["ok"] and retry.ran("update-grub") and state["pending_refresh"] == []


def test_undo_survives_a_missing_backup_and_edits_made_since(tmp_path):
    root = host(tmp_path)
    add_card(Path(root))
    write(Path(root), "/etc/modprobe.d/vfio.conf", "options vfio-pci ids=10de:2504\n")
    state = hs.new_state()
    assert hs.apply_plan(plan_for(root), state, root, FakeRun(default=(0, "")))["ok"]
    edited = next(e for e in state["host_changes"] if e["path"] == "/etc/modprobe.d/vfio.conf")
    Path(edited["backup"]).unlink()
    write(Path(root), hs.GRUB_DROPIN, "# the user changed this\n")
    res = hs.undo_host(state, root, FakeRun(default=(0, "")))
    assert not res["ok"]  # a missing backup is a real problem; an edited file is not
    assert any("backup of /etc/modprobe.d/vfio.conf is missing" in e for e in res["errors"])
    assert any("was changed after this tool wrote it" in k for k in res["kept"])
    assert (Path(root) / hs.GRUB_DROPIN.lstrip("/")).read_text() == "# the user changed this\n"
    assert hs.MODULES_FILE in res["restored"]  # the other files were still restored


def test_paths_outside_etc_are_refused(tmp_path):
    root = host(tmp_path)
    plan = hs.new_plan()
    plan["changes"].append({"path": "/etc/../usr/bin/x", "old": None, "new": "x", "reason": "r", "mode": 0o644})
    res = hs.apply_plan(plan, hs.new_state(), root, FakeRun())
    assert not res["ok"] and "outside /etc" in res["error"]
    state = hs.new_state()
    state["host_changes"] = [{"path": "/usr/bin/x", "existed": False, "backup": "", "reverted": False, "sha_after": ""}]
    assert "not a path this tool manages" in hs.undo_host(state, root, FakeRun())["errors"][0]


def test_two_applies_in_one_second_keep_separate_backups(tmp_path, monkeypatch):
    root = host(tmp_path)
    add_card(Path(root))
    monkeypatch.setattr(hs, "_now", lambda: "20260101-000000")
    write(Path(root), "/etc/modprobe.d/vfio.conf", "options vfio-pci ids=10de:2504\n")
    s1, s2 = hs.new_state(), hs.new_state()
    hs.apply_plan(plan_for(root), s1, root, FakeRun(default=(0, "")))
    write(Path(root), "/etc/modprobe.d/vfio.conf", "options vfio-pci ids=10de:9999\n")
    hs.apply_plan(plan_for(root), s2, root, FakeRun(default=(0, "")))
    dirs = {e["stamp"] for e in s1["host_changes"] + s2["host_changes"]}
    assert len(dirs) == 2


def test_secure_boot_unknown_counts_as_on(tmp_path):
    root = host(tmp_path)
    var = Path(root) / hs.SECURE_BOOT_VAR.lstrip("/")
    assert hs.detect_facts(root, FakeRun(), env={})["secure_boot"] is False  # efivar says off
    var.write_bytes(b"\x06\x00\x00\x00\x01")
    assert hs.detect_facts(root, FakeRun(), env={})["secure_boot"] is True
    var.write_bytes(b"\x06")
    assert hs.detect_facts(root, FakeRun(), env={})["secure_boot"] is True
    var.unlink()
    assert hs.detect_facts(root, FakeRun(), env={})["secure_boot"] is True  # EFI host, state unreadable
    assert hs.detect_facts(host(tmp_path / "bios", efi=False), FakeRun(), env={})["secure_boot"] is False
    assert hs.detect_facts(root, FakeRun({"mokutil --sb-state": (0, "SecureBoot disabled")}), env={})["secure_boot"] is False


def test_grub_assignments_are_evaluated_in_order(tmp_path):
    root = host(tmp_path)
    write(Path(root), "/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on"\nGRUB_CMDLINE_LINUX="root=x"\n')
    write(Path(root), "/etc/default/grub.d/10.cfg", 'GRUB_CMDLINE_LINUX_DEFAULT="splash"\n')
    write(Path(root), "/etc/default/grub.d/20.cfg", 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT iommu=pt ${GRUB_CMDLINE_LINUX_DEFAULT}"\n')
    assert hs.configured_cmdline("grub", root) == ["splash", "iommu=pt", "root=x"]
