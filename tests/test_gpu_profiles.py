"""Vendor profiles: AMD allowlist, global args, the original-settings record, AMD attach and exact undo."""
from __future__ import annotations

import importlib.util
import json
import plistlib
import subprocess
from pathlib import Path

import pytest

from gpu_fakes import add_card
from nullmoth_fakes import GUID, FakeQm, MAC_CFG, add_dev, write_oc
from osx_proxmox_next import amd_host, gpu, gpu_attach, nullmoth

REPO = Path(__file__).resolve().parent.parent
PATCHER = REPO / "src/osx_proxmox_next/data/gpu/oc_boot_args.py"


# ── AMD allowlist ────────────────────────────────────────────────────

@pytest.mark.parametrize("device,family,reset,args", [
    (0x67DF, "polaris10", "vendor-reset", ()),
    (0x67FF, "polaris11", "vendor-reset", ()),
    (0x687F, "vega10", "vendor-reset", ()),
    (0x6867, "vega10", "vendor-reset", ()),
    (0x66AF, "vega20", "vendor-reset-warn", ()),
    (0x731F, "navi10", "vendor-reset", ("agdpmod=pikera",)),
    (0x7340, "navi14", "vendor-reset", ("agdpmod=pikera",)),
    (0x73BF, "navi21", "early-bind", ("agdpmod=pikera",)),
    (0x73FF, "navi23", "early-bind", ("agdpmod=pikera",)),
    (0x73AF, "navi21", "early-bind", ("agdpmod=pikera",)),  # spoofed into 73BF
    (0x73A5, "navi21", "early-bind", ("agdpmod=pikera",)),
    (0x73EF, "navi23", "early-bind", ("agdpmod=pikera",)),
])
def test_supported_cards_and_their_profile(device, family, reset, args):
    fam = amd_host.family_for(device)
    assert fam.key == family and fam.reset == reset and amd_host.boot_args_for(device) == args
    assert amd_host.is_supported_device(device)
    assert amd_host.needs_vendor_reset(device) == reset.startswith("vendor-reset")


@pytest.mark.parametrize("device,needle", [
    (0x73DF, "Navi 22"), (0x73C0, "Navi 22"), (0x743F, "Navi 24"), (0x7422, "Navi 24"),
    (0x6FDF, "2048SP"), (0x699F, "RX 540/550"), (0x744C, "RX 7000"), (0x7480, "RX 7000"), (0x7550, "RX 9000"),
    (0x1636, "no Apple driver"), (0x15E7, "no Apple driver"),
])
def test_unsupported_cards_get_a_one_line_reason(device, needle):
    assert not amd_host.is_supported_device(device)
    reason = amd_host.unsupported_reason(device)
    assert needle in reason and "\n" not in reason and "—" not in reason
    assert amd_host.boot_args_for(device) == () and amd_host.reset_note(device) == ""


def test_spoof_adds_a_device_id_to_the_first_function_only():
    assert amd_host.hostpci_extra(0x73AF) == ",device-id=0x73bf"
    assert amd_host.hostpci_extra(0x73EF) == ",device-id=0x73ff"
    assert amd_host.hostpci_extra(0x73FF) == "" and amd_host.spoofed_id(0x73FF) is None


def test_reset_notes():
    assert "reset bug" in amd_host.reset_note(0x67DF) and "vendor-reset" in amd_host.reset_note(0x67DF)
    assert "before amdgpu loads" in amd_host.reset_note(0x73FF)


def test_data_file_ids_do_not_overlap():
    seen: dict[int, str] = {}
    for fam in amd_host._load().families:
        for dev in fam.ids:
            assert dev not in seen, (hex(dev), fam.key, seen[dev])
            seen[dev] = fam.key
    for _label, ids, _reason in amd_host._load().deny:
        assert not (ids & set(seen))


def test_check_amd_gpu(tmp_path):
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("quiet")
    add_card(tmp_path, device=0x73FF)
    sysfs = tmp_path / "sys"
    report = amd_host.check_amd_gpu("06:00", sysfs, cmdline)
    assert report.ok and report.warnings == []
    assert [g.bdf for g in amd_host.detect_amd_gpus(sysfs)] == ["0000:06:00"]


