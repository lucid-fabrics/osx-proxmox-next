from __future__ import annotations

import os
from pathlib import Path


DEFAULT_STORAGE = "local-lvm"
DEFAULT_BRIDGE = "vmbr0"


def detect_cpu_vendor() -> str:
    """Return 'AMD' or 'Intel' based on /proc/cpuinfo (default: Intel)."""
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("vendor_id"):
                if "AuthenticAMD" in line:
                    return "AMD"
                return "Intel"
    return "Intel"


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


def default_disk_gb(macos: str) -> int:
    if macos == "tahoe":
        return 160
    if macos == "sequoia":
        return 128
    if macos == "sonoma":
        return 96
    return 80
