"""Fake host tree and scripted commands for the GPU walkthrough tests."""
from __future__ import annotations

from pathlib import Path

from nullmoth_fakes import add_dev


def write(root: Path, path: str, text: str) -> Path:
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


class FakeRun:
    """run(argv) -> (code, output). Keys are the argv joined by spaces; a prefix match works too."""

    def __init__(self, answers: dict[str, tuple[int, str]] | None = None, default: tuple[int, str] = (127, "")) -> None:
        self.answers = answers or {}
        self.default = default
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout: int = 20) -> tuple[int, str]:
        self.calls.append(list(argv))
        key = " ".join(argv)
        if key in self.answers:
            return self.answers[key]
        for prefix, answer in self.answers.items():
            if key.startswith(prefix):
                return answer
        return self.default

    def ran(self, *prefix: str) -> bool:
        return any(call[: len(prefix)] == list(prefix) for call in self.calls)


EFI_GRUB = ("BootCurrent: 0000\nBoot0000* proxmox\tHD(2,GPT)/File(\\EFI\\proxmox\\shimx64.efi)\n"
            "Boot0001* UEFI OS\tHD(1,GPT)/File(\\EFI\\systemd\\systemd-bootx64.efi)\n")
EFI_SDBOOT = "BootCurrent: 0001\nBoot0000* proxmox\tFile(\\EFI\\proxmox\\grubx64.efi)\nBoot0001* Linux Boot Manager\tFile(\\EFI\\systemd\\systemd-bootx64.efi)\n"


def make_host(root: Path, cpu: str = "intel", efi: bool = True, cmdline: str = "BOOT_IMAGE=/boot/vmlinuz root=/dev/x ro quiet",
              virt: bool = True, tables: tuple[str, ...] = ("DMAR",), groups: int = 1, board: tuple[str, str] = ("ASUSTeK COMPUTER INC.", "PRIME X670-P")) -> Path:
    vendor = "GenuineIntel" if cpu == "intel" else "AuthenticAMD"
    write(root, "/proc/cpuinfo", f"vendor_id\t: {vendor}\nflags\t\t: fpu {'vmx' if cpu == 'intel' else 'svm'} sse\n" if virt else f"vendor_id\t: {vendor}\nflags\t\t: fpu sse\n")
    write(root, "/proc/cmdline", cmdline + "\n")
    write(root, "/sys/class/dmi/id/board_vendor", board[0] + "\n")
    write(root, "/sys/class/dmi/id/board_name", board[1] + "\n")
    write(root, "/sys/class/dmi/id/sys_vendor", board[0] + "\n")
    write(root, "/sys/class/dmi/id/bios_vendor", "American Megatrends\n")
    if efi:
        (root / "sys/firmware/efi/efivars").mkdir(parents=True, exist_ok=True)
        (root / "sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c").write_bytes(b"\x06\x00\x00\x00\x00")
    for table in tables:
        write(root, f"/sys/firmware/acpi/tables/{table}", "x")
    (root / "sys/kernel/iommu_groups").mkdir(parents=True, exist_ok=True)
    for idx in range(groups):
        (root / f"sys/kernel/iommu_groups/{idx + 100}").mkdir(parents=True, exist_ok=True)
    return root


def add_card(root: Path, bdf: str = "0000:06:00", vendor: int = 0x1002, device: int = 0x73FF, audio: int = 0xAB28,
             driver: str | None = "vfio-pci", group: int = 7, boot_vga: bool = False, bar0: int = 256 * 1024 * 1024) -> None:
    sysfs = root / "sys"
    add_dev(sysfs, f"{bdf}.0", vendor, device, cls=0x030000, driver=driver, group=group, boot_vga=boot_vga, bar1=bar0)
    # AMD BAR0 is the VRAM aperture: line one of the resource file
    res = sysfs / "bus/pci/devices" / f"{bdf}.0" / "resource"
    res.write_text(f"0x{0x100000000:016x} 0x{0x100000000 + bar0 - 1:016x} 0x000000000014220c\n"
                   "0x0000000000000000 0x0000000000000000 0x0000000000000000\n")
    add_dev(sysfs, f"{bdf}.1", vendor, audio, cls=0x040300, driver=driver, group=group)


def add_other(root: Path, bdf: str, vendor: int, device: int, cls: int, group: int, driver: str | None = "nvme") -> None:
    add_dev(root / "sys", bdf, vendor, device, cls=cls, driver=driver, group=group)
