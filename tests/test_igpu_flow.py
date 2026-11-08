"""Intel iGPU: host plan, attach plan, detection and the multi-GPU recommendation."""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from gpu_fakes import EFI_GRUB, FakeRun, add_card, make_host
from nullmoth_fakes import FakeQm, add_dev, good_card
from osx_proxmox_next import gpu, gpu_setup, gpu_setup_cli, host_setup, igpu_attach, igpu_engine, intel_host
from osx_proxmox_next.gpu_setup import Session

REGS = {"setpci -s 00:02.0 C0.L 50.W": (0, "4b800001\n02c1\n"), "efibootmgr -v": (0, EFI_GRUB)}
IGD = "0000:00:02"
VM = ('name: igpu-seq\nargs: -device isa-applesmc,osk="x" -smbios type=2 -cpu host\nmachine: pc\n'
      'ide0: local-lvm:vm-1-disk-1,media=disk\nvirtio0: local-lvm:vm-1-disk-2,size=64G\nvga: std\n'
      'smbios1: uuid=C0684C25-95BD-4F64-AF3C-DBEA2D1266CE\n')


def host(tmp_path: Path, *, igpu: str | None = "i915", amd: bool = False, nvidia: bool = False, device: int = 0x46A6):
    root = make_host(tmp_path, groups=3)
    if igpu is not None:
        add_dev(root / "sys", f"{IGD}.0", 0x8086, device, driver=igpu or None, group=1, boot_vga=True)
    if amd:
        add_card(root)
    if nvidia:
        good_card(root / "sys", group=2)
    return root


def session(root: Path, vms=None) -> Session:
    return Session(str(root), FakeRun(REGS, default=(0, "")), FakeQm(vms if vms is not None else {100: ("stopped", VM)}))


# ── host plan ────────────────────────────────────────────────────────

def plan_for(root: Path):
    facts = host_setup.detect_facts(str(root), FakeRun(REGS, default=(0, "")))
    target = host_setup.build_target(str(root), IGD, "intel")
    return host_setup.plan_host(facts, target, {"allow_no_console": True}, str(root), FakeRun(REGS, default=(0, "")))


def test_an_unprepared_host_blacklists_i915_and_binds_the_id(tmp_path):
    plan = plan_for(host(tmp_path))
    mod = next(c for c in plan["changes"] if c["path"] == host_setup.MODPROBE_FILE)["new"]
    assert "blacklist i915\n" in mod and "options vfio-pci ids=8086:46a6\n" in mod and "disable_vga" not in mod
    assert "softdep" not in mod
    paths = [c["path"] for c in plan["changes"]]
    assert host_setup.MODULES_FILE in paths and plan["reboot_required"] and plan["initramfs"]
    assert any("gives up its Intel graphics" in w for w in plan["warnings"])
    assert not any(p in (host_setup.GRUB_DROPIN, host_setup.KERNEL_CMDLINE) for p in paths)  # IOMMU already on


def test_the_only_display_needs_the_i_understand(tmp_path):
    root = host(tmp_path)
    facts = host_setup.detect_facts(str(root), FakeRun(REGS, default=(0, "")))
    plan = host_setup.plan_host(facts, host_setup.build_target(str(root), IGD, "intel"), {}, str(root), FakeRun(REGS, default=(0, "")))
    assert any("only display adapter" in r for r in plan["refusals"])


def test_a_prepared_host_needs_no_changes_and_no_reboot(tmp_path):
    root = host(tmp_path, igpu="vfio-pci")
    (root / "etc/modprobe.d").mkdir(parents=True)
    (root / "etc/modprobe.d/igpu-spike.conf").write_text("blacklist i915\noptions vfio-pci ids=8086:46a6\n")
    (root / "etc/modules-load.d").mkdir(parents=True)
    (root / "etc/modules-load.d/igpu-spike.conf").write_text("vfio-pci\n")
    plan = plan_for(root)
    assert not plan["changes"] and not plan["commands"] and not plan["reboot_required"]
    assert any("already held by vfio-pci" in s for s in plan["skipped"])


