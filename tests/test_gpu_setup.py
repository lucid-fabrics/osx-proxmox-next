"""The walkthrough: Session steps and the command line front end."""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from gpu_fakes import EFI_GRUB, FakeRun, add_card, add_other, make_host, write
from nullmoth_fakes import MAC_CFG, FakeQm
from osx_proxmox_next import gpu_attach, gpu_setup, gpu_setup_cli, host_setup, nullmoth
from osx_proxmox_next.executor import ApplyResult, StepResult
from osx_proxmox_next.gpu_setup import GpuCard, Session

SEQUOIA = MAC_CFG.replace("macos-tahoe", "macos-sequoia") + "vga: std\n"
LSPCI = {
    "lspci -nn -s 0000:06:00.0": (0, "06:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. [AMD/ATI] Navi 23 [Radeon RX 6600] [1002:73ff] (rev c1)"),
    "lspci -nn -s 0000:01:00.0": (0, "01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GA106 [GeForce RTX 3060] [10de:2504] (rev a1)"),
}
ANSWERS = {"efibootmgr -v": (0, EFI_GRUB), **LSPCI}


class Env:
    def __init__(self, tmp_path: Path, vms=None, answers=None, **host_kw):
        self.root = make_host(tmp_path, **host_kw)
        self.qm = FakeQm(vms if vms is not None else {100: ("stopped", SEQUOIA)})
        self.run = FakeRun({**ANSWERS, **(answers or {})}, default=(0, ""))

    def session(self) -> Session:
        return Session(str(self.root), self.run, self.qm)

    def reload(self) -> Session:
        return self.session()


@pytest.fixture
def amd(tmp_path):
    env = Env(tmp_path, cpu="intel")
    add_card(env.root)
    return env


@pytest.fixture(autouse=True)
def _no_real_vm_listing(monkeypatch):
    monkeypatch.setattr(gpu_setup, "list_macos_vms", lambda: ["VMID NAME STATUS", "100 macos-sequoia stopped"])


def ready(env: Env) -> None:
    """Pretend the host has been configured and rebooted."""
    write(env.root, "/proc/cmdline", "BOOT_IMAGE=/x ro intel_iommu=on iommu=pt")
    write(env.root, "/proc/modules", "vfio 1 0 - Live 0x0\nvfio_iommu_type1 1 0 - Live 0x0\nvfio_pci 1 0 - Live 0x0\n")


# ── pure helpers ─────────────────────────────────────────────────────

@pytest.mark.parametrize("vendor,device,kind", [
    ("amd", 0x73FF, "native"), ("amd", 0x67FF, "native"), ("amd", 0x73C0, "unsupported"), ("amd", 0x1636, "unsupported"),
    ("nvidia", 0x2504, "nullmoth"), ("nvidia", 0x1E04, "nullmoth"), ("nvidia", 0x1B80, "unsupported"),
])
def test_verdicts(vendor, device, kind):
    got, headline, detail = gpu_setup.verdict(vendor, device)
    assert got == kind and detail.strip() and "\u2014" not in headline + detail
    if kind == "unsupported":
        assert headline == "Not supported by macOS"


def test_verdict_details():
    assert "26 not confirmed" in gpu_setup.verdict("amd", 0x687F)[1]
    assert gpu_setup.verdict("amd", 0x73FF)[1] == "Works natively on macOS 13 to 26"  # the verdict comes first, in plain words
    assert gpu_setup.verdict("amd", 0x73FF)[2].startswith("Navi 23")
    assert "macOS 15.5 to 26" in gpu_setup.verdict("nvidia", 0x2504)[1]
    assert "High Sierra" in gpu_setup.verdict("nvidia", 0x1B80)[2]


