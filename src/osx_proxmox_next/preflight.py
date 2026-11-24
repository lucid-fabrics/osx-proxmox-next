from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import shutil

from .defaults import detect_cpu_info, detect_cpu_vendor
from .infrastructure import ProxmoxAdapter, get_proxmox_adapter

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FixCommand:
    """One command of an automatic fix.

    *display* is the exact text shown to the user and *argv* what runs. Both
    are constants (an apt package name comes from a fixed table), never built
    from VM or user supplied text."""
    display: str
    argv: tuple[str, ...]


@dataclass
class PreflightCheck:
    name: str
    ok: bool
    details: str
    fix: tuple[FixCommand, ...] = ()   # empty when there is no safe automatic fix

    @property
    def why(self) -> str:
        """One plain sentence on why this check matters."""
        return WHY.get(self.name) or WHY.get(self.name.split()[0] + " available", "")

    @property
    def problem(self) -> str:
        """What is wrong when this check fails, in a sentence (never the title it carries when it passes)."""
        name = self.name
        if name.endswith(" available"):
            return f"{name.split()[0]} is not installed"
        known = {
            "KVM ignore_msrs": "KVM ignore_msrs is off",
            "/dev/kvm present": "/dev/kvm is missing",
            "Root privileges": "osx-next is not running as root",
            "IOMMU enabled": "IOMMU is not enabled",
            "initcall_blacklist": "initcall_blacklist is missing from the kernel command line",
            "CPU vendor": "The CPU vendor is not recognised",
        }
        return known.get(name, f"{name} needs attention")


KVM_CONF = "/etc/modprobe.d/kvm.conf"
IGNORE_MSRS_LIVE_FIX = FixCommand("echo Y > /sys/module/kvm/parameters/ignore_msrs",
                                  ("sh", "-c", "echo Y > /sys/module/kvm/parameters/ignore_msrs"))
IGNORE_MSRS_PERSIST_FIX = FixCommand(
    f"echo 'options kvm ignore_msrs=Y' >> {KVM_CONF}",
    ("sh", "-c", f"echo 'options kvm ignore_msrs=Y' >> {KVM_CONF}"))
INITRAMFS_FIX = FixCommand("update-initramfs -k all -u", ("update-initramfs", "-k", "all", "-u"))

WHY: dict[str, str] = {
    "KVM ignore_msrs": "macOS hangs at the Apple logo without it. No reboot needed.",
    "qm available": "qm creates and starts the VMs.",
    "pvesm available": "pvesm finds the storage the VM disk lives on.",
    "pvesh available": "pvesh asks Proxmox for the next free VM ID.",
    "qemu-img available": "qemu-img converts the macOS recovery image.",
    "dmg2img available": "Needed to unpack the macOS recovery image.",
    "sgdisk available": "Needed to partition the OpenCore boot disk.",
    "partprobe available": "Needed to make the new partitions visible.",
    "losetup available": "Needed to mount the boot disk while it is built.",
    "mkfs.fat available": "Needed to format the OpenCore boot partition.",
    "blkid available": "Needed to find the boot partition.",
    "/dev/kvm present": "Without hardware virtualization a Mac VM is far too slow to install.",
    "Root privileges": "Creating VMs and changing the host needs root. Start osx-next as root.",
    "IOMMU enabled": "Only needed for GPU passthrough.",
    "initcall_blacklist": "Only needed for GPU passthrough on Proxmox 8 and later.",
}


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
    "dmg2img": "dmg2img",
    "sgdisk": "gdisk",
    "partprobe": "parted",
    "losetup": "mount",
    "mkfs.fat": "dosfstools",
    "blkid": "util-linux",
}


def _check_ignore_msrs(
    kvm_conf: Path | None = None,
    sysfs_path: Path | None = None,
) -> PreflightCheck:
    """Check that the running KVM module ignores unsupported MSR reads.

    kvm.conf is only read when the module loads. On a node where the file was
    written after kvm was already loaded and never rebooted, the conf says Y
    while /sys/module/kvm/parameters/ignore_msrs still says N, and macOS hangs
    at the Apple logo. The live sysfs value is the only one that decides, so the
    conf file is treated as the persistence hint, not as the answer.
    """
    if kvm_conf is None:
        kvm_conf = Path("/etc/modprobe.d/kvm.conf")
    if sysfs_path is None:
        sysfs_path = Path("/sys/module/kvm/parameters/ignore_msrs")

    persisted = kvm_conf.exists() and "ignore_msrs=Y" in kvm_conf.read_text()
    live = sysfs_path.read_text().strip() if sysfs_path.exists() else ""

    if live in ("Y", "1"):
        persistence = (
            "persisted in /etc/modprobe.d/kvm.conf"
            if persisted
            else "not persisted, add 'options kvm ignore_msrs=Y' to "
                 "/etc/modprobe.d/kvm.conf so it survives a reboot"
        )
        return PreflightCheck(
            name="KVM ignore_msrs",
            ok=True,
            details=f"ignore_msrs=Y on the running kvm module ({persistence})",
        )

    if persisted:
        fix = (IGNORE_MSRS_LIVE_FIX,)
        details = (
            "ignore_msrs=Y is in /etc/modprobe.d/kvm.conf but the running kvm module has not "
            "picked it up (sysfs still reports N), so macOS will hang at the Apple logo. "
            "Fix, no reboot needed: echo Y > /sys/module/kvm/parameters/ignore_msrs"
        )
    else:
        fix = (IGNORE_MSRS_LIVE_FIX, IGNORE_MSRS_PERSIST_FIX, INITRAMFS_FIX)
        details = (
            "Missing ignore_msrs=Y - macOS will kernel panic on unsupported MSR access. "
            "Fix now, no reboot needed: echo Y > /sys/module/kvm/parameters/ignore_msrs - "
            "then persist it: echo 'options kvm ignore_msrs=Y' >> /etc/modprobe.d/kvm.conf "
            "&& update-initramfs -k all -u"
        )
    if not sysfs_path.exists():
        fix = ()  # no kvm module loaded: nothing to write to, /dev/kvm reports the real problem
    return PreflightCheck(name="KVM ignore_msrs", ok=False, details=details, fix=fix)


