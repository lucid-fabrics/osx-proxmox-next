"""The macOS 27 arm64 (beta) target: what the user chooses and the rules every value must pass.

The target runs inside a Debian runner VM that Proxmox manages like any other VM. Nothing is installed on the Proxmox
host itself. Every value below ends up in a `qm` argument or in the runner's cloud-init files, so each one is
restricted to a small character set here, before any plan is built."""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "ARM64_LABEL", "ARM64_TAG", "DEBIAN_IMAGE_URL", "DEBIAN_SUMS_URL", "DEFAULT_SOURCE_REF", "DEFAULT_SOURCE_SHA256",
    "DEFAULT_SOURCE_URL", "Arm64Config", "KEY_DIR",
    "DEFAULT_CORES", "DEFAULT_DISK_GB", "DEFAULT_MEMORY_MB", "MIN_CORES", "MIN_DISK_GB", "MIN_MEMORY_MB",
    "RUNNER_USER", "validate_arm64_config",
]

ARM64_LABEL = "macOS 27 arm64 (beta)"
ARM64_TAG = "osx-next-arm64"        # the Proxmox tag the runner VM carries
RUNNER_USER = "snapintosh"

DEBIAN_IMAGE_URL = "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
DEBIAN_SUMS_URL = "https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS"
KEY_DIR = "/root/.ssh"              # on the Proxmox host: the runner's login key, one pair per runner (osx-next-arm64-<vmid>)

# The runner downloads the arm64/ tree of this repository at {ref} and refuses a file whose sha256 differs. The ref must be a tag
# or a full commit id (a branch moves), and the checksum is mandatory. The defaults below are pinned to the beta tag;
# `arm64/scripts/arm64-source-pin.sh <ref>` prints both lines for a new tag.
DEFAULT_SOURCE_URL = "https://github.com/lucid-fabrics/osx-proxmox-next/archive/{ref}.tar.gz"
DEFAULT_SOURCE_REF = "v0.38.4-beta.1"
DEFAULT_SOURCE_SHA256 = "345e5c00dd52ab95558dabe2beeeb5fbfa229c75f539040f86f01db01674c183"

MIN_VMID = 100
MAX_VMID = 999999
DEFAULT_CORES = 6
MIN_CORES = 4
DEFAULT_MEMORY_MB = 8192
MIN_MEMORY_MB = 8192
DEFAULT_DISK_GB = 120
MIN_DISK_GB = 100

_NAME_RE = re.compile(r"[a-zA-Z0-9]([a-zA-Z0-9.\-]*[a-zA-Z0-9])?")
_STORAGE_RE = re.compile(r"[a-zA-Z0-9_\-]+")
_BRIDGE_RE = re.compile(r"vmbr[0-9]+")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_TAG_RE = re.compile(r"v[0-9][A-Za-z0-9._\-]{0,60}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_URL_RE = re.compile(r"https?://[A-Za-z0-9._\-]+(:[0-9]{1,5})?/[A-Za-z0-9._/\-{}]*")
_PCI_RE = re.compile(r"([0-9a-f]{4}:)?[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")
_SSH_KEY_RE = re.compile(r"(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)) [A-Za-z0-9+/]+={0,3}( [A-Za-z0-9_.@\-]{1,64})?")
_PATH_RE = re.compile(r"/[A-Za-z0-9._@+/\-]+")
_ECID_RE = re.compile(r"[0-9]{1,20}")


@dataclass
class Arm64Config:
    vmid: int
    name: str
    storage: str
    cores: int = DEFAULT_CORES
    memory_mb: int = DEFAULT_MEMORY_MB
    disk_gb: int = DEFAULT_DISK_GB
    bridge: str = "vmbr0"
    vlan: int = 0                    # 0 = untagged
    cpu_units: int = 0               # 0 = the Proxmox default (1024)
    ssh_public_key: str = ""         # login for the runner; empty = no remote login
    gpu_pci: str = ""                # a free host GPU to pass to the runner; empty = none (lavapipe)
    source_url: str = DEFAULT_SOURCE_URL
    source_ref: str = DEFAULT_SOURCE_REF       # a tag (v1.2) or a full commit id, never a branch
    source_sha256: str = DEFAULT_SOURCE_SHA256  # sha256 of the archive at source_ref; the runner refuses any other file
    renderer_url: str = ""           # empty = the release tarball named in pins.env; a build from a local copy sets both
    renderer_sha256: str = ""
    ipsw: str = ""                   # path on the runner to the IPSW the Mac-free install restores
    aux: str = ""                    # path on the runner to the aux image the restore boots with
    ecid: str = ""                   # the machine uuid (ECID) the restore is personalized for, decimal
    disk_target: str = ""            # path on the runner for the raw disk the restore writes

    @property
    def archive_url(self) -> str:
        return self.source_url.replace("{ref}", self.source_ref)


def validate_arm64_config(config: Arm64Config) -> list[str]:
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
    if config.vlan and not 1 <= config.vlan <= 4094:
        issues.append("VLAN tag must be between 1 and 4094 (or 0 for untagged).")
    if config.cpu_units and not 1 <= config.cpu_units <= 10000:
        issues.append("CPU units must be between 1 and 10000 (or 0 for the default).")
    if config.ssh_public_key and not _SSH_KEY_RE.fullmatch(config.ssh_public_key):
        issues.append("SSH public key must be one line: a key type, the key and an optional comment.")
    if config.gpu_pci and not _PCI_RE.fullmatch(config.gpu_pci):
        issues.append("GPU address must look like 0000:03:00.0 or 03:00.0.")
    if not _URL_RE.fullmatch(config.source_url):
        issues.append("Source URL must be http or https and contain only URL-safe characters.")
    if not (_COMMIT_RE.fullmatch(config.source_ref) or _TAG_RE.fullmatch(config.source_ref)) or ".." in config.source_ref:
        issues.append("Source ref must be a release tag (v1.2) or a full 40 digit commit id, not a branch"
                      + ("; this build has no pinned release yet, pass --source-ref and --source-sha256." if not config.source_ref else "."))
    if not _SHA256_RE.fullmatch(config.source_sha256):
        issues.append("Source sha256 must be 64 lowercase hex digits (the checksum of the archive at the ref).")
    if bool(config.renderer_url) != bool(config.renderer_sha256):
        issues.append("Renderer URL and renderer sha256 are given together or not at all.")
    if config.renderer_url and not _URL_RE.fullmatch(config.renderer_url.replace("file:///", "https://x/")):
        issues.append("Renderer URL must be http, https or file and contain only URL-safe characters.")
    if config.renderer_sha256 and not _SHA256_RE.fullmatch(config.renderer_sha256):
        issues.append("Renderer sha256 must be 64 lowercase hex digits.")
    for label, value in (("IPSW", config.ipsw), ("Aux image", config.aux), ("Disk target", config.disk_target)):
        if value and (not _PATH_RE.fullmatch(value) or ".." in value):
            issues.append(f"{label} must be an absolute path of letters, digits and . _ + @ / - (no ..).")
    if config.ecid and (not _ECID_RE.fullmatch(config.ecid) or not 0 < int(config.ecid) < 2**64):
        issues.append("ECID must be decimal digits (1 to 20) and fit in 64 bits.")
    return issues