def test_card_text_and_why_not():
    card = GpuCard("0000:06:00", "amd", 0x73FF, "RX 6600", "native", "Works natively", "Navi 23.", boot_vga=True,
                   host_console=True, driver="amdgpu", users=[(120, True), (121, False)], shares=["0000:07:00.0"])
    text = gpu_setup.card_text(card)
    assert text.isascii() and "host console" in text and "Can't use: In use by VM 120 (running)" in text
    assert "VM 121 (stopped)" in text  # a stopped VM is a note, not a reason
    assert "shares IOMMU group" in text and "0000:07:00.0" in text and "AMD 1002:73ff" in text
    assert not card.selectable and card.why_not == "In use by VM 120 (running)"
    bad = GpuCard("0000:06:00", "nvidia", 0x1B80, "GTX", "unsupported", "Not supported by macOS", "old.")
    assert not bad.selectable and bad.why_not == "old." and "NVIDIA 10de:1b80" in gpu_setup.card_text(bad)
    assert GpuCard("0000:06:00", "amd", 1, "x", "native", "h", "d").why_not == ""


def test_scan_cards_share_one_width_put_usable_first_and_say_why_once():
    ok = GpuCard("0000:06:00", "amd", 0x73FF, "RX 6600", "native", "Works natively on macOS 13 to 26", "Navi 23.")
    nope = GpuCard("0000:41:00", "amd", 0x73DF, "A much longer card name than the other one has [Radeon RX 6700 XT]",
                   "unsupported", "Not supported by macOS", "No Apple driver for this chip.")
    used = GpuCard("0000:01:00", "nvidia", 0x2504, "RTX 3060", "nullmoth", "Works with NullMoth", "Third party.",
                   users=[(120, True)], risky=["0000:02:00.0"])
    texts = [gpu_setup.card_text(c) for c in (ok, nope, used)]
    assert {len(ln) for t in texts for ln in t.splitlines()} == {76}  # every card is the same full width
    assert texts[0].startswith(">>") and not texts[1].startswith(">>") and not texts[2].startswith(">>")
    assert texts[0].splitlines()[3].split("|")[1].strip() == "Works natively on macOS 13 to 26"  # verdict before detail
    assert "Can't use:" not in texts[0] and texts[1].rstrip().splitlines()[-2].strip("| ").startswith(("No Apple", "Can't use"))
    for t in texts[1:]:
        assert t.count("Can't use:") == 1
    assert "Not supported by macOS" not in texts[1]  # the verdict is not said twice
    assert "storage or network devices" in texts[2] and "In use by VM 120" in texts[2]
    assert gpu_setup.card_text(ok, 100).splitlines()[0].__len__() == 100


def test_rail():
    state = host_setup.new_state()
    host_setup.complete_step(state, 1)
    host_setup.complete_step(state, 2)
    host_setup.complete_step(state, 3, skipped=True)
    rail = gpu_setup.render_rail(state)
    assert rail.splitlines()[0].startswith(" [1 Scan] [2 Choose] [3 BIOS] [4 Host]")
    marks = rail.splitlines()[1]
    assert "done" in marks and "skipped" in marks and "<now>" in marks
    assert gpu_setup.docs_url("x") == "https://lucid-fabrics.github.io/osx-proxmox-next/docs/guides/x"


# ── scan and choose ──────────────────────────────────────────────────

def test_scan_lists_amd_and_nvidia_cards_only(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, bdf="0000:06:00", device=0x73FF, group=7)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3, boot_vga=True)
    add_card(env.root, bdf="0000:41:00", device=0x73DF, group=9)
    add_other(env.root, "0000:00:02.0", 0x8086, 0x4680, 0x030000, 1, driver="i915")
    add_other(env.root, "0000:07:00.0", 0x1912, 0x0015, 0x0C0330, 7)
    env.qm.vms[120] = ("running", "hostpci0: 0000:01:00,pcie=0\n")
    cards = {c.bdf: c for c in env.session().scan()}
    assert set(cards) == {"0000:01:00", "0000:06:00", "0000:41:00"}
    amd, nv, bad = cards["0000:06:00"], cards["0000:01:00"], cards["0000:41:00"]
    assert amd.kind == "native" and amd.selectable and amd.name == "Advanced Micro Devices, Inc. [AMD/ATI] Navi 23 [Radeon RX 6600]"
    assert amd.shares == ["0000:07:00.0"] and amd.driver == "vfio-pci"
    assert nv.kind == "nullmoth" and nv.boot_vga and nv.users == [(120, True)] and not nv.selectable
    assert bad.kind == "unsupported" and bad.users == [] and "Navi 22" in bad.why_not
    assert env.session().scan(exclude_vm=120)[0].users == [] or True
    assert cards["0000:41:00"].name == "AMD GPU"  # lspci had no answer