def test_vfio_without_the_i915_blacklist_is_not_called_prepared(tmp_path):
    root = host(tmp_path, igpu="vfio-pci")
    (root / "etc/modprobe.d").mkdir(parents=True)
    (root / "etc/modprobe.d/x.conf").write_text("options vfio-pci ids=8086:46a6\n")
    plan = plan_for(root)
    assert not any("already held" in x for x in plan["skipped"])
    assert "blacklist i915" in next(c for c in plan["changes"] if c["path"] == host_setup.MODPROBE_FILE)["new"]


def test_an_identical_second_device_is_refused(tmp_path):
    root = host(tmp_path)
    add_dev(root / "sys", "0000:00:03.0", 0x8086, 0x46A6, driver="i915", group=2)
    assert any("same PCI id" in r for r in plan_for(root)["refusals"])


def test_existing_vfio_ids_lines_are_extended(tmp_path):
    root = host(tmp_path)
    (root / "etc/modprobe.d").mkdir(parents=True)
    (root / "etc/modprobe.d/vfio.conf").write_text("options vfio-pci ids=10de:2504\n")
    plan = plan_for(root)
    assert "8086:46a6" in next(c for c in plan["changes"] if c["path"].endswith("vfio.conf"))["new"]


def test_kernel_args_are_still_written_when_the_iommu_is_off(tmp_path):
    import shutil
    root = make_host(tmp_path, groups=0, tables=("DMAR",))
    add_dev(root / "sys", f"{IGD}.0", 0x8086, 0x46A6, driver="i915", group=1, boot_vga=True)
    shutil.rmtree(root / "sys/kernel/iommu_groups")  # the firmware table is there but the kernel did not turn it on
    assert any("intel_iommu=on" in c["new"] for c in plan_for(root)["changes"] if c["path"] in (host_setup.GRUB_DROPIN, host_setup.KERNEL_CMDLINE))


# ── intel_host ───────────────────────────────────────────────────────

def test_intel_host_adapters(tmp_path):
    root = host(tmp_path, igpu="vfio-pci")
    assert [g.device_id for g in intel_host.detect_intel_gpus(root / "sys")] == [0x46A6]
    assert intel_host.is_supported_device(0x46A6) and not intel_host.is_supported_device(0x56A0)
    rep = intel_host.check_intel_gpu(root, FakeRun(REGS))
    assert rep.ok and rep.layout["max_ram_below_4g_mb"] == 1342 and rep.rom_file.endswith("igd.rom")
    bad = intel_host.check_intel_gpu(tmp_path / "empty", FakeRun(REGS))
    assert not bad.ok and bad.rom_file == ""


# ── attach plan ──────────────────────────────────────────────────────

def attach(root, vms, **kw):
    return igpu_attach.prepare_attach_igpu(100, Path("/c/w.zip"), Path("/c/a.zip"), Path("/c/x.rom"), adapter=FakeQm(vms),
                                           root=root, run=FakeRun(REGS), **kw)


def test_attach_plan_order_and_values(tmp_path):
    plan = attach(host(tmp_path, igpu="vfio-pci"), {100: ("stopped", VM)})
    assert plan.ok, plan.issues
    titles = [s.title for s in plan.steps]
    assert titles[0] == "Verify VM is stopped" and titles[1].startswith("Record the original")
    assert titles[2].startswith("Add WhateverXe") and titles[3].startswith("Put the option ROM")
    by = {s.title: s.argv for s in plan.steps}
    assert by["Attach the iGPU (hostpci0, legacy IGD mode)"][-1] == "0000:00:02.0,legacy-igd=1,romfile=ADL-H_RPL-H_GOPv21_igd.rom"
    assert "max-ram-below-4g=1342M" in by["Size the guest's low RAM for the iGPU memory layout"][-1]
    assert by["Turn the emulated display off (vga none)"][-2:] == ["--vga", "none"]
    oc = plan.steps[2].argv[2]
    assert "IGPU_WEX_SHA256=" + igpu_engine.PINS["whateverxe"][2] in oc and "config.plist" in oc