def test_check_amd_gpu_warnings_and_refusals(tmp_path):
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("quiet")
    add_card(tmp_path, bdf="0000:06:00", device=0x66AF, bar0=8 * 1024**3, boot_vga=True)
    add_card(tmp_path, bdf="0000:41:00", device=0x687F, group=8)
    add_card(tmp_path, bdf="0000:42:00", device=0x73DF, group=9)
    add_card(tmp_path, bdf="0000:43:00", device=0x67DF, group=10, driver="amdgpu")
    sysfs = tmp_path / "sys"
    vega20 = amd_host.check_amd_gpu("06:00", sysfs, cmdline)
    assert vega20.ok
    joined = " ".join(vega20.warnings)
    assert "issue 112" in joined and "BAR0 is 8192 MiB" in joined and "sysfb_init" in joined
    assert "BAR1" not in joined
    vega10 = amd_host.check_amd_gpu("41:00", sysfs, cmdline)
    assert any("Tahoe 26 yet" in w for w in vega10.warnings)
    navi22 = amd_host.check_amd_gpu("42:00", sysfs, cmdline)
    assert not navi22.ok and "Navi 22" in navi22.issues[0]
    unbound = amd_host.check_amd_gpu("43:00", sysfs, cmdline)
    assert not unbound.ok and "amdgpu" in unbound.issues[0]
    assert "  softdep amdgpu pre: vfio-pci" in unbound.hints and "  softdep nouveau pre: vfio-pci" not in unbound.hints


def test_vfio_hint_adds_xhci_for_amd_usb_c(tmp_path):
    add_card(tmp_path, device=0x73FF)
    add_dev(tmp_path / "sys", "0000:06:00.2", 0x1002, 0x73A6, cls=0x0C0330, driver="vfio-pci")
    hint = gpu.vfio_conf_hint(tmp_path / "sys", "0000:06:00")
    assert "  softdep xhci_pci pre: vfio-pci" in hint and "  softdep i2c_nvidia_gpu pre: vfio-pci" not in hint
    assert "  options vfio-pci ids=1002:73ff,1002:ab28,1002:73a6" in hint


# ── global args ──────────────────────────────────────────────────────

HOTPLUG = gpu.HOTPLUG_ARG


@pytest.mark.parametrize("args,expect", [
    ("", HOTPLUG),
    ("-cpu host", "-cpu host " + HOTPLUG),
    ("-cpu host -global ICH9-LPC.acpi-pci-hotplug-with-bridge-support=on -smbios x", "-cpu host " + HOTPLUG + " -smbios x"),
    ("-global   ICH9-LPC.acpi-pci-hotplug-with-bridge-support=on", HOTPLUG),
    ("-cpu host " + HOTPLUG, "-cpu host " + HOTPLUG),
])
def test_with_global_args(args, expect):
    assert gpu.with_global_args(args, (HOTPLUG,)) == expect
    assert gpu.with_global_args(expect, (HOTPLUG,)) == expect


def test_without_global_args_removes_only_the_wanted_keys():
    args = f"-cpu host {gpu.MMIO_ARG} {HOTPLUG}"
    assert gpu.without_global_args(args, (HOTPLUG,)) == f"-cpu host {gpu.MMIO_ARG}"
    assert gpu.without_global_args(args, (HOTPLUG, gpu.MMIO_ARG)) == "-cpu host"
    assert gpu.without_global_args("-cpu host", (HOTPLUG,)) == "-cpu host"


def test_profiles():
    assert gpu.NVIDIA_PROFILE.pcie == 0 and gpu.NVIDIA_PROFILE.global_args == (gpu.MMIO_ARG,)
    assert amd_host.AMD_PROFILE.pcie == 1 and amd_host.AMD_PROFILE.global_args == (HOTPLUG,)
    assert gpu.hostpci_value("0000:06:00", amd_host.AMD_PROFILE, ",device-id=0x73bf") == "0000:06:00,pcie=1,device-id=0x73bf"
    steps = gpu.build_hostpci_attach_steps(100, "0000:06:00", "-cpu host", amd_host.AMD_PROFILE, vga_none=True)
    assert steps[0].argv == ["qm", "set", "100", "--hostpci0", "0000:06:00,pcie=1"]
    assert steps[1].argv == ["qm", "set", "100", "--args", "-cpu host " + HOTPLUG]
    assert steps[2].argv == ["qm", "set", "100", "--vga", "none"]
    assert len(gpu.build_hostpci_attach_steps(100, "0000:06:00", "-cpu host " + HOTPLUG, amd_host.AMD_PROFILE)) == 1