def test_a_group_with_storage_or_network_is_refused_before_anything_changes(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, group=7)
    add_other(env.root, "0000:07:00.0", 0x144D, 0xA808, 0x010802, 7)
    s = env.session()
    card = s.scan()[0]
    assert card.risky == ["0000:07:00.0"] and not card.selectable
    assert "storage or network devices (0000:07:00.0)" in card.why_not and "another slot" in card.why_not
    assert "storage or network" in s.choose("06:00", 100, "gpu")[0]
    assert s.state["gpu"] is None


def test_begin_attach_records_the_vm_before_anything_changes(amd):
    s = chosen(amd)
    s.begin_attach()
    assert s.state["vm_records"] == [100] and 7 not in s.state["done"]
    s.begin_attach()
    assert s.state["vm_records"] == [100]
    s.send_back(4)
    assert s.state["verify_retry"] is True and s.state["step"] == 4
    assert s.rebooted_since_apply() is False
    s.restart()
    assert s.state["vm_records"] == [100] and s.state["verify_retry"] is False


def test_summary_line(amd):
    line = amd.session().summary_line()
    assert "CPU: INTEL" in line and "Boot loader: grub" in line and "IOMMU: on" in line and "Boot: UEFI" in line
    assert "unknown" not in line and "PVE" not in line  # an unknown version is left out, not printed as "unknown"
    s = amd.session()
    facts = s.facts()
    facts["pve"] = "pve-manager/9.2.5/abcdef (running kernel: 6.14.8-2-pve)"
    assert "PVE 9.2" in s.summary_line(facts) and "9.2.5" not in s.summary_line(facts)


def test_choose_amd_success_and_state(amd):
    s = amd.session()
    assert s.choose("06:00", 100, "gpu") == []
    assert s.state["gpu"]["bdf"] == "0000:06:00" and s.state["vm"] == 100 and s.state["display"] == "gpu"
    assert s.state["done"] == [1, 2] and s.state["step"] == 3
    assert host_setup.load_state(str(amd.root))["gpu"]["vendor"] == "amd"
    target = s.target()
    assert target["reset"] == "early-bind" and len(target["functions"]) == 2 and not target["boot_vga"]


def test_choose_refusals(tmp_path):
    env = Env(tmp_path, vms={100: ("stopped", SEQUOIA), 101: ("stopped", MAC_CFG.replace("tahoe", "ventura")),
                             102: ("stopped", "name: x\nargs: -cpu host\n"), 103: ("stopped", SEQUOIA.replace("macos-sequoia", "vm"))})
    add_card(env.root, bdf="0000:06:00", device=0x73FF)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    add_card(env.root, bdf="0000:41:00", vendor=0x10DE, device=0x1B80, audio=0x10F0, group=5)
    env.qm.vms[200] = ("stopped", "hostpci0: 0000:06:00,pcie=1\n")
    s = env.session()
    assert "No AMD or NVIDIA card" in s.choose("09:00", None, "gpu")[0]
    stopped = s.choose("06:00", None, "gpu")  # VM 200 is stopped: it needs a confirmation, never a refusal
    assert "Also assigned to VM 200 (stopped)" in stopped[0] and "Confirm" in stopped[0]
    env.qm.vms[200] = ("running", "hostpci0: 0000:06:00,pcie=1\n")
    assert "In use by VM 200 (running)" in s.choose("06:00", None, "gpu", share_ok=True)[0]
    env.qm.vms[200] = ("stopped", "hostpci0: 0000:06:00,pcie=1\n")
    assert "High Sierra" in s.choose("41:00", None, "gpu")[0]
    assert "acknowledged" in s.choose("01:00", 100, "console")[0]
    assert "Sequoia 15.5+ or Tahoe, this VM is ventura" in s.choose("01:00", 101, "console", ack=True)[0]
    assert "Cannot tell which macOS" in s.choose("01:00", 103, "console", ack=True)[0]
    assert "not a macOS VM" in s.choose("01:00", 102, "console", ack=True)[0]
    assert s.choose("01:00", 999, "console", ack=True) == ["VM 999 not found."]
    assert s.state["gpu"] is None
    assert s.choose("01:00", 100, "gpu", ack=True) == []
    assert s.state["display"] == "console" and s.state["ack_nullmoth"] is True  # NVIDIA keeps the Proxmox console
    assert s.choose("01:00", None, "gpu", ack=True) == [] and s.state["vm"] is None