def _check_iommu(cmdline_path: Path | None = None) -> PreflightCheck:
    """Check if IOMMU is enabled in kernel cmdline - informational (GPU passthrough)."""
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
        details="IOMMU not detected in kernel cmdline - only needed for GPU passthrough",
    )


def has_initcall_blacklist(cmdline_path: Path | None = None) -> bool:
    """True when the kernel cmdline carries initcall_blacklist=sysfb_init."""
    cmdline = cmdline_path or Path("/proc/cmdline")
    return cmdline.exists() and "initcall_blacklist=sysfb_init" in cmdline.read_text()


def _check_initcall_blacklist(cmdline_path: Path | None = None) -> PreflightCheck:
    """Check for initcall_blacklist=sysfb_init - informational (PVE 8+ GPU passthrough)."""
    if has_initcall_blacklist(cmdline_path):
        return PreflightCheck(
            name="initcall_blacklist",
            ok=True,
            details="sysfb_init blacklisted in kernel cmdline (PVE 8+ GPU passthrough)",
        )
    return PreflightCheck(
        name="initcall_blacklist",
        ok=True,
        details="initcall_blacklist not set - only needed for PVE 8+ GPU passthrough",
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

    for cmd, pkg in _BUILD_BINARIES.items():
        binary = _find_binary(cmd)
        checks.append(
            PreflightCheck(
                name=f"{cmd} available",
                ok=bool(binary),
                details=binary or f"Not found. Install with: apt install {pkg}",
                fix=() if binary else (FixCommand(f"apt-get install -y {pkg}", ("apt-get", "install", "-y", pkg)),),
            )
        )

    for check in checks:
        if not check.ok:
            log.debug("Preflight fail: %s - %s", check.name, check.details)

    checks.append(_check_ignore_msrs())
    checks.append(_check_iommu())
    checks.append(_check_initcall_blacklist())

    vendor = detect_cpu_vendor()
    cpu_info = detect_cpu_info()
    # Mirrors planner._cpu_args selection order so preflight reports the
    # profile the plan will actually use.
    if cpu_info.needs_emulated_cpu:
        cpu_detail = "Cascadelake-Server emulation"
    elif cpu_info.xeon_hedt_model:
        cpu_detail = f"{cpu_info.xeon_hedt_model.split(',')[0]} (multi-socket Xeon profile)"
    else:
        cpu_detail = "native host passthrough"
    checks.append(
        PreflightCheck(
            name="CPU vendor",
            ok=True,
            details=f"{vendor} - {cpu_detail}",
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
    failed = [c for c in checks if not c.ok]
    log.debug("Preflight complete: %d/%d checks passed", len(checks) - len(failed), len(checks))
    return checks


def has_missing_build_deps(checks: list[PreflightCheck]) -> bool:
    """Return True if any build dependency checks failed."""
    return any(
        not c.ok and c.name.endswith("available") and c.name.split()[0] in _BUILD_BINARIES
        for c in checks
    )


def find_missing_packages() -> list[str]:
    """Return apt package names for missing build binaries."""
    packages: list[str] = []
    for cmd, pkg in _BUILD_BINARIES.items():
        if not _find_binary(cmd):
            if pkg not in packages:
                packages.append(pkg)
    return packages


def install_missing_packages(
    on_output: Callable[[str], None] | None = None,
    adapter: ProxmoxAdapter | None = None,
) -> tuple[bool, list[str]]:
    """Auto-install missing build dependencies via apt-get.

    Returns (success, list_of_installed_packages).
    Calls *on_output* with status messages if provided.
    """
    if not _is_root():
        return False, []

    packages = find_missing_packages()
    if not packages:
        return True, []

    def _emit(msg: str) -> None:
        log.info(msg)
        if on_output:
            on_output(msg)

    _emit(f"Installing: {', '.join(packages)}")

    if adapter is None:
        adapter = get_proxmox_adapter()
    runtime = adapter
    result = runtime.run(["apt-get", "install", "-y", *packages])
    if result.ok:
        _emit("Installation complete")
        return True, packages
    _emit(f"apt-get failed (exit {result.returncode}): {result.output}")
    return False, []
