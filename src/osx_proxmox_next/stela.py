"""The macOS 27 arm64 via Stela (beta) target: what the user chooses and the rules every value must pass.

Stela installs macOS on a PC with no Mac. Its author withdrew the upstream repository (tylertms/Stela) on 2026-11-22
and removed the releases, so this project builds Stela from source. It never vendors or copies that source: at install
time it fetches one pinned commit, by its sha, from an ordered list of forks (any fork that serves the sha is as good as
any other, because git checks the object hash), builds it in a throwaway Debian 13 container on the host and caches the
result. Every value below ends up in a `qm` argument, a shell argument or the VM's hook script, so each is restricted to
a small character set before any plan is built."""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "DEFAULT_CORES", "DEFAULT_DISK_GB", "DEFAULT_MEMORY_MB", "MAX_VMID", "MIN_CORES", "MIN_DISK_GB", "MIN_MEMORY_MB",
    "MIN_VMID", "STELA_COMMIT", "STELA_FORKS", "STELA_LABEL", "STELA_TAG", "STELA_VM_TAG", "StelaConfig", "mac_for",
    "tap_for", "validate_stela_config",
]

STELA_LABEL = "macOS 27 arm64 via Stela (beta)"
STELA_VM_TAG = "osx-next-stela"          # the Proxmox tag the VM carries
STELA_TAG = "v0.5.0"
# The commit the tag v0.5.0 names. Four forks serve it by sha (InSnh-Gd also keeps the tag); the object hash is the check.
STELA_COMMIT = "cd732c12f6c4d0fdfb5fffba2f68c09f2bbf21fc"
STELA_FORKS = (
    "https://github.com/InSnh-Gd/Stela.git",
    "https://github.com/xiloe85/Stela.git",
    "https://github.com/AnthoDingo/Stela.git",
    "https://github.com/rschulzgit/Stela.git",
)

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
_URL_RE = re.compile(r"(https?|git)://[A-Za-z0-9._\-]+(:[0-9]{1,5})?(/[A-Za-z0-9._\-]+)*")


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
    stela_commit: str = STELA_COMMIT
    stela_url: str = ""      # a git URL tried before STELA_FORKS (a mirror); it must serve stela_commit, the sha decides


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
    if config.stela_url and not _URL_RE.fullmatch(config.stela_url):
        issues.append("The Stela mirror must be an http(s) or git URL with a plain path.")
    if not re.fullmatch(r"[0-9a-f]{40}", config.stela_commit):
        issues.append("The Stela commit must be 40 lowercase hex characters.")
    return issues