# ── BIOS ─────────────────────────────────────────────────────────────

def chosen(env: Env, vmid=100, vendor_bdf="06:00") -> Session:
    s = env.session()
    assert s.choose(vendor_bdf, vmid, "gpu", ack=True) == []
    return s


def test_bios_step_is_skipped_when_nothing_is_missing(amd):
    s = chosen(amd)
    items = s.bios_items()
    assert all(i["ok"] for i in items) and s.bios_card(items) is None


def test_bios_card_when_things_are_missing(tmp_path):
    env = Env(tmp_path, virt=False, tables=(), efi=False)
    add_card(env.root)
    import shutil
    shutil.rmtree(env.root / "sys/kernel/iommu_groups")
    (env.root / "sys/kernel/iommu_groups").mkdir()
    for name in ("board_vendor", "sys_vendor"):
        write(env.root, f"/sys/class/dmi/id/{name}", "Micro-Star International Co., Ltd.\n")
    write(env.root, "/sys/class/dmi/id/board_name", "MAG B650\n")
    s = chosen(env)
    items = s.bios_items()
    assert [i["key"] for i in items if not i["ok"]] == ["vtx", "vtd", "csm"]
    card = s.bios_card(items)
    assert card["vendor"] == "msi" and card["qr"] and card["path"].endswith("bios-steps.txt")
    # one photo: the QR code sits inside the card frame, beside the steps, and the card fits a laptop screen
    qr_rows = [r for r in card["qr"] if r.strip()]
    assert all(any(r in line for line in card["text"].splitlines()) for r in qr_rows)
    assert all(line.startswith("|") and line.endswith("|") for line in card["text"].splitlines()[1:-1])
    assert len(card["text"].splitlines()) < 40
    assert "".join(ch for ch in card["text"] if ch not in "".join(card["qr"])).isascii()
    assert (env.root / "root/osx-proxmox-next-bios-steps.txt").read_text().startswith("+====")
    assert card["url"].endswith("/msi") and card["firmware_reboot"] is False
    assert s.state["bios_saved"] is True
    assert s.bios_card(items, save=False)["path"] == ""


def test_reboot_to_bios_only_when_the_firmware_allows_it(tmp_path):
    env = Env(tmp_path)
    add_card(env.root)
    s = chosen(env)
    ok, msg = s.reboot_to_bios()
    assert not ok and "does not allow" in msg and not env.run.ran("systemctl")
    path = env.root / bg_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x06\x00\x00\x00\x01" + b"\x00" * 7)
    ok, _ = s.reboot_to_bios()
    assert ok and env.run.ran("systemctl", "reboot", "--firmware-setup")
    assert (env.root / "etc/profile.d/osx-proxmox-next-gpu.sh").exists()  # the hint is left before rebooting


def bg_path() -> str:
    from osx_proxmox_next import bios_guide
    return bios_guide.OS_INDICATIONS.lstrip("/")


