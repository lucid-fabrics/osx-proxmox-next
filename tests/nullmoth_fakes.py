"""Shared fakes for the NullMoth/GPU tests: a sysfs tree and a scripted qm."""
from __future__ import annotations

import plistlib
from pathlib import Path

from osx_proxmox_next.infrastructure import CommandResult

GUID = "7C436110-AB2A-4BBB-A880-FE41995C9F82"


def add_dev(root: Path, bdf: str, vendor: int, device: int, cls: int = 0x030000,
            driver: str | None = "vfio-pci", group: int = 1, boot_vga: bool = False,
            bar1: int = 256 * 1024 * 1024, subsys: tuple[int, int] = (0x1043, 0x8800)) -> None:
    dev = root / "bus/pci/devices" / bdf
    dev.mkdir(parents=True)
    (dev / "subsystem_vendor").write_text(f"0x{subsys[0]:04x}\n")
    (dev / "subsystem_device").write_text(f"0x{subsys[1]:04x}\n")
    (dev / "vendor").write_text(f"0x{vendor:04x}\n")
    (dev / "device").write_text(f"0x{device:04x}\n")
    (dev / "class").write_text(f"0x{cls:06x}\n")
    (dev / "boot_vga").write_text("1\n" if boot_vga else "0\n")
    (dev / "resource").write_text(
        "0x00000000f0000000 0x00000000f0ffffff 0x0000000000040200\n"
        f"0x0000000100000000 0x{0x100000000 + bar1 - 1:016x} 0x000000000014220c\n"
    )
    if driver:
        target = root / "bus/pci/drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        (dev / "driver").symlink_to(target)
    grp = root / "kernel/iommu_groups" / str(group)
    (grp / "devices").mkdir(parents=True, exist_ok=True)
    link = grp / "devices" / bdf
    if link.is_symlink():  # a left-over, possibly dangling, link from a removed device
        link.unlink()
    link.symlink_to(dev)
    (dev / "iommu_group").symlink_to(grp)


def good_card(root: Path, device: int = 0x2504, **kw) -> None:
    add_dev(root, "0000:01:00.0", 0x10DE, device, **kw)
    add_dev(root, "0000:01:00.1", 0x10DE, 0x228E, cls=0x040300, **kw)


def oc_config(csr: bytes = b"\x67\x0f\x00\x00", sbm: str = "Disabled", model: str = "iMacPro1,1",
              boot_args: str = "keepsyms=1 debug=0x100 revblock=pci nv_disable=1") -> dict:
    return {
        "Kernel": {"Add": [{"BundlePath": "Lilu.kext", "Enabled": True},
                           {"BundlePath": "VirtualSMC.kext", "Enabled": True}],
                   "Block": [{"Identifier": "com.apple.driver.AppleTyMCEDriver", "Enabled": False}]},
        "Misc": {"Security": {"SecureBootModel": sbm}},
        "NVRAM": {"Add": {GUID: {"csr-active-config": csr, "boot-args": boot_args}}},
        "PlatformInfo": {"Generic": {"SystemProductName": model}},
    }


def write_oc(oc_dir: Path, **kw) -> bytes:
    (oc_dir / "Kexts").mkdir(parents=True, exist_ok=True)
    path = oc_dir / "config.plist"
    path.write_bytes(plistlib.dumps(oc_config(**kw)))
    return path.read_bytes()


class FakeQm:
    """Scripted ProxmoxAdapter: vms = {vmid: (status, config_text)}."""

    def __init__(self, vms: dict[int, tuple[str, str]]) -> None:
        self.vms = vms
        self.calls: list[list[str]] = []

    def run(self, argv: list[str]) -> CommandResult:
        self.calls.append(argv)
        if argv[:2] == ["qm", "status"]:
            vm = self.vms.get(int(argv[2]))
            return CommandResult(bool(vm), 0 if vm else 2, f"status: {vm[0]}" if vm else "no such VM")
        if argv[:2] == ["qm", "config"]:
            vm = self.vms.get(int(argv[2]))
            return CommandResult(bool(vm), 0 if vm else 2, vm[1] if vm else "")
        if argv[:2] == ["qm", "list"]:
            rows = "\n".join(f"{v} vm{v} {st} 4096 64 1" for v, (st, _c) in self.vms.items())
            return CommandResult(True, 0, "VMID NAME STATUS MEM BOOTDISK PID\n" + rows)
        return CommandResult(True, 0, "")

    def qm(self, *a: str) -> CommandResult:
        return self.run(["qm", *a])


MAC_CFG = ('name: macos-tahoe\n'
           'args: -device isa-applesmc,osk="x" -smbios type=2 -cpu host\n'
           'ide0: local-lvm:vm-1-disk-1,media=disk\n')
