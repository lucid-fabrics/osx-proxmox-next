from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_STORAGE = "local-lvm"
DEFAULT_BRIDGE = "vmbr0"

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


@dataclass
class CpuInfo:
    """Host CPU identification used for QEMU flag selection."""
    vendor: str             # "AMD" or "Intel"
    model_name: str         # e.g. "12th Gen Intel(R) Core(TM) i7-12700K"
    family: int             # cpu family from /proc/cpuinfo
    model: int              # model number from /proc/cpuinfo
    needs_emulated_cpu: bool  # True for AMD and Intel hybrid (12th gen+)


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
                # "model\t\t: 183" — must come after "model name" check
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

    # Intel: check for hybrid architecture (Family 6 + known hybrid model)
    is_hybrid = (
        family == 6
        and (model in _INTEL_HYBRID_MODELS or model >= _INTEL_HYBRID_THRESHOLD)
    )
    return CpuInfo(vendor=vendor, model_name=model_name, family=family,
                   model=model, needs_emulated_cpu=is_hybrid)


def detect_cpu_vendor() -> str:
    """Return 'AMD' or 'Intel' based on /proc/cpuinfo (default: Intel)."""
    return detect_cpu_info().vendor


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


def detect_memory_mb() -> int:
    mem_total_kb = 0
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("MemTotal:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    mem_total_kb = int(parts[1])
                break
    if mem_total_kb <= 0:
        return 8192

    mem_total_mb = mem_total_kb // 1024
    # Default to half of host memory with sane bounds.
    return max(4096, min(32768, mem_total_mb // 2))


DEFAULT_ISO_DIR = "/var/lib/vz/template/iso"


def detect_iso_storage() -> list[str]:
    """Return ISO directory paths from Proxmox storage pools that support ISO content."""
    import subprocess
    dirs: list[str] = []
    try:
        output = subprocess.check_output(
            ["pvesm", "status", "-content", "iso"], text=True, timeout=2.0,
        )
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 7 and parts[2] == "active":
                storage_id = parts[0]
                path = _resolve_iso_path(storage_id)
                if path and path not in dirs:
                    dirs.append(path)
    except Exception:
        pass
    # Always include local as fallback
    if DEFAULT_ISO_DIR not in dirs:
        dirs.insert(0, DEFAULT_ISO_DIR)
    return dirs


def _resolve_iso_path(storage_id: str) -> str | None:
    """Resolve a Proxmox storage ID to its ISO template directory."""
    import subprocess
    try:
        output = subprocess.check_output(
            ["pvesm", "path", f"{storage_id}:iso/probe.iso"],
            text=True, timeout=2.0,
        ).strip()
        # pvesm path returns full file path; we want the directory
        if output:
            return str(Path(output).parent)
    except Exception:
        pass
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
