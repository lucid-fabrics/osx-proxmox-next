from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil

from .defaults import detect_cpu_vendor


@dataclass
class PreflightCheck:
    name: str
    ok: bool
    details: str


def _find_binary(cmd: str) -> str | None:
    binary = shutil.which(cmd)
    if binary:
        return binary

    for prefix in ("/usr/sbin", "/sbin", "/usr/bin", "/bin"):
        candidate = Path(prefix) / cmd
        if candidate.exists():
            return str(candidate)
    return None


def _is_root() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


_PROXMOX_BINARIES = ("qm", "pvesm", "pvesh", "qemu-img")

_BUILD_BINARIES: dict[str, str] = {
    "dmg2img": "apt install dmg2img",
    "sgdisk": "apt install gdisk",
    "partprobe": "apt install parted",
    "losetup": "apt install mount",
    "mkfs.fat": "apt install dosfstools",
    "blkid": "apt install util-linux",
}


def _check_ignore_msrs(kvm_conf: Path | None = None) -> PreflightCheck:
    """Check if KVM ignore_msrs=Y is set — critical for macOS (prevents MSR kernel panics)."""
    if kvm_conf is None:
        kvm_conf = Path("/etc/modprobe.d/kvm.conf")
    if kvm_conf.exists():
        content = kvm_conf.read_text()
        if "ignore_msrs=Y" in content:
            return PreflightCheck(
                name="KVM ignore_msrs",
                ok=True,
                details="ignore_msrs=Y set in /etc/modprobe.d/kvm.conf",
            )
    return PreflightCheck(
        name="KVM ignore_msrs",
        ok=False,
        details="Missing ignore_msrs=Y — macOS will kernel panic on unsupported MSR access. "
                "Fix: echo 'options kvm ignore_msrs=Y' >> /etc/modprobe.d/kvm.conf && update-initramfs -k all -u",
    )


def _check_iommu(cmdline_path: Path | None = None) -> PreflightCheck:
    """Check if IOMMU is enabled in kernel cmdline — informational (GPU passthrough)."""
    cmdline = cmdline_path or Path("/proc/cmdline")
    if cmdline.exists():
        content = cmdline.read_text()
        if "intel_iommu=on" in content or "amd_iommu=on" in content:
            return PreflightCheck(
                name="IOMMU enabled",
                ok=True,
                details="IOMMU enabled in kernel cmdline (required for GPU passthrough)",
            )
    return PreflightCheck(
        name="IOMMU enabled",
        ok=True,
        details="IOMMU not detected in kernel cmdline — only needed for GPU passthrough",
    )


def _check_initcall_blacklist(cmdline_path: Path | None = None) -> PreflightCheck:
    """Check for initcall_blacklist=sysfb_init — informational (PVE 8+ GPU passthrough)."""
    cmdline = cmdline_path or Path("/proc/cmdline")
    if cmdline.exists():
        content = cmdline.read_text()
        if "initcall_blacklist=sysfb_init" in content:
            return PreflightCheck(
                name="initcall_blacklist",
                ok=True,
                details="sysfb_init blacklisted in kernel cmdline (PVE 8+ GPU passthrough)",
            )
    return PreflightCheck(
        name="initcall_blacklist",
        ok=True,
        details="initcall_blacklist not set — only needed for PVE 8+ GPU passthrough",
    )


def run_preflight() -> list[PreflightCheck]:
    checks: list[PreflightCheck] = []
    for cmd in _PROXMOX_BINARIES:
        binary = _find_binary(cmd)
        checks.append(
            PreflightCheck(
                name=f"{cmd} available",
                ok=bool(binary),
                details=binary or f"{cmd} not found in PATH or common system paths",
            )
        )

    for cmd, install_hint in _BUILD_BINARIES.items():
        binary = _find_binary(cmd)
        checks.append(
            PreflightCheck(
                name=f"{cmd} available",
                ok=bool(binary),
                details=binary or f"Not found. Install with: {install_hint}",
            )
        )

    checks.append(_check_ignore_msrs())
    checks.append(_check_iommu())
    checks.append(_check_initcall_blacklist())

    vendor = detect_cpu_vendor()
    checks.append(
        PreflightCheck(
            name="CPU vendor",
            ok=True,
            details=f"{vendor} — {'Cascadelake-Server emulation' if vendor == 'AMD' else 'native host passthrough'}",
        )
    )
    checks.append(
        PreflightCheck(
            name="/dev/kvm present",
            ok=Path("/dev/kvm").exists(),
            details="Required for hardware acceleration",
        )
    )
    checks.append(
        PreflightCheck(
            name="Root privileges",
            ok=_is_root(),
            details="Current UID must be root (uid=0) for full workflow",
        )
    )
    return checks
