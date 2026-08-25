from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .infrastructure import ProxmoxAdapter

log = logging.getLogger(__name__)


DEFAULT_STORAGE = "local-lvm"
DEFAULT_BRIDGE = "vmbr0"

_MIN_MEMORY_MB = 4096
_MAX_MEMORY_MB = 32768
DEFAULT_MEMORY_MB = 16384

# Intel Family 6 model numbers for hybrid (P+E core) architectures.
# These CPUs need emulated CPU mode because macOS hardware validation
# fails on hybrid core topology when using -cpu host with correct SMBIOS.
_INTEL_HYBRID_MODELS: frozenset[int] = frozenset({
    151,  # Alder Lake-S (12th gen)
    154,  # Alder Lake-P (12th gen mobile)
    170,  # Meteor Lake (14th gen)
    183,  # Raptor Lake-S (13th gen)
    186,  # Raptor Lake-P (13th gen mobile)
})

# Models >= this threshold are assumed hybrid (future-proofing).
_INTEL_HYBRID_THRESHOLD: int = 190

# Intel Family 6 models below this threshold are pre-Skylake (Broadwell, Haswell,
# Ivy Bridge, Sandy Bridge, etc.). These CPUs work more reliably with -cpu Penryn
# during macOS installation than with -cpu host.
_INTEL_LEGACY_THRESHOLD: int = 94  # Skylake desktop starts at model 94


@dataclass
class CpuInfo:
    """Host CPU identification used for QEMU flag selection."""
    vendor: str             # "AMD" or "Intel"
    model_name: str         # e.g. "12th Gen Intel(R) Core(TM) i7-12700K"
    family: int             # cpu family from /proc/cpuinfo
    model: int              # model number from /proc/cpuinfo
    needs_emulated_cpu: bool  # True for AMD and Intel hybrid (12th gen+)
    needs_penryn: bool = False  # True for pre-Skylake Intel (Broadwell and older), excluding Xeon
    is_xeon: bool = False   # True when "Xeon" appears in model name (server chips, always use -cpu host)
    xeon_hedt_model: str = ""  # Fixed QEMU -cpu model for multi-socket-capable Xeon HEDT/Scalable chips (empty if not applicable)


def _classify_intel_cpu(family: int, model: int, model_name: str) -> tuple[bool, bool, bool]:
    """Classify an Intel CPU into (is_hybrid, is_xeon, is_legacy).

    is_hybrid: Family 6 with P+E core topology (12th gen+), needs emulated CPU.
    is_xeon: Server/workstation chip, always uses -cpu host.
    is_legacy: Pre-Skylake desktop (Broadwell and older), needs Penryn mode.
    """
    is_hybrid = (
        family == 6
        and (model in _INTEL_HYBRID_MODELS or model >= _INTEL_HYBRID_THRESHOLD)
    )
    # Xeon CPUs are server/workstation chips that generally work fine with
    # -cpu host even below the Skylake model threshold (e.g. Xeon E5 v4 is
    # model 79). Real HEDT parts (E5/E7 v2-v4) are the exception, see
    # _xeon_hedt_cpu_model.
    is_xeon = "Xeon" in model_name
    # Pre-Skylake Intel (Broadwell, Haswell, Ivy Bridge, etc.) work more reliably
    # with -cpu Penryn during macOS installation than with -cpu host.
    # Xeon chips are excluded: they are modern enough and stripping features causes issues.
    is_legacy = (
        family == 6
        and model > 0
        and not is_hybrid
        and not is_xeon
        and model < _INTEL_LEGACY_THRESHOLD
    )
    return is_hybrid, is_xeon, is_legacy


_XEON_HEDT_PATTERN = re.compile(r"Xeon.*E[57][ -]*\d+ *v([234])", re.IGNORECASE)
_XEON_SCALABLE_PATTERN = re.compile(r"Xeon.*(?:Platinum|Gold|Silver|Bronze)[ -]*\d{4}", re.IGNORECASE)


def _xeon_hedt_cpu_model(model_name: str) -> str:
    """Return a fixed QEMU -cpu model for multi-socket-capable Xeon chips, or "".

    Real HEDT and Scalable parts (dual-socket/multi-die) leak their genuine
    multi-package topology through -cpu host. Combined with a multi-socket-
    capable SMBIOS (MacPro7,1), XNU's scheduler can livelock under heavy
    multithreaded I/O, e.g. the macOS installer copy phase (CPU pegged at
    100% while disk and network progress both flatline).

    E5/E7 v2-v4 HEDT overrides match the known-working profile from
    github.com/mchiappinam/proxmox-macos. Xeon Scalable (Skylake-SP and
    later: Platinum/Gold/Silver/Bronze) hits the identical livelock on
    dual-socket boards; confirmed fix on Cascade Lake-SP is the same idea,
    pin to a fixed non-host model instead of exposing genuine topology.
    """
    match = _XEON_HEDT_PATTERN.search(model_name)
    if match:
        if match.group(1) == "2":
            return "Haswell-noTSX,model=158,stepping=3"
        return "Broadwell-noTSX,model=158"
    if _XEON_SCALABLE_PATTERN.search(model_name):
        return "Skylake-Client-noTSX-IBRS"
    return ""


