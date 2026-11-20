from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..defaults import (
    CpuInfo,
    DEFAULT_ISO_DIR,
    detect_cpu_info,
    detect_host_cores,
    detect_iso_storage,
    detect_total_memory_mb,
    max_vm_memory_mb,
)
from .detection_service import detect_next_vmid, detect_storage_free_gb, detect_storage_targets

log = logging.getLogger(__name__)

__all__ = ["HostFacts", "load_host_facts"]


@dataclass
class HostFacts:
    """What the New Mac screen needs to know about this host, read once."""
    cpu: CpuInfo
    storage_targets: list[str] = field(default_factory=list)
    iso_dirs: list[str] = field(default_factory=list)
    storage_free_gb: dict[str, int] = field(default_factory=dict)
    host_cores: int = 0
    host_memory_mb: int = 0       # 0 = unknown
    vm_memory_limit_mb: int = 0   # 0 = unknown
    next_vmid: int = 0

    @property
    def iso_dir(self) -> str:
        return self.iso_dirs[0] if self.iso_dirs else DEFAULT_ISO_DIR


def load_host_facts() -> HostFacts:
    cpu = detect_cpu_info()
    return HostFacts(
        cpu=cpu,
        storage_targets=detect_storage_targets(),
        iso_dirs=detect_iso_storage(),
        storage_free_gb=detect_storage_free_gb(),
        host_cores=detect_host_cores(),
        host_memory_mb=detect_total_memory_mb(),
        vm_memory_limit_mb=max_vm_memory_mb(),
        next_vmid=detect_next_vmid(),
    )
