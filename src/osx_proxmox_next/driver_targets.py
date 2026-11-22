"""Which driver a Mac has and which version an update would put on it: NullMoth and WhateverXe come from GitHub
releases (the newest unless the user picked one), Lilu and WhateverGreen are the versions this release pins."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple
from pathlib import Path

from . import driver_engine as engine, gpu, igpu_engine, nullmoth_engine
from .oc_builder import OC_COMPONENTS

__all__ = ["Choice", "Target", "detect_kind", "latest_known", "pinned_amd", "resolve"]

AMD_PINNED = ("Lilu", "WhateverGreen")


@dataclass
class Choice:
    """The releases the user picked; an empty one means the latest."""
    nullmoth: str = ""
    wex: str = ""
    graphics: str = ""
    macos: str = ""        # when the Mac's description or name does not say which macOS it runs


@dataclass
class Target:
    kind: str
    components: dict[str, dict] = field(default_factory=dict)   # name -> {version, tag, sha256, source}
    picks: dict = field(default_factory=dict)                    # the releases as the engines resolved them

    def versions(self) -> dict[str, str]:
        return {name: c["version"] for name, c in self.components.items()}


class Detected(NamedTuple):
    kind: str          # nvidia, intel, amd; '' when the Mac has no graphics card or its kind cannot be told
    conflict: bool     # the card on the host is of another kind than the Mac's driver record says
    readable: bool     # a passed-through device could be looked up on this host


def detect_kind(vmid: int, cfg: dict[str, str], sysfs_root: Path = Path("/sys")) -> Detected:
    """nvidia, intel or amd for a Mac with a card on hostpci: the vendor of the passed-through device of PCI class 0x03
    on this host decides. A USB controller or a NIC is no card. The driver record and the attach record must agree
    with it (else `conflict`); without a readable device they decide."""
    if not any(k.startswith("hostpci") for k in cfg):
        return Detected("", False, True)
    record = engine.read_record(vmid)
    attach = gpu.load_record(vmid)
    recorded = record.get("kind") if record.get("kind") in engine.KINDS else (
        attach.vendor if attach is not None and attach.vendor in engine.KINDS else "")
    live, readable = engine.live_kind(cfg, str(sysfs_root))
    return Detected(*engine.resolve_kind(recorded, live, readable), readable)


def pinned_amd() -> dict[str, dict]:
    return {n: {"version": OC_COMPONENTS[n].version, "tag": OC_COMPONENTS[n].version,
                "sha256": OC_COMPONENTS[n].sha256, "source": "pinned"} for n in AMD_PINNED}


def resolve(kind: str, choice: Choice, get: Callable | None = None) -> Target:
    """The release(s) an update to *choice* would install. Raises nullmoth_engine.ReleaseError (a ReleaseError of
    the iGPU engine is the same class) when GitHub cannot be asked or a release cannot be used."""
    if kind == "amd":
        return Target(kind, pinned_amd())
    if kind == "nvidia":
        pick = nullmoth_engine.resolve(choice.nullmoth or nullmoth_engine.LATEST, get or nullmoth_engine.github_get)
        comp = {"version": pick["tag"], "tag": pick["tag"], "sha256": pick.get("digest", ""), "source": pick["source"]}
        return Target(kind, {"NullMoth": comp}, {"nullmoth": pick})
    picks = igpu_engine.select_assets(choice.wex or igpu_engine.LATEST, choice.graphics or igpu_engine.LATEST, get)
    comps = {name: {"version": picks[key]["version"], "tag": picks[key]["tag"], "sha256": picks[key]["sha256"],
                    "source": picks[key]["source"]}
             for key, name in (("whateverxe", "WhateverXe"), ("graphics", "WhateverXe Graphics"))}
    return Target(kind, comps, picks)


def latest_known(get: Callable | None = None) -> dict[str, str]:
    """{component: newest published version} for the daily check. Raises ReleaseError when GitHub cannot be asked."""
    out = {"NullMoth": nullmoth_engine.resolve(nullmoth_engine.LATEST, get or nullmoth_engine.github_get)["tag"]}
    for key, name in (("whateverxe", "WhateverXe"), ("graphics", "WhateverXe Graphics")):
        out[name] = igpu_engine.resolve(key, igpu_engine.LATEST, get)["version"]
    return out