# ── the record ───────────────────────────────────────────────────────

def run_step(step) -> None:
    subprocess.run(step.argv, check=True)


def test_record_step_keeps_the_first_originals(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu, "STATE_DIR", tmp_path / "state")
    first = gpu.AttachRecord(100, "0000:06:00", "amd", hostpci0=None, args="-cpu host", vga="std", vga_changed=True, oc="gpu")
    run_step(gpu.record_step(first))
    assert gpu.load_record(100) == first
    run_step(gpu.record_step(gpu.AttachRecord(100, "0000:06:00", "amd", hostpci0="changed")))
    assert gpu.load_record(100) == first
    assert gpu.load_record(101) is None
    (tmp_path / "state/gpu-attach-102.json").write_text("{not json")
    assert gpu.load_record(102) is None


def test_detach_restores_exactly_what_was_there(tmp_path):
    rec = gpu.AttachRecord(100, "0000:06:00", "amd", hostpci0=None, args="-cpu host", vga="std", vga_changed=True)
    steps = gpu.build_hostpci_detach_steps(100, "-cpu host " + HOTPLUG, True, record=rec, profile=amd_host.AMD_PROFILE)
    assert [s.argv[2:] for s in steps[:3]] == [["100", "--delete", "hostpci0"], ["100", "--args", "-cpu host"], ["100", "--vga", "std"]]
    assert steps[-1].argv == ["rm", "-f", str(gpu.record_path(100))]


def test_detach_restores_a_pre_existing_hostpci0_and_removes_added_args():
    rec = gpu.AttachRecord(100, "0000:06:00", "amd", hostpci0="0000:06:00,pcie=1", args=None, vga=None, vga_changed=True)
    cfg = {"args": HOTPLUG, "hostpci0": "0000:06:00,pcie=1,x-vga=1", "vga": "none"}
    steps = gpu.build_hostpci_detach_steps(100, HOTPLUG, True, record=rec, cfg=cfg)
    assert steps[0].argv == ["qm", "set", "100", "--hostpci0", "0000:06:00,pcie=1"]
    assert steps[1].argv == ["qm", "set", "100", "--delete", "args"]
    assert steps[2].argv == ["qm", "set", "100", "--delete", "vga"]


def test_detach_with_nothing_to_change_only_forgets_the_record():
    rec = gpu.AttachRecord(100, "0000:06:00", "amd", args="-cpu host")
    steps = gpu.build_hostpci_detach_steps(100, "-cpu host", False, record=rec)
    assert [s.title for s in steps] == ["Forget the recorded settings"]


# ── AMD attach ───────────────────────────────────────────────────────

AMD_CFG = MAC_CFG.replace("macos-tahoe", "macos-sequoia") + "vga: std\n"


def amd_prep(tmp_path, vms=None, bdf="06:00", device=0x73FF, display="gpu", **kw):
    add_card(tmp_path, device=device)
    cmd = tmp_path / "cmdline"
    cmd.write_text("quiet")
    return gpu_attach.prepare_attach_amd(100, bdf, display, adapter=FakeQm(vms if vms is not None else {100: ("stopped", AMD_CFG)}),
                                         sysfs_root=tmp_path / "sys", cmdline_path=cmd, **kw)


def test_amd_attach_plan_for_navi(tmp_path):
    plan = amd_prep(tmp_path)
    assert plan.ok, plan.issues
    titles = [s.title for s in plan.steps]
    assert titles[0] == "Verify VM is stopped" and titles[1].startswith("Record the original VM settings")
    assert titles[2].startswith("Add the display boot-args")
    assert plan.steps[3].argv == ["qm", "set", "100", "--hostpci0", "0000:06:00,pcie=1"]
    assert plan.steps[4].argv[-1].endswith(HOTPLUG) and plan.steps[5].argv == ["qm", "set", "100", "--vga", "none"]
    script = plan.steps[2].argv[2]
    assert "GPU_BOOT_ARGS=agdpmod=pikera python3" in script and "GPU_OC_PY" in script
    record = json.loads(plan.steps[1].argv[2].split("printf %s ")[1].split(" > ")[0].strip("'"))
    assert record["oc"] == "gpu" and record["vga"] == "std" and record["vga_changed"] is True


