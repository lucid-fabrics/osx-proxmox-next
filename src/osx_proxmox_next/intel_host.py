"""Intel iGPU vendor profile: which integrated graphics WhateverXe can drive, and the host checks.

The decisions live in igpu_engine.py (standard library only, so the bash installer embeds the same
file); this module adapts them to the vendor-neutral types the GPU walkthrough uses. The allowlist
is derived from the WhateverXe release that will be installed (igpu_engine.device_list); data/gpu/intel_devices.txt
is only the offline fallback.
"""

from __future__ import annotations

from pathlib import Path

from . import gpu, igpu_engine
from .gpu import GpuInfo, GpuReport

__all__ = [
    "INTEL_VENDOR",
    "IgpuCheck",
    "check_intel_gpu",
    "device_list",
    "detect_intel_gpus",
    "is_supported_device",
    "lookup",
    "verdict",
]

INTEL_VENDOR = igpu_engine.VENDOR
lookup = igpu_engine.lookup
verdict = igpu_engine.verdict


def device_list(wex: str = igpu_engine.LATEST) -> dict:
    """The supported chips of WhateverXe *wex* ("latest" or a tag); the bundled list, labelled, when GitHub is down."""
    return igpu_engine.device_list(wex)


def is_supported_device(device_id: int) -> bool:
    return verdict(device_id)[0] != "unsupported"


def detect_intel_gpus(root: Path = Path("/sys")) -> list[GpuInfo]:
    return gpu.detect_gpus(INTEL_VENDOR, is_supported_device, root)


class IgpuCheck(GpuReport):
    """A GpuReport that also carries the engine's findings (device, layout, ROM file)."""

    def __init__(self, bdf: str, engine: dict) -> None:
        super().__init__(bdf, issues=list(engine["issues"]), warnings=list(engine["warnings"]),
                         hints=list(engine["hints"]))
        self.engine = engine

    @property
    def layout(self) -> dict | None:
        return self.engine["layout"]

    @property
    def rom_file(self) -> str:
        return self.engine["info"]["rom_file"] if self.engine["info"] else ""


def check_intel_gpu(root: Path = Path("/"), run=igpu_engine.default_run, wex: str = igpu_engine.LATEST) -> IgpuCheck:
    """Every host-side check for the iGPU at 00:02.0 (the only slot Intel puts it in), judged against WhateverXe *wex*."""
    return IgpuCheck(igpu_engine.IGD_BDF.rsplit(".", 1)[0], igpu_engine.check(str(root), run, wex=wex))