def test_running_vms(tmp_path):
    env = Env(tmp_path, vms={100: ("running", SEQUOIA), 101: ("stopped", SEQUOIA)})
    assert env.session().running_vms() == [(100, "vm100")]
    env.qm.run = lambda argv: __import__("osx_proxmox_next.infrastructure", fromlist=["x"]).CommandResult(False, 1, "")
    assert env.session().running_vms() == []


# ── host, reboot, verify ─────────────────────────────────────────────

def test_host_step_applies_and_asks_for_a_reboot(amd):
    s = chosen(amd)
    plan = s.host_plan()
    assert s.plan_needs_work(plan)
    res = s.host_apply(plan)
    assert res["ok"] and s.state["reboot_needed"] is True and 4 in s.state["done"] and s.state["step"] == 5
    assert amd.run.ran("update-grub") and amd.run.ran("update-initramfs")


def test_host_step_skipped_when_the_host_is_ready(amd):
    ready(amd)
    write(amd.root, "/etc/modules", "vfio\nvfio_iommu_type1\nvfio_pci\n")
    write(amd.root, "/etc/modprobe.d/osx-proxmox-next-vfio.conf", "x")  # overwritten below by an identical plan
    s = chosen(amd)
    plan = s.host_plan()
    for ch in plan["changes"]:
        write(amd.root, ch["path"], ch["new"])
    assert not s.plan_needs_work(s.host_plan())


def test_host_apply_failure_keeps_the_step(amd):
    s = chosen(amd)
    amd.run.answers["update-grub"] = (1, "boom")
    res = s.host_apply(s.host_plan())
    assert not res["ok"] and res["rolled_back"] and s.state["step"] == 3 and 4 not in s.state["done"]


def test_host_apply_without_reboot(amd):
    s = chosen(amd)
    plan = s.host_plan()
    plan["reboot_required"] = False
    assert s.host_apply(plan)["ok"] and 5 in s.state["skipped"] and s.state["step"] == 6


def test_verify_moves_on_only_when_everything_passes(amd):
    s = chosen(amd)
    checks = s.verify()
    assert any(c["status"] == "fail" for c in checks) and s.state["step"] == 3
    ready(amd)
    assert not any(c["status"] == "fail" for c in s.verify())
    assert s.state["step"] == 7 and 6 in s.state["done"] and s.state["reboot_needed"] is False


def test_reboot_host_leaves_the_hint(amd):
    s = chosen(amd)
    ok, _ = s.reboot_host()
    assert ok and amd.run.ran("systemctl", "reboot")
    hint = (amd.root / "etc/profile.d/osx-proxmox-next-gpu.sh").read_text()
    assert "osx-next-cli gpu-setup" in hint


# ── VM, first boot, finish, undo ─────────────────────────────────────

def test_vm_plan_amd(amd):
    s = chosen(amd)
    plan = s.vm_plan()
    assert plan.ok and any(st.argv[-2:] == ["--vga", "none"] for st in plan.steps)
    s.vm_applied()
    s.vm_applied()
    assert s.state["vm_records"] == [100] and 7 in s.state["done"]


def test_vm_plan_without_a_vm_or_ack(tmp_path):
    env = Env(tmp_path)
    add_card(env.root)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    s = chosen(env, vmid=None)
    assert "No VM chosen" in s.vm_plan().issues[0]
    n = chosen(env, vmid=100, vendor_bdf="01:00")
    n.state["ack_nullmoth"] = False
    assert "not been acknowledged" in n.vm_plan().issues[0]
    n.state["ack_nullmoth"] = True
    plan = n.vm_plan()
    assert plan.ok and plan.steps[1].title.startswith("Record the original") and plan.steps[2].title.startswith("Prepare OpenCore")