def test_polaris_needs_no_opencore_change_and_console_keeps_the_display(tmp_path):
    plan = amd_prep(tmp_path, device=0x67DF, display="console")
    assert plan.ok and not any("boot-args" in s.title for s in plan.steps)
    assert not any(s.argv[-2:] == ["--vga", "none"] for s in plan.steps)


def test_spoofed_card_gets_the_device_id_option(tmp_path):
    plan = amd_prep(tmp_path, device=0x73AF)
    assert plan.ok and any(s.argv[-1] == "0000:06:00,pcie=1,device-id=0x73bf" for s in plan.steps)


def test_amd_attach_can_stop_a_running_vm(tmp_path):
    plan = amd_prep(tmp_path, vms={100: ("running", AMD_CFG)}, allow_stop=True)
    assert plan.ok and plan.steps[0].title == "Stop VM"


@pytest.mark.parametrize("vms,needle", [
    ({}, "not found"),
    ({100: ("running", AMD_CFG)}, "qm stop 100"),
    ({100: ("stopped", "name: x\nargs: -cpu host\n")}, "not a macOS VM"),
    ({100: ("stopped", AMD_CFG + "ide2: local:iso/rec.dmg,media=disk\n")}, "recovery disk attached"),
    ({100: ("stopped", AMD_CFG + "hostpci0: 0000:09:00,pcie=0\n")}, "hostpci0 is already in use"),
    ({100: ("stopped", AMD_CFG), 101: ("running", "hostpci0: host=06:00.0,pcie=1\n")}, "running VM 101"),
])
def test_amd_attach_refusals(tmp_path, vms, needle):
    plan = amd_prep(tmp_path, vms=vms)
    assert not plan.ok and any(needle in i for i in plan.issues) and not plan.steps


def test_amd_attach_other_checks(tmp_path):
    stopped_user = amd_prep(tmp_path, vms={100: ("stopped", AMD_CFG), 101: ("stopped", "hostpci0: 06:00,pcie=1\n")})
    assert stopped_user.ok and any("VM 101 also has this card" in w for w in stopped_user.warnings)
    same = amd_prep(tmp_path / "s", vms={100: ("stopped", AMD_CFG + "hostpci0: 0000:06:00,pcie=0\n")})
    assert same.ok
    unknown = amd_prep(tmp_path / "u", vms={100: ("stopped", AMD_CFG.replace("macos-sequoia", "vm"))})
    assert unknown.ok and any("Cannot tell which macOS" in w for w in unknown.warnings)
    bad_card = amd_prep(tmp_path / "b", device=0x73DF)
    assert not bad_card.ok and "Navi 22" in bad_card.issues[0]


# ── undo through the record ──────────────────────────────────────────

def test_prepare_detach_amd_uses_the_record_and_the_gpu_patcher(tmp_path):
    rec = gpu.AttachRecord(100, "0000:06:00", "amd", hostpci0=None, args=None, vga="std", vga_changed=True, oc="gpu")
    run_step(gpu.record_step(rec))
    cfg = AMD_CFG.replace("vga: std", "vga: none") + f"hostpci0: 0000:06:00,pcie=1\n"
    cfg = cfg.replace("-cpu host\n", "-cpu host " + HOTPLUG + "\n")
    plan = gpu_attach.prepare_detach(100, adapter=FakeQm({100: ("stopped", cfg)}))
    assert plan.ok
    titles = [s.title for s in plan.steps]
    assert titles[:2] == ["Verify VM is stopped", "Restore the previous OpenCore config"]
    assert "GPU_BOOT_ARGS=" not in plan.steps[1].argv[2] and '"$OC_MNT/EFI/OC" restore' in plan.steps[1].argv[2]
    assert "Restore the display setting" in titles and titles[-1] == "Forget the recorded settings"
    assert not gpu_attach.prepare_detach(100, adapter=FakeQm({})).ok
    running = gpu_attach.prepare_detach(100, adapter=FakeQm({100: ("running", cfg)}))
    assert not running.ok and "qm stop 100" in running.issues[0]
    stop = gpu_attach.prepare_detach(100, adapter=FakeQm({100: ("running", cfg)}), allow_stop=True)
    assert stop.steps[0].title == "Stop VM"


