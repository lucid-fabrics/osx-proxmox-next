"""VM size presets of the New Mac screen (Light, Balanced, Heavy).

Pure functions: the screen feeds them the host facts, so every rule here is
unit-testable without a Proxmox host."""

from __future__ import annotations

from dataclasses import dataclass

from .defaults import (
    _round_down_power_of_2,
    default_disk_gb,
)

__all__ = ["SizePreset", "PRESET_KEYS", "preset_label", "build_presets", "preset_fits"]

PRESET_KEYS = ("light", "balanced", "heavy", "custom")
_LABELS = {"light": "Light", "balanced": "Balanced", "heavy": "Heavy", "custom": "Custom"}

LIGHT_CORES, LIGHT_MEMORY_MB, LIGHT_DISK_GB = 4, 8192, 64
HEAVY_DISK_GB = 256


@dataclass(frozen=True)
class SizePreset:
    key: str
    cores: int
    memory_mb: int
    disk_gb: int

    @property
    def label(self) -> str:
        return _LABELS[self.key]

    @property
    def memory_gb(self) -> int:
        return self.memory_mb // 1024


def preset_label(key: str) -> str:
    return _LABELS[key]


def build_presets(
    macos: str,
    balanced_cores: int,
    balanced_memory_mb: int,
    host_cores: int,
    host_memory_mb: int,
) -> dict[str, SizePreset]:
    """Light, Balanced and Heavy for *macos* on a host with the given cores and RAM.

    Balanced is exactly what the app has always suggested. Heavy doubles
    Balanced, capped at the host's cores (rounded down to a power of two) and
    at half of the host's RAM. A host figure of 0 means unknown: no cap."""
    heavy_cores = balanced_cores * 2
    if host_cores > 0:
        heavy_cores = min(heavy_cores, host_cores)
    heavy_cores = _round_down_power_of_2(heavy_cores)
    heavy_memory = balanced_memory_mb * 2
    if host_memory_mb > 0:
        heavy_memory = min(heavy_memory, host_memory_mb // 2)
    heavy_memory = max(heavy_memory, 1024)
    return {
        "light": SizePreset("light", LIGHT_CORES, LIGHT_MEMORY_MB, LIGHT_DISK_GB),
        "balanced": SizePreset("balanced", balanced_cores, balanced_memory_mb, default_disk_gb(macos)),
        "heavy": SizePreset("heavy", heavy_cores, heavy_memory, HEAVY_DISK_GB),
    }


def preset_fits(
    preset: SizePreset,
    host_cores: int,
    vm_memory_limit_mb: int,
    free_disk_gb: int | None,
) -> bool:
    """True when the preset can be created on this host.

    *vm_memory_limit_mb* is defaults.max_vm_memory_mb() (0 = unknown) and
    *free_disk_gb* is the free space of the chosen storage (None = unknown)."""
    if host_cores > 0 and preset.cores > host_cores:
        return False
    if vm_memory_limit_mb > 0 and preset.memory_mb > vm_memory_limit_mb:
        return False
    if free_disk_gb is not None and preset.disk_gb > free_disk_gb:
        return False
    return True

