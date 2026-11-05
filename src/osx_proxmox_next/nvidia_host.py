"""NullMoth vendor profile: which NVIDIA cards qualify (Turing and newer)."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from . import gpu
from .gpu import GpuInfo, GpuReport

__all__ = ["NVIDIA_VENDOR", "check_nvidia_gpu", "detect_nvidia_gpus", "is_supported_device",
           "supported_device_ids"]

NVIDIA_VENDOR = 0x10DE
# Upstream's installer treats every device id from this value up as Turing or
# newer (TU102 is 1E02, Volta and older sit below it).
_TURING_FLOOR = 0x1E00
_UNSUPPORTED = "Device 10de:{device} is older than Turing. NullMoth supports RTX 20 series and newer only."


def supported_device_ids() -> frozenset[int]:
    text = (resources.files("osx_proxmox_next") / "data/nullmoth/supported_device_ids.txt").read_text()
    return frozenset(
        int(line, 16) for line in text.splitlines() if line.strip() and not line.startswith("#")
    )


def is_supported_device(device_id: int) -> bool:
    return device_id in supported_device_ids() or device_id >= _TURING_FLOOR


def detect_nvidia_gpus(root: Path = Path("/sys")) -> list[GpuInfo]:
    return gpu.detect_gpus(NVIDIA_VENDOR, is_supported_device, root)


def check_nvidia_gpu(bdf: str, root: Path = Path("/sys"), cmdline_path: Path | None = None) -> GpuReport:
    return gpu.check_gpu(bdf, NVIDIA_VENDOR, is_supported_device, _UNSUPPORTED, root, cmdline_path)