def test_attach_stops_a_running_vm_only_when_allowed(tmp_path):
    root = host(tmp_path, igpu="vfio-pci")
    assert "is running" in " ".join(attach(root, {100: ("running", VM)}).issues)
    plan = attach(root, {100: ("running", VM)}, allow_stop=True)
    assert plan.ok and plan.steps[0].title == "Stop VM"


@pytest.mark.parametrize("cfg,text", [
    (VM.replace("machine: pc\n", "machine: q35\n"), "needs i440fx"),
    (VM.replace("machine: pc\n", ""), "default machine type"),
    (VM + "sata1: x:vm-1-disk-3,media=disk\n", "recovery disk"),
    (VM + "ide2: x:vm-1-disk-3,media=disk\n", "recovery disk"),
    (VM.replace("-device isa-applesmc", "-device other"), "not a macOS VM"),
    (VM + "hostpci0: 0000:06:00,pcie=1\n", "hostpci0 is already in use"),
])
def test_attach_refusals(tmp_path, cfg, text):
    plan = attach(host(tmp_path, igpu="vfio-pci"), {100: ("stopped", cfg)})
    assert not plan.ok and text in " ".join(plan.issues) and not plan.steps


def test_attach_missing_vm_and_unready_host(tmp_path):
    assert "not found" in attach(host(tmp_path, igpu="vfio-pci"), {}).issues[0]
    assert "still uses the iGPU" in " ".join(attach(host(tmp_path / "b", igpu="i915"), {100: ("stopped", VM)}).issues)


def test_attach_notes_tahoe_unknown_macos_and_other_holders(tmp_path):
    root = host(tmp_path, igpu="vfio-pci")
    other = VM.replace("name: igpu-seq", "name: other") + "hostpci0: 0000:00:02.0,legacy-igd=1\n"
    plan = attach(root, {100: ("stopped", VM.replace("igpu-seq", "macos-tahoe")), 200: ("stopped", other)})
    assert plan.ok and any("every restart and shutdown" in w for w in plan.warnings) and any("VM 200" in w for w in plan.warnings)
    assert any("Cannot tell which macOS" in w for w in attach(root, {100: ("stopped", VM.replace("igpu-seq", "vm"))}).warnings)
    busy = attach(root, {100: ("stopped", VM), 200: ("running", other)})
    assert any("running VM 200" in i for i in busy.issues)


def test_the_record_remembers_the_originals_and_undo_restores_them(tmp_path):
    plan = attach(host(tmp_path, igpu="vfio-pci"), {100: ("stopped", VM)})
    record_cmd = plan.steps[1].argv[2]
    assert '"oc": "igpu"' in record_cmd and '"vendor": "intel"' in record_cmd
    import json
    rec = json.loads(record_cmd.split("printf %s ")[1].split(" > ")[0].strip("'"))
    cfg = host_setup.parse_config(VM + f"hostpci0: {rec['after']['hostpci0']}\nargs: {rec['after']['args']}\n".replace("args: ", "args: ", 1))
    cfg["vga"] = "none"
    res = host_setup.vm_restore(rec, cfg)
    keys = {a["key"]: a for a in res["actions"]}
    assert keys["hostpci0"]["op"] == "delete" and keys["vga"]["value"] == "std"
    assert "max-ram" not in keys["args"]["value"]


def test_fresh_vm_conversion_steps():
    described = igpu_attach.build_fresh_vm_steps(996, "macOS Sequoia 15")
    assert described[0].argv == ["qm", "set", "996", "--description", "macOS Sequoia 15 VM"] and len(described) == 4
    steps = igpu_attach.build_fresh_vm_steps(996)
    assert steps[0].argv == ["qm", "set", "996", "--machine", "pc"]
    assert "--sata1" in steps[1].argv[2] and "order=sata1;virtio0;ide0" in steps[1].argv[2]
    assert "fadt" in steps[2].argv[2]


