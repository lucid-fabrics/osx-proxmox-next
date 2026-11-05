"""AMD vendor profile: which Radeon cards macOS can drive, and how to attach them.

macOS ships its own AMD drivers, so no third-party driver is involved. The
allowlist lives in data/gpu/amd_devices.txt (sources in its header); the bash
installer embeds the same file and tests/test_gpu_parity.py diffs the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path

from . import gpu
from .gpu import HOTPLUG_ARG, GpuInfo, GpuProfile, GpuReport

__all__ = [
    "AMD_PROFILE",
    "AMD_VENDOR",
    "AmdFamily",
    "boot_args_for",
    "check_amd_gpu",
    "data_text",
    "detect_amd_gpus",
    "family_for",
    "hostpci_extra",
    "is_supported_device",
    "needs_vendor_reset",
    "reset_note",
    "spoofed_id",
    "unsupported_reason",
]

AMD_VENDOR = 0x1002
AMD_PROFILE = GpuProfile(vendor="amd", pcie=1, global_args=(HOTPLUG_ARG,), sidecar="gpu")
_GENERIC_REASON = "This AMD GPU has no Apple driver (APUs and cards newer than the RX 6000 series are not supported)."
_VEGA20_WARNING = (
    "Radeon VII / Pro VII: stopping and starting a VM that holds this card without rebooting the host "
    "has corrupted host storage on Proxmox 9 with vendor-reset (vendor-reset issue 112). Reboot the host "
    "between VM runs and do not rely on automatic restarts."
)


@dataclass(frozen=True)
class AmdFamily:
    key: str
    label: str
    reset: str  # vendor-reset | vendor-reset-warn | early-bind
    boot_args: tuple[str, ...]
    tahoe: str  # ok | unverified
    ids: frozenset[int]


@dataclass(frozen=True)
class _Data:
    families: tuple[AmdFamily, ...]
    spoof: dict[int, tuple[int, str]]  # real id -> (id the guest sees, family key)
    deny: tuple[tuple[str, frozenset[int], str], ...]


def data_text() -> str:
    return (resources.files("osx_proxmox_next") / "data/gpu/amd_devices.txt").read_text()


def _parse_ids(text: str) -> frozenset[int]:
    out: set[int] = set()
    for token in text.split():
        lo, _, hi = token.partition("-")
        out.update(range(int(lo, 16), int(hi or lo, 16) + 1))
    return frozenset(out)


@lru_cache(maxsize=1)
def _load() -> _Data:
    families: list[AmdFamily] = []
    spoof: dict[int, tuple[int, str]] = {}
    deny: list[tuple[str, frozenset[int], str]] = []
    for line in data_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("|")
        if parts[0] == "spoof":
            spoof[int(parts[1], 16)] = (int(parts[2], 16), parts[3])
        elif parts[0] == "deny":
            deny.append((parts[1], _parse_ids(parts[2]), parts[3]))
        else:
            key, label, reset, args, tahoe, ids = parts
            families.append(AmdFamily(key, label, reset, tuple(args.split()), tahoe, _parse_ids(ids)))
    return _Data(tuple(families), spoof, tuple(deny))


def family_for(device_id: int) -> AmdFamily | None:
    """Family of a supported card (spoofed ids resolve to the family they are spoofed into)."""
    data = _load()
    target = data.spoof[device_id][1] if device_id in data.spoof else None
    for fam in data.families:
        if device_id in fam.ids or fam.key == target:
            return fam
    return None


def is_supported_device(device_id: int) -> bool:
    return family_for(device_id) is not None


def unsupported_reason(device_id: int) -> str:
    for _label, ids, reason in _load().deny:
        if device_id in ids:
            return reason
    return _GENERIC_REASON


def spoofed_id(device_id: int) -> int | None:
    entry = _load().spoof.get(device_id)
    return entry[0] if entry else None


def hostpci_extra(device_id: int) -> str:
    """Extra hostpci option that makes the guest see a supported device id."""
    sid = spoofed_id(device_id)
    return f",device-id=0x{sid:04x}" if sid is not None else ""


def boot_args_for(device_id: int) -> tuple[str, ...]:
    fam = family_for(device_id)
    return fam.boot_args if fam else ()


def needs_vendor_reset(device_id: int) -> bool:
    fam = family_for(device_id)
    return fam is not None and fam.reset.startswith("vendor-reset")


def reset_note(device_id: int) -> str:
    """One line on what the reset bug means for this card."""
    fam = family_for(device_id)
    if fam is None:
        return ""
    if fam.reset.startswith("vendor-reset"):
        return ("This family has the AMD reset bug: without the vendor-reset module the card may only "
                "work once per host boot.")
    return "This card resets cleanly if it is bound to vfio-pci before amdgpu loads (the setup does that)."


def detect_amd_gpus(root: Path = Path("/sys")) -> list[GpuInfo]:
    return gpu.detect_gpus(AMD_VENDOR, is_supported_device, root)


def check_amd_gpu(bdf: str, root: Path = Path("/sys"), cmdline_path: Path | None = None) -> GpuReport:
    report = gpu.check_gpu(bdf, AMD_VENDOR, is_supported_device, unsupported_reason, root, cmdline_path,
                           bar_index=0)
    dev_id = gpu._read_hex(gpu._devices(root) / f"{report.bdf}.0" / "device")
    fam = family_for(dev_id) if dev_id is not None else None
    if fam is not None and fam.key == "vega20":
        report.warnings.append(_VEGA20_WARNING)
    if fam is not None and fam.tahoe == "unverified":
        report.warnings.append(
            f"{fam.label}: no report of this family on macOS Tahoe 26 yet. Ventura to Sequoia are fine."
        )
    return report
