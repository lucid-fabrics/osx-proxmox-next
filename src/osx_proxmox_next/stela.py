"""The macOS 27 arm64 via Stela (beta) target: what the user chooses and the rules every value must pass.

Stela (https://github.com/tylertms/Stela) installs macOS on a PC with no Mac. This project never vendors or copies its
source: at install time it downloads one pinned release tarball, checks it against a pinned sha256 and against the
release's own SHA256SUMS, and runs the `stela` binary inside. Every value below ends up in a `qm` argument, a shell
argument or the VM's hook script, so each is restricted to a small character set before any plan is built."""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "DEFAULT_CORES", "DEFAULT_DISK_GB", "DEFAULT_MEMORY_MB", "MAX_VMID", "MIN_CORES", "MIN_DISK_GB", "MIN_MEMORY_MB",
    "MIN_VMID", "STELA_ASSET", "STELA_LABEL", "STELA_RELEASE_URL", "STELA_SHA256", "STELA_TAG", "STELA_VM_TAG",
    "StelaConfig", "mac_for", "tap_for", "validate_stela_config",
]

STELA_LABEL = "macOS 27 arm64 via Stela (beta)"
STELA_VM_TAG = "osx-next-stela"          # the Proxmox tag the VM carries
STELA_TAG = "v0.5.0"
STELA_ASSET = f"stela-{STELA_TAG}-linux-x86_64.tar.gz"
STELA_RELEASE_URL = f"https://github.com/tylertms/Stela/releases/download/{STELA_TAG}"
STELA_SHA256 = "6236ddffb39e52ffd6ea71ec3a763788e75497493f4de19ad9e8a35a3722ed6a"   # of STELA_ASSET, checked twice

MIN_VMID = 100
MAX_VMID = 999999
DEFAULT_CORES = 8
MIN_CORES = 4
DEFAULT_MEMORY_MB = 16384
MIN_MEMORY_MB = 8192
DEFAULT_DISK_GB = 100
MIN_DISK_GB = 64

_NAME_RE = re.compile(r"[a-zA-Z0-9]([a-zA-Z0-9.\-]*[a-zA-Z0-9])?")
_STORAGE_RE = re.compile(r"[a-zA-Z0-9_\-]+")
_BRIDGE_RE = re.compile(r"vmbr[0-9]+")
_PCI_RE = re.compile(r"([0-9a-f]{4}:)?[0-9a-f]{2}:[0-9a-f]{2}(\.[0-7])?")   # no function = every function of the card
_VGA_VALUES = ("none", "std")
_URL_RE = re.compile(r"https?://[A-Za-z0-9._\-]+(:[0-9]{1,5})?(/[A-Za-z0-9._\-]+)*")


@dataclass
class StelaConfig:
    vmid: int
    name: str
    storage: str
    cores: int = DEFAULT_CORES
    memory_mb: int = DEFAULT_MEMORY_MB
    disk_gb: int = DEFAULT_DISK_GB
    bridge: str = "vmbr0"
    gpu_pci: str = ""        # a free host GPU to pass to the VM (all its functions when no .N); empty = no GPU
    vga: str = "std"         # the emulated display when there is no GPU: std | none (ignored with a GPU)
    stela_sha256: str = STELA_SHA256
    stela_url: str = STELA_RELEASE_URL    # folder that holds the tarball and its SHA256SUMS: a mirror is fine, the pin still decides


def mac_for(vmid: int) -> str:
    """The guest NIC's MAC, from the VMID: BC:24:11 is the prefix Proxmox itself uses for its VMs."""
    return f"BC:24:11:{(vmid >> 16) & 255:02X}:{(vmid >> 8) & 255:02X}:{vmid & 255:02X}"


def tap_for(vmid: int) -> str:
    """The host tap device behind the NIC: its own, because the VM's NIC is not one of Proxmox's own (see the planner)."""
    return f"tap{vmid}x"


def validate_stela_config(config: StelaConfig) -> list[str]:
    issues: list[str] = []
    if not MIN_VMID <= config.vmid <= MAX_VMID:
        issues.append(f"VMID must be between {MIN_VMID} and {MAX_VMID}.")
    if not 3 <= len(config.name) <= 63 or not _NAME_RE.fullmatch(config.name):
        issues.append("VM name must be 3 to 63 characters, start with alphanumeric and contain only [a-zA-Z0-9.-].")
    if not config.storage or not _STORAGE_RE.fullmatch(config.storage):
        issues.append("Storage target must be alphanumeric, hyphens, underscores only.")
    if config.cores < MIN_CORES:
        issues.append(f"At least {MIN_CORES} CPU cores are required.")
    if config.memory_mb < MIN_MEMORY_MB:
        issues.append(f"At least {MIN_MEMORY_MB} MB RAM is required.")
    if config.disk_gb < MIN_DISK_GB:
        issues.append(f"At least {MIN_DISK_GB} GB disk is required.")
    if not _BRIDGE_RE.fullmatch(config.bridge):
        issues.append("Bridge must match vmbr<N> (e.g. vmbr0).")
    if config.gpu_pci and not _PCI_RE.fullmatch(config.gpu_pci):
        issues.append("GPU address must look like 0a:00 or 0000:0a:00.0.")
    if config.vga not in _VGA_VALUES:
        issues.append("Display must be none or std.")
    if not _URL_RE.fullmatch(config.stela_url):
        issues.append("The Stela download folder must be an http(s) URL with a plain path.")
    if not re.fullmatch(r"[0-9a-f]{64}", config.stela_sha256):
        issues.append("The Stela checksum must be 64 lowercase hex characters.")
    return issues