# ── detection and the multi-GPU recommendation ───────────────────────

def cards(root, macos="sequoia", vms=None):
    return {c.vendor: c for c in session(root, vms).scan(macos=macos)}


def test_igpu_only_is_detected_and_preselected(tmp_path):
    found = cards(host(tmp_path, igpu="i915"))
    card = found["intel"]
    assert card.recommended and card.works and card.status == "works with macOS Sequoia 15"
    assert card.detected_line == ("Intel Iris Xe Graphics (46a6) detected: full macOS acceleration. "
                                  "The host gives up its local screen. One host reboot needed.")


def test_a_prepared_host_skips_the_reboot_wording(tmp_path):
    card = cards(host(tmp_path, igpu="vfio-pci"))["intel"]
    assert card.prepared and "reboot" not in card.detected_line


def test_amd_beats_the_igpu_on_every_release(tmp_path):
    for macos in gpu_setup.MACOS_NAMES:
        found = cards(host(tmp_path / macos, amd=True), macos)
        assert found["amd"].recommended and not found["intel"].recommended and found["intel"].works


def test_the_igpu_beats_nvidia_on_sequoia_and_wins_alone_on_ventura(tmp_path):
    sequoia = cards(host(tmp_path / "s", nvidia=True), "sequoia")
    assert sequoia["intel"].recommended and sequoia["nvidia"].works and not sequoia["nvidia"].recommended
    ventura = cards(host(tmp_path / "v", nvidia=True), "ventura")
    assert ventura["intel"].recommended and not ventura["nvidia"].works
    assert ventura["nvidia"].status == "not supported by macOS Ventura 13 (NullMoth needs macOS 15.5 or newer)"


def lspci(name: str, device: int = 0x28E0) -> dict:
    return {"lspci -nn -s 0000:01:00.0": (0, f"0000:01:00.0 VGA compatible controller [0300]: NVIDIA Corporation {name} [10de:{device:04x}]\n")}


def named_session(root: Path, answers: dict) -> Session:
    return Session(str(root), FakeRun({**REGS, **answers}, default=(0, "")), FakeQm({100: ("stopped", VM)}))


@pytest.mark.parametrize("name", ["AD107M [GeForce RTX 4060 Max-Q / Mobile]", "GA104 [GeForce RTX 3070 Laptop GPU]",
                                  "TU117M [GeForce GTX 1650 Mobile]", "GA106M [Something]"])
def test_a_laptop_nvidia_gpu_is_not_supported_and_the_igpu_is_recommended(tmp_path, name):
    root = host(tmp_path, nvidia=True)
    found = {c.vendor: c for c in named_session(root, lspci(name)).scan()}
    card = found["nvidia"]
    assert card.laptop and card.kind == "unsupported" and not card.works and not card.recommended
    assert card.status == "not supported (laptop NVIDIA)" and card.headline == "Not supported (laptop NVIDIA)"
    assert found["intel"].recommended
    assert "Can't use" in gpu_setup.card_text(card) and "laptop NVIDIA" in gpu_setup.card_text(card)


def test_a_laptop_nvidia_gpu_is_not_supported_on_every_release(tmp_path):
    root = host(tmp_path, nvidia=True)
    for macos in gpu_setup.MACOS_NAMES:
        found = {c.vendor: c for c in named_session(root, lspci("AD107M [GeForce RTX 4060 Max-Q / Mobile]")).scan(macos=macos)}
        assert found["nvidia"].status == "not supported (laptop NVIDIA)" and found["intel"].recommended


def test_a_desktop_nvidia_gpu_next_to_the_igpu_is_unchanged(tmp_path):
    root = host(tmp_path, nvidia=True)
    found = {c.vendor: c for c in named_session(root, lspci("AD107 [GeForce RTX 4060]")).scan()}
    assert not found["nvidia"].laptop and found["nvidia"].kind == "nullmoth" and found["nvidia"].works
    assert found["intel"].recommended and not found["nvidia"].recommended


