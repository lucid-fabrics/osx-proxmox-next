"""Update WhateverXe and its graphics package on a Mac with the Intel iGPU: the kext is swapped with the iGPU
patcher's own attach (it replaces its two Kernel > Add entries, never adds a second) and the package goes in through
the same payload disk and recovery staging as the first setup."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from . import igpu_engine
from .assets import resolve_recovery_or_installer_path
from .defaults import DEFAULT_ISO_DIR
from .downloader import DownloadError
from .driver_core import DriverError, Updater
from .executor import apply_plan
from .igpu_attach import oc_step
from .planner import stopped_guard
from .igpu_finish import (
    FIRSTBOOT_TIMEOUT, FinishError, StageJob, _drop_disk, _read_payload_result, _storage_of, _wait_first_boot,
    stage_recovery, wait_stopped,
)
from .oc_builder import ensure_igpu_assets

__all__ = ["run"]


def _assets(u: Updater):
    env, comps = u.env, u.plan.target.components
    iso = env.iso_dir or Path(DEFAULT_ISO_DIR)
    work = env.work_dir or iso
    u.say("Downloading WhateverXe %s and the graphics package %s" % (comps["WhateverXe"]["version"],
                                                                    comps["WhateverXe Graphics"]["version"]))
    try:
        assets = ensure_igpu_assets(iso, None, wex=comps["WhateverXe"]["tag"],
                                    graphics=comps["WhateverXe Graphics"]["tag"], get=env.get)
    except (igpu_engine.ReleaseError, DownloadError, OSError) as exc:
        raise DriverError(str(exc)) from exc
    payload = work / f"igpu-payload-{u.vmid}.img"
    ok, msg = igpu_engine.build_payload(str(assets["graphics"]), str(payload))
    if not ok:
        raise DriverError(msg)
    recovery = resolve_recovery_or_installer_path(SimpleNamespace(installer_path="", macos=u.plan.macos))  # type: ignore[arg-type]
    if not recovery.exists():
        from .downloader import download_recovery
        u.say(f"Downloading the {u.plan.macos} recovery image")
        recovery = download_recovery(u.plan.macos, iso)
    return assets, payload, recovery


def guarded_step(vmid: int, action: str, title: str, *args):
    """The patcher's OpenCore step with the stopped check inside its own bash, so it never writes a running Mac's disk."""
    step = oc_step(str(vmid), action, title, *args)
    step.argv[2] = stopped_guard(str(vmid)) + step.argv[2]
    return step


def _swap_kext(u: Updater, assets) -> None:
    """WhateverXe.kext and AMFIPass.kext on the OpenCore disk, through the patcher's attach."""
    picks = assets.picks
    step = guarded_step(u.vmid, "attach", "Replace WhateverXe and AMFIPass on the OpenCore disk",
                        assets["whateverxe"], assets["amfipass"], picks["whateverxe"]["sha256"],
                        igpu_engine.PINS["amfipass"][2])
    applied = apply_plan([step], execute=True, adapter=u.pve)
    if not applied.ok:
        raise DriverError(f"Replacing WhateverXe on the OpenCore disk failed. Log: {applied.log_path}")
    u.result.installed["AMFIPass"] = {"version": igpu_engine.AMFIPASS_VERSION, "tag": igpu_engine.AMFIPASS_VERSION,
                                      "sha256": igpu_engine.PINS["amfipass"][2], "source": "pinned"}


def run(u: Updater) -> None:
    env, pve, vmid = u.env, u.pve, u.vmid
    assets, payload, recovery = _assets(u)
    tahoe = u.plan.macos == "tahoe"
    u.stop()
    u.snapshot()
    u.take_card_off()
    if u.begin("stage"):
        u.need_stopped()          # a resumed run goes on from here: the Mac stopped and the card off, whatever the rows say
        u.need_card_off()
        if u.resumed:
            for slot in ("sata1", "sata2"):
                _drop_disk(pve, vmid, slot)
        job = StageJob(vmid, pve, env.console, _storage_of(u.plan.cfg), u.say, env.clock, env.sleep, tahoe,
                       tuple(igpu_engine.vm_settings(0, "")["args_add"]))
        try:
            u.state.update(payload_ref=stage_recovery(job, payload, recovery, u.staging_cfg()), staged=True)
            u.persist()
        except FinishError as exc:
            raise DriverError(str(exc)) from exc
        err = env.console.restore_picker_timeout() if env.console else ""
        if err:
            u.result.notes.append(f"Could not restore the picker's auto-boot ({err}); run post-install afterwards.")
        _swap_kext(u, assets)
        u.end("stage")
    u.put_card_back()
    if u.begin("start"):
        u.start()
        u.say("First boot with the card: the guest installs the graphics package and powers off")
        u.end("start")
    u.begin("verify")
    ref = u.payload_ref()
    if not _wait_first_boot(pve, vmid, FIRSTBOOT_TIMEOUT, tahoe, ref, env.clock, env.sleep):
        pve.qm("stop", str(vmid))
        wait_stopped(pve, vmid, 120, env.clock, env.sleep)
    result = _read_payload_result(pve, ref)
    _drop_disk(pve, vmid, "sata2")
    payload.unlink(missing_ok=True)
    if result != "installed":
        pve.qm("start", str(vmid))
        raise DriverError("The graphics package is not installed: " + (result or "the guest reported nothing") + ".")
    if not apply_plan([guarded_step(vmid, "mark", "Note in OpenCore that the graphics package is installed")],
                      execute=True, adapter=pve).ok:
        u.result.notes.append("Could not note the installed package on the OpenCore disk.")
    pve.qm("start", str(vmid))
    u.say("Started the Mac again")
    accel = env.accel or igpu_engine.check_accel
    ok, detail = accel(clock=env.clock, sleep=env.sleep)
    u.result.detail = "macOS uses the iGPU" if ok else "the iGPU is not used yet (" + str(detail) + ")"
    if not ok:
        u.result.notes.append("The host did not see macOS use the iGPU yet: " + str(detail))
    u.end("verify")