def detect_cpu_info() -> CpuInfo:
    """Detect host CPU vendor, model, and whether it needs emulated CPU mode.

    AMD always needs Cascadelake-Server emulation (no native macOS support).
    Intel hybrid CPUs (12th gen+) need it because macOS hardware validation
    fails on P+E core topology when using -cpu host with correct SMBIOS.
    """
    vendor = "Intel"
    model_name = ""
    family = 0
    model = 0

    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("vendor_id"):
                vendor = "AMD" if "AuthenticAMD" in line else "Intel"
            elif line.startswith("cpu family"):
                parts = line.split(":")
                if len(parts) >= 2 and parts[1].strip().isdigit():
                    family = int(parts[1].strip())
            elif line.startswith("model name"):
                parts = line.split(":", 1)
                if len(parts) >= 2:
                    model_name = parts[1].strip()
            elif line.startswith("model"):
                # "model\t\t: 183" must come after "model name" check
                parts = line.split(":")
                if len(parts) >= 2 and parts[1].strip().isdigit():
                    model = int(parts[1].strip())
            elif not line.strip():
                # Empty line = end of first CPU block; all cores report same values
                if vendor and family:
                    break

    if vendor == "AMD":
        return CpuInfo(vendor=vendor, model_name=model_name, family=family,
                       model=model, needs_emulated_cpu=True)

    is_hybrid, is_xeon, is_legacy = _classify_intel_cpu(family, model, model_name)
    return CpuInfo(vendor=vendor, model_name=model_name, family=family,
                   model=model, needs_emulated_cpu=is_hybrid, needs_penryn=is_legacy,
                   is_xeon=is_xeon, xeon_hedt_model=_xeon_hedt_cpu_model(model_name))


def detect_cpu_vendor() -> str:
    """Return 'AMD' or 'Intel' based on /proc/cpuinfo (default: Intel)."""
    return detect_cpu_info().vendor


def detect_net_model(cpu: CpuInfo) -> str:
    """Return the recommended NIC model for the given CPU.

    Xeon and pre-Skylake Intel CPUs use e1000-82545em because vmxnet3
    requires a kext from the OpenCore EFI that may not load cleanly during
    installation on older hardware, silently degrading recovery downloads.
    e1000-82545em has a native macOS driver and needs no kext.
    """
    if cpu.is_xeon or cpu.needs_penryn:
        return "e1000-82545em"
    return "vmxnet3"


def _round_down_power_of_2(n: int) -> int:
    """Round down to the nearest power of 2 (minimum 2)."""
    p = 1
    while p * 2 <= n:
        p *= 2
    return max(2, p)


def detect_cpu_cores() -> int:
    count = os.cpu_count() or 4
    # Keep host responsive and avoid overcommit by default.
    half = max(2, min(16, count // 2 if count >= 8 else count))
    # macOS expects power-of-2 core counts matching real Mac topology;
    # odd counts (e.g. 6) can hang at the Apple logo during boot.
    return _round_down_power_of_2(half)


def _meminfo_mb(field: str) -> int:
    """Read one /proc/meminfo field in MB; 0 when absent or unreadable."""
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return 0
    for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(field + ":"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) // 1024
            break
    return 0


# Kept free for the Proxmox host itself (pve daemons, ZFS ARC headroom, ssh).
# The VM runs with balloon=0, so its full allocation is pinned at qm start.
HOST_RAM_RESERVE_MB = 1024


def detect_available_memory_mb() -> int:
    """Host MemAvailable in MB; 0 when unknown (non-Linux or old kernel)."""
    return _meminfo_mb("MemAvailable")


def max_vm_memory_mb() -> int:
    """Largest VM allocation the host can take right now (0 = unknown)."""
    avail = detect_available_memory_mb()
    if avail <= 0:
        return 0
    return max(0, avail - HOST_RAM_RESERVE_MB)


def detect_memory_mb() -> int:
    mem_total_mb = _meminfo_mb("MemTotal")
    if mem_total_mb <= 0:
        return 8192

    # Default to half of host memory, but never suggest more than what is
    # actually free right now (the allocation is pinned, balloon=0).
    default = mem_total_mb // 2
    limit = max_vm_memory_mb()
    if limit > 0:
        default = min(default, limit)
    return max(_MIN_MEMORY_MB, min(_MAX_MEMORY_MB, default))


DEFAULT_ISO_DIR = "/var/lib/vz/template/iso"


def detect_iso_storage() -> list[str]:
    """Return ISO directory paths from Proxmox storage pools that support ISO content."""
    from .services import get_proxmox_adapter
    pve = get_proxmox_adapter()
    dirs: list[str] = []
    res = pve.pvesm("status", "-content", "iso")
    if res.ok:
        for line in res.output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 7 and parts[2] == "active":
                storage_id = parts[0]
                path = _resolve_iso_path(pve, storage_id)
                if path and path not in dirs:
                    dirs.append(path)
    else:
        log.debug("Failed to detect ISO storage: %s", res.output)
    # Always include local as fallback
    if DEFAULT_ISO_DIR not in dirs:
        dirs.insert(0, DEFAULT_ISO_DIR)
    return dirs


def _resolve_iso_path(pve: ProxmoxAdapter, storage_id: str) -> str | None:
    """Resolve a Proxmox storage ID to its ISO template directory."""
    res = pve.pvesm("path", f"{storage_id}:iso/probe.iso")
    if res.ok and res.output.strip():
        return str(Path(res.output.strip()).parent)
    else:
        log.debug("Failed to resolve ISO path for %s: %s", storage_id, res.output)
    # Fallback heuristics for common Proxmox layouts
    local_path = Path(f"/mnt/pve/{storage_id}/template/iso")
    if local_path.exists():
        return str(local_path)
    if storage_id == "local":
        return DEFAULT_ISO_DIR
    return None


def default_disk_gb(macos: str) -> int:
    if macos == "tahoe":
        return 160
    if macos == "sequoia":
        return 128
    if macos == "sonoma":
        return 96
    return 80