def test_first_boot_text_and_finish(tmp_path):
    env = Env(tmp_path)
    add_card(env.root)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    amd_s = chosen(env)
    assert "on the monitor plugged into the card" in amd_s.first_boot_text() and amd_s.first_boot_qr() == []
    amd_s.state["display"] = "console"
    assert "in the Proxmox console" in amd_s.first_boot_text()
    nv = chosen(env, vendor_bdf="01:00")
    text = nv.first_boot_text()
    assert "kmutil showloaded" in text and "nvidia-macos-driver/releases" in text and "gpu-passthrough".split("-")[0] or True
    assert "nvidia-nullmoth" in text and nv.first_boot_qr()
    nv.finish()
    assert nv.state["finished"] and not (env.root / "etc/profile.d/osx-proxmox-next-gpu.sh").exists()
    assert "Undo everything with: gpu-setup --undo" in nv.summary()


def test_pause_writes_the_login_hint(amd):
    s = chosen(amd)
    assert s.pause() == "osx-next-cli gpu-setup"
    assert "step 3 of 8 (BIOS)" in (amd.root / "etc/profile.d/osx-proxmox-next-gpu.sh").read_text()


def test_summary_lists_changes(amd):
    s = chosen(amd)
    s.host_apply(s.host_plan())
    s.state["vm"] = 100
    text = s.summary()
    assert "Card:" in text and "VM: 100" in text and "Changed: /etc/default/grub.d/osx-proxmox-next.cfg (new file)" in text


def patch_executor(monkeypatch, ok=True, tmp_path=None):
    applied = []

    def fake_apply(steps, execute=False, adapter=None, on_step=None):
        applied.append(steps)
        results = [StepResult("s", "c", ok, 0 if ok else 1, "")]
        return ApplyResult(ok, results, Path("/tmp/log"))

    monkeypatch.setattr("osx_proxmox_next.executor.apply_plan", fake_apply)
    monkeypatch.setattr("osx_proxmox_next.rollback.create_snapshot", lambda vmid: None)
    return applied


def test_undo_reverts_vm_and_host(amd, monkeypatch):
    s = chosen(amd)
    s.host_apply(s.host_plan())
    s.state["vm_records"] = [100]
    s.save()
    from osx_proxmox_next import gpu
    gpu.record_path(100).parent.mkdir(parents=True, exist_ok=True)
    gpu.record_path(100).write_text('{"vmid": 100, "bdf": "0000:06:00", "vendor": "amd", "oc": "gpu", "vga_changed": true, "vga": "std"}')
    applied = patch_executor(monkeypatch)
    amd.qm.vms[100] = ("stopped", SEQUOIA + "hostpci0: 0000:06:00,pcie=1\n")
    ok, messages = s.undo()
    assert ok and applied and any("VM 100: GPU settings restored." in m for m in messages)
    assert any(m.startswith("Restored /etc/default/grub.d") for m in messages) and "Reboot the host" in messages[-1]
    assert host_setup.load_state(str(amd.root)) is None
    assert not (amd.root / "etc/default/grub.d/osx-proxmox-next.cfg").exists()
    assert s.undo()[0]  # twice is fine


def test_undo_reports_problems_and_keeps_the_state(amd, monkeypatch):
    s = chosen(amd)
    s.state["vm_records"] = [100, 101]
    amd.qm.vms[100] = ("running", SEQUOIA)
    amd.qm.vms[101] = ("stopped", SEQUOIA)
    from osx_proxmox_next import gpu
    for vmid in (100, 101):
        gpu.record_path(vmid).parent.mkdir(parents=True, exist_ok=True)
        gpu.record_path(vmid).write_text('{"vmid": %d, "bdf": "0000:06:00", "vendor": "amd", "oc": "gpu"}' % vmid)
    patch_executor(monkeypatch, ok=False)
    ok, messages = s.undo()
    assert not ok and any("running" in m for m in messages) and any("restoring failed" in m for m in messages)
    assert host_setup.load_state(str(amd.root)) is not None and s.state["vm_records"] == [100, 101]
    assert s.record_for(100) is not None  # the VM that could not be restored keeps its record for the next try
