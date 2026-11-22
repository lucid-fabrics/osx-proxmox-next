"""Update Lilu and WhateverGreen on a Mac with an AMD card: the two kexts on its OpenCore disk are replaced by the
versions this release pins. macOS has the AMD driver itself, so there is no recovery and the card stays on."""

from __future__ import annotations

from pathlib import Path

from . import driver_oc
from .defaults import DEFAULT_ISO_DIR
from .downloader import DownloadError
from .driver_core import DriverError, Updater
from .driver_targets import AMD_PINNED
from .oc_builder import ensure_component

__all__ = ["run"]


def run(u: Updater) -> None:
    iso = u.env.iso_dir or Path(DEFAULT_ISO_DIR)
    comps = u.plan.target.components
    try:
        zips = [(n, ensure_component(n, iso), comps[n]["sha256"]) for n in AMD_PINNED]
    except DownloadError as exc:
        raise DriverError(str(exc)) from exc
    u.stop()
    u.snapshot()
    u.begin("replace")
    try:
        found = driver_oc.replace_kexts(u.pve, u.vmid, zips)
    except driver_oc.OcError as exc:
        raise DriverError(str(exc)) from exc
    for name, version in found.items():
        if version and version != comps[name]["version"]:
            u.result.notes.append(f"{name} reports version {version}, the pin is {comps[name]['version']}.")
    u.say("Replaced " + " and ".join(f"{n} {comps[n]['version']}" for n in AMD_PINNED))
    u.end("replace")
    u.begin("start")
    u.start()
    u.end("start")
    u.result.detail = "the kexts on the OpenCore disk are the pinned versions"