def test_prepare_detach_falls_back_to_nullmoth(tmp_path):
    cfg = MAC_CFG + "hostpci0: 0000:01:00,pcie=0\n"
    plan = gpu_attach.prepare_detach(100, adapter=FakeQm({100: ("stopped", cfg)}))
    assert plan.ok and plan.steps[1].title == "Restore the previous OpenCore config"
    assert "pre-nullmoth" in plan.steps[1].argv[2] or "NULLMOTH_PY" in plan.steps[1].argv[2]


def test_nvidia_attach_records_and_detach_restores_exactly(tmp_path):
    cfg = MAC_CFG + "vga: std\n"
    good = FakeQm({100: ("stopped", cfg)})
    from nullmoth_fakes import good_card
    good_card(tmp_path)
    cmd = tmp_path / "cmdline"
    cmd.write_text("quiet")
    plan = nullmoth.prepare_attach(100, "01:00", Path("/c/a.zip"), adapter=good, sysfs_root=tmp_path, cmdline_path=cmd)
    assert plan.ok
    run_step(next(s for s in plan.steps if s.title.startswith("Record")))
    rec = gpu.load_record(100)
    assert (rec.hostpci0, rec.args, rec.vga, rec.oc, rec.vendor) == (None, MAC_CFG.splitlines()[1][6:], "std", "nullmoth", "nvidia")
    attached = cfg.replace("-cpu host\n", "-cpu host " + gpu.MMIO_ARG + "\n") + "hostpci0: 0000:01:00,pcie=0\n"
    detach = nullmoth.prepare_detach(100, adapter=FakeQm({100: ("stopped", attached)}))
    argvs = [s.argv for s in detach.steps]
    assert ["qm", "set", "100", "--args", rec.args] in argvs and ["qm", "set", "100", "--delete", "hostpci0"] in argvs
    assert not any("vga" in a for a in argvs)  # NVIDIA keeps the display, so it is not touched


# ── the OpenCore boot-args patcher ───────────────────────────────────

@pytest.fixture(scope="module")
def patch():
    spec = importlib.util.spec_from_file_location("oc_boot_args", PATCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_patcher_attach_and_detach(patch, tmp_path, monkeypatch):
    oc = tmp_path / "OC"
    original = write_oc(oc, boot_args="keepsyms=1 agdpmod=vit9696 debug=0x100")
    monkeypatch.setenv("GPU_BOOT_ARGS", "agdpmod=pikera")
    patch.attach(str(oc))
    cfg = plistlib.loads((oc / "config.plist").read_bytes())
    assert cfg["NVRAM"]["Add"][GUID]["boot-args"] == "keepsyms=1 debug=0x100 agdpmod=pikera"
    assert (oc / patch.BACKUP).read_bytes() == original and b"<array/>" not in (oc / "config.plist").read_bytes()
    patch.attach(str(oc))  # again: no duplicate, backup untouched
    assert plistlib.loads((oc / "config.plist").read_bytes())["NVRAM"]["Add"][GUID]["boot-args"].count("agdpmod") == 1
    assert (oc / patch.BACKUP).read_bytes() == original
    patch.detach(str(oc))
    assert (oc / "config.plist").read_bytes() == original and not (oc / patch.BACKUP).exists()
    patch.detach(str(oc))  # repeating an interrupted undo is fine
    assert (oc / "config.plist").read_bytes() == original


def test_patcher_refuses_when_the_config_has_the_nullmoth_changes(patch, tmp_path, monkeypatch):
    oc = tmp_path / "OC"
    write_oc(oc, boot_args="keepsyms=1 nvfb=1 nvaccel=1")
    monkeypatch.setenv("GPU_BOOT_ARGS", "agdpmod=pikera")
    before = (oc / "config.plist").read_bytes()
    with pytest.raises(SystemExit) as exc:
        patch.attach(str(oc))
    assert exc.value.code == 2 and (oc / "config.plist").read_bytes() == before


def test_patcher_main_usage(patch, tmp_path):
    with pytest.raises(SystemExit) as exc:
        patch.main(["x"])
    assert exc.value.code == 64
    oc = tmp_path / "OC"
    write_oc(oc)
    import os
    os.environ["GPU_BOOT_ARGS"] = "agdpmod=pikera"
    try:
        patch.main(["x", str(oc), "attach"])
        patch.main(["x", str(oc), "detach"])
    finally:
        del os.environ["GPU_BOOT_ARGS"]