def test_only_nvidia_parts_are_judged_by_name():
    assert gpu_setup.is_laptop_nvidia("nvidia", "GeForce RTX 4060 Laptop GPU")
    assert not gpu_setup.is_laptop_nvidia("amd", "Radeon RX 6800M Mobile")
    assert not gpu_setup.is_laptop_nvidia("nvidia", "GeForce RTX 4090")
    assert not gpu_setup.is_laptop_nvidia("nvidia", "")


def test_several_stopped_vms_share_one_note(tmp_path):
    held = VM + "hostpci0: 0000:00:02.0,legacy-igd=1\n"
    card = cards(host(tmp_path, igpu="vfio-pci"), vms={7: ("stopped", held), 8: ("stopped", held), 9: ("stopped", held)})["intel"]
    shared = [n for n in card.notes if n.startswith("Also assigned")]
    assert shared == ["Also assigned to stopped VMs 7, 8, 9. Only one can run at a time; Proxmox refuses to start the others."]
    assert gpu.shared_note(7) == "Also assigned to stopped VM 7. Only one can run at a time; Proxmox refuses to start the second."
    assert gpu.shared_note([7]) == gpu.shared_note(7)


def test_the_list_leads_with_the_recommendation(tmp_path):
    order = [c.vendor for c in session(host(tmp_path, amd=True, nvidia=True)).scan()]
    assert order[0] == "amd" and set(order) == {"amd", "intel", "nvidia"}


def test_a_busy_igpu_is_shown_with_the_reason_and_not_preselected(tmp_path):
    busy = VM + "hostpci0: 0000:00:02.0,legacy-igd=1\n"
    found = cards(host(tmp_path, igpu="vfio-pci"), vms={7: ("running", busy)})
    card = found["intel"]
    assert not card.recommended and not card.works and card.status == "in use by VM 7"
    assert "Can't use" in gpu_setup.card_text(card)


def test_an_unsupported_igpu_is_shown_not_preselected(tmp_path):
    card = cards(host(tmp_path, device=0x4C8A))["intel"]
    assert card.kind == "unsupported" and not card.recommended
    assert card.status == "not supported by macOS Sequoia 15"


def test_tahoe_is_marked_experimental_and_unknown_release_falls_back(tmp_path):
    root = host(tmp_path)
    assert "experimental" in cards(root, "tahoe")["intel"].status
    assert cards(root, "bogus")["intel"].macos == "sequoia"


def test_macos_for_prefers_the_flag_then_the_vm(tmp_path):
    s = session(host(tmp_path), {100: ("stopped", VM.replace("igpu-seq", "macos-sonoma"))})
    assert s.macos_for(None, "tahoe") == "tahoe" and s.macos_for(100) == "sonoma"
    assert s.macos_for(None) == "sequoia" and s.macos_for(999) == "sequoia"


def args(**kw):
    base = dict(status=False, undo=False, yes=False, vm=None, gpu="", macos="", display="", accept_risk=False, stop_vm=False)
    return argparse.Namespace(**{**base, **kw})


class Io:
    def __init__(self, answers=(), interactive=True):
        self.said, self.answers, self.interactive = [], list(answers), interactive

    def say(self, text):
        self.said.append(text)

    def ask(self, prompt):
        return self.answers.pop(0) if self.answers else ""

    def confirm(self, prompt, yes):
        return yes or (self.interactive and self.ask(prompt).lower() in ("y", "yes"))

    def typed(self, prompt, phrase):
        return False

    def text(self):
        return "\n".join(self.said)


def choose(root, io, **kw):
    s = session(root)
    return s, gpu_setup_cli._step_choose(s, io, args(**kw))


