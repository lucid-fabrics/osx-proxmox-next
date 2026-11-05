from __future__ import annotations

from pathlib import Path

import pytest

from osx_proxmox_next import gpu, nvidia_host
from nullmoth_fakes import add_dev, good_card

V = nvidia_host.NVIDIA_VENDOR


def check(root: Path, bdf: str = "01:00", cmdline: Path | None = None) -> gpu.GpuReport:
    return nvidia_host.check_nvidia_gpu(bdf, root, cmdline)


@pytest.mark.parametrize("raw,want", [
    ("01:00", "0000:01:00"), ("0000:01:00.0", "0000:01:00"), ("0A:1F.3", "0000:0a:1f"),
    ("zz", None), ("1:00", None), ("01:00.9", None),
])
def test_normalize_bdf(raw, want):
    assert gpu.normalize_bdf(raw) == want


def test_allowlist_loads_and_floor():
    ids = nvidia_host.supported_device_ids()
    assert 0x2504 in ids and len(ids) > 200
    assert nvidia_host.is_supported_device(0x1E00 + 0x99)
    assert not nvidia_host.is_supported_device(0x1B80)  # Pascal


def test_detect_lists_supported_and_unsupported(tmp_path):
    good_card(tmp_path)
    add_dev(tmp_path, "0000:02:00.0", V, 0x1B80, group=2)
    add_dev(tmp_path, "0000:03:00.0", 0x1002, 0x73BF, group=3)  # AMD, ignored
    add_dev(tmp_path, "0000:04:00.0", V, 0x2504, cls=0x020000, group=4)  # not display
    found = nvidia_host.detect_nvidia_gpus(tmp_path)
    assert [(g.bdf, g.supported) for g in found] == [("0000:01:00", True), ("0000:02:00", False)]
    assert found[0].functions == ["0000:01:00.0", "0000:01:00.1"]
    assert "supported" in found[0].label
    assert gpu.detect_gpus(0x1002, root=tmp_path)[0].bdf == "0000:03:00"
    assert nvidia_host.detect_nvidia_gpus(tmp_path / "nope") == []


def test_ready_card_passes(tmp_path):
    good_card(tmp_path)
    r = check(tmp_path)
    assert r.ok and r.bdf == "0000:01:00" and not r.warnings


def test_bad_inputs(tmp_path):
    good_card(tmp_path)
    assert "not a PCI address" in check(tmp_path, "nonsense").issues[0]
    assert "No PCI device" in check(tmp_path, "09:00").issues[0]
    add_dev(tmp_path, "0000:05:00.0", 0x1002, 0x73BF, group=5)
    assert "not a vendor" in check(tmp_path, "05:00").issues[0]
    add_dev(tmp_path, "0000:02:00.0", V, 0x1B80, group=2)
    assert "older than Turing" in check(tmp_path, "02:00").issues[0]


def test_unbound_function_prints_vfio_conf(tmp_path):
    add_dev(tmp_path, "0000:01:00.0", V, 0x2504, driver="nvidia")
    add_dev(tmp_path, "0000:01:00.1", V, 0x228E, cls=0x040300, driver=None)
    r = check(tmp_path)
    assert "0000:01:00.0 (nvidia)" in r.issues[0] and "0000:01:00.1 (no driver)" in r.issues[0]
    text = "\n".join(r.hints)
    assert "options vfio-pci ids=10de:2504,10de:228e" in text
    assert "softdep nouveau pre: vfio-pci" in text and "xhci_pci" not in text
    assert "update-initramfs -u -k all" in text


def test_usb_c_functions_add_softdeps(tmp_path):
    add_dev(tmp_path, "0000:01:00.0", V, 0x1E84, driver="nouveau")
    add_dev(tmp_path, "0000:01:00.2", V, 0x1AD8, cls=0x0C0330, driver="nouveau")
    text = "\n".join(check(tmp_path).hints)
    assert "softdep xhci_pci pre: vfio-pci" in text and "i2c_nvidia_gpu" in text


def test_iommu_inactive(tmp_path):
    good_card(tmp_path)
    (tmp_path / "bus/pci/devices/0000:01:00.0/iommu_group").unlink()
    assert "IOMMU is not active" in check(tmp_path).issues[0]


def test_group_with_foreign_device(tmp_path):
    good_card(tmp_path)
    add_dev(tmp_path, "0000:00:1f.3", 0x8086, 0x1234, cls=0x040300, group=1)
    add_dev(tmp_path, "0000:00:01.0", 0x8086, 0x1235, cls=0x060400, group=1)  # bridge ok
    r = check(tmp_path)
    assert "0000:00:1f.3" in r.issues[0] and "0000:00:01.0" not in r.issues[0]


def test_warnings_boot_gpu_and_bar(tmp_path):
    good_card(tmp_path, boot_vga=True, bar1=8 * 1024**3)
    cmd = tmp_path / "cmdline"
    cmd.write_text("quiet")
    r = check(tmp_path, cmdline=cmd)
    assert r.ok and len(r.warnings) == 2 and "8192 MiB" in r.warnings[1]
    cmd.write_text("quiet initcall_blacklist=sysfb_init")
    assert len(check(tmp_path, cmdline=cmd).warnings) == 1


def test_bad_resource_file_is_ignored(tmp_path):
    good_card(tmp_path)
    (tmp_path / "bus/pci/devices/0000:01:00.0/resource").write_text("garbage\n")
    assert check(tmp_path).ok


def test_mmio_arg_idempotent_and_preserving():
    a = '-device isa-applesmc,osk="x" -cpu host'
    once = gpu.with_mmio_arg(a)
    assert once == a + " " + gpu.MMIO_ARG
    assert gpu.with_mmio_arg(once) == once
    assert gpu.with_mmio_arg(a + " -global q35-pcihost.pci-hole64-size=32G") == once
    assert gpu.without_mmio_arg(once) == a
    assert gpu.without_mmio_arg(a) == a
    assert gpu.with_mmio_arg("") == gpu.MMIO_ARG


def test_hostpci_steps():
    steps = gpu.build_hostpci_attach_steps(100, "0000:01:00", "-cpu host")
    assert steps[0].argv == ["qm", "set", "100", "--hostpci0", "0000:01:00,pcie=0"]
    assert steps[1].argv[-1] == "-cpu host " + gpu.MMIO_ARG
    assert len(gpu.build_hostpci_attach_steps(100, "0000:01:00", "-cpu host " + gpu.MMIO_ARG)) == 1
    d = gpu.build_hostpci_detach_steps(100, gpu.MMIO_ARG, True)
    assert d[0].argv[-2:] == ["--delete", "hostpci0"] and d[1].argv == ["qm", "set", "100", "--delete", "args"]
    assert gpu.build_hostpci_detach_steps(100, "-cpu host", False) == []
    assert gpu.build_hostpci_detach_steps(100, "-cpu host " + gpu.MMIO_ARG, False)[0].argv[-1] == "-cpu host"
