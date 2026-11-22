"""Update Lilu and WhateverGreen on a Mac with an AMD card: the kexts on its OpenCore disk that are behind the versions
this release pins are replaced (one that is newer than the pin is left alone). macOS has the AMD driver itself, so
there is no recovery and the card stays on."""

from __future__ import annotations

from pathlib import Path

from . import driver_engine as engine, driver_oc
from .defaults import DEFAULT_ISO_DIR
from .downloader import DownloadError
from .driver_core import DriverError, Updater
from .driver_targets import AMD_PINNED
from .oc_builder import ensure_component

__all__ = ["run"]


def run(u: Updater) -> None:
    iso = u.env.iso_dir or Path(DEFAULT_ISO_DIR)
    comps = u.plan.target.components
    names = engine.replace_names(u.plan.installed, u.plan.target.versions(), u.plan.reinstall)
    u.result.kept = {n for n in AMD_PINNED if n not in names}
    try:
        zips = [(n, ensure_component(n, iso), comps[n]["sha256"]) for n in names]
    except DownloadError as exc:
        raise DriverError(str(exc)) from exc
    u.stop()
    u.snapshot()
    if u.begin("replace"):
        u.need_stopped()          # a resumed run goes on from here: never write the OpenCore disk of a running Mac
        try:
            found = driver_oc.replace_kexts(u.pve, u.vmid, zips, u.result.notes)
        except driver_oc.OcError as exc:
            raise DriverError(str(exc)) from exc
        for name, version in found.items():
            if version and version != comps[name]["version"]:
                u.result.notes.append(f"{name} reports version {version}, the pin is {comps[name]['version']}.")
        u.say("Replaced " + " and ".join(f"{n} {comps[n]['version']}" for n in names)
              + "".join(f". {n} {u.plan.installed.get(n, '')} is newer than the pin and was kept" for n in sorted(u.result.kept)))
        u.end("replace")
    if u.begin("start"):
        u.start()
        u.end("start")
    u.result.detail = ("the kexts that were behind are now the pinned versions" if u.result.kept
                       else "the kexts on the OpenCore disk are the pinned versions")