def test_cli_igpu_only_needs_the_one_confirmation(tmp_path):
    root = host(tmp_path)
    s, rc = choose(root, Io(["y", "p"]))
    assert rc is None and s.state["gpu"]["vendor"] == "intel" and s.state["display"] == "gpu"
    s, rc = choose(host(tmp_path / "decline"), Io(["n"]))
    assert rc == gpu_setup_cli.EXIT_REFUSED and s.state["gpu"] is None


def test_cli_never_enables_the_igpu_without_a_confirmation(tmp_path):
    io = Io(interactive=False)
    s, rc = choose(host(tmp_path), io)
    assert rc == gpu_setup_cli.EXIT_REFUSED and s.state["gpu"] is None and "takes the host console" in io.text()
    s, rc = choose(host(tmp_path / "y"), Io(interactive=False, answers=["p"]), yes=True, vm=None)
    assert rc is None and s.state["gpu"]["vendor"] == "intel"


def test_cli_amd_and_igpu_preselects_amd_and_can_switch(tmp_path):
    root = host(tmp_path, amd=True)
    io = Io(["y", "p"])
    s, rc = choose(root, io)
    assert rc is None and s.state["gpu"]["vendor"] == "amd" and "Recommended: " in io.text()
    # declining the AMD card leaves the iGPU, which is picked from the list like any other card
    s, rc = choose(host(tmp_path / "switch", amd=True), Io(["n", "y", "p"]))
    assert rc is None and s.state["gpu"]["vendor"] == "intel"


def test_cli_lists_every_gpu_with_a_status(tmp_path):
    io = Io(["y", "p"])
    choose(host(tmp_path, amd=True, nvidia=True), io, macos="ventura")
    text = io.text()
    assert "works with macOS Ventura 13" in text and "not supported by macOS Ventura 13" in text


def test_cli_none_works_continues_without_passthrough(tmp_path):
    io = Io()
    s, rc = choose(host(tmp_path, igpu=None, nvidia=True), io, macos="ventura")
    assert rc == gpu_setup_cli.EXIT_OK and "Continuing without passthrough" in io.text() and s.state["gpu"] is None


def test_cli_no_gpu_and_unusable_cards(tmp_path):
    io = Io()
    assert choose(host(tmp_path, igpu=None), io)[1] == gpu_setup_cli.EXIT_REFUSED
    assert "No supported graphics card" in io.text()


def test_choose_forces_the_gpu_display_for_the_igpu(tmp_path):
    s = session(host(tmp_path))
    assert s.choose(IGD, None, "console") == [] and s.state["display"] == "gpu"


def test_vm_options_and_check_vm_for_the_igpu(tmp_path):
    q35 = VM.replace("machine: pc\n", "machine: q35\n")
    s = session(host(tmp_path), {100: ("stopped", VM), 101: ("stopped", q35)})
    s.macos_vms = lambda: ["VMID NAME STATUS", "100 igpu-seq stopped", "101 old stopped"]
    by = {o.vmid: o for o in s.vm_options("intel")}
    assert by[100].eligible and not by[101].eligible and "i440fx" in by[101].why
    assert s.check_vm(100, "intel") == [] and "i440fx" in " ".join(s.check_vm(101, "intel"))


def test_vm_plan_summary_and_first_boot_for_the_igpu(tmp_path):
    root = host(tmp_path, igpu="vfio-pci")
    s = session(root)
    assert s.choose(IGD, 100, "gpu") == []
    plan = s.vm_plan()
    assert plan.ok and any("iGPU" in t.title for t in plan.steps)
    assert "HDMI" in s.first_boot_text() and "Next: plug a monitor" in s.summary()
    s2 = session(host(tmp_path / "x", igpu="i915"))
    s2.choose(IGD, 100, "gpu")
    assert not s2.vm_plan().ok  # the host still holds the iGPU


def test_undo_goes_through_the_intel_record(tmp_path, monkeypatch):
    from osx_proxmox_next import gpu_attach
    plan = gpu_attach.prepare_detach(5, adapter=FakeQm({}), missing_ok=True)
    assert plan.warnings and not plan.steps
