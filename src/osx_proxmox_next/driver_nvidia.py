"""Update the NullMoth driver of a Mac that has an NVIDIA card. The staging code is the card setup's: the same
download and verification, the same payload disk, the same recovery staging and the same first-boot check."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from . import driver_oc, nullmoth_engine as nm
from .assets import resolve_recovery_or_installer_path
from .defaults import DEFAULT_ISO_DIR
from .downloader import DownloadError
from .driver_core import DriverError, Updater
from .igpu_finish import FinishError, StageJob, _storage_of
from .nullmoth_finish import FIRSTBOOT_TIMEOUT, FinishResult, _download, _wait_result, conclude
from .oc_builder import AMFIPASS, ensure_amfipass

__all__ = ["run"]


def _assets(u: Updater) -> tuple[dict, Path, str, Path, Path]:
    """Everything the update needs, downloaded and verified before the Mac is touched: (pick, tarball sha, payload,
    recovery image, AMFIPass zip)."""
    env = u.env
    iso = env.iso_dir or Path(DEFAULT_ISO_DIR)
    work = env.work_dir or iso / "nullmoth"
    work.mkdir(parents=True, exist_ok=True)
    tag = u.plan.target.components["NullMoth"]["tag"]
    try:
        amfipass = ensure_amfipass(iso)
        pick, tarball, sha, firmware, fw_sha, notes = _download(tag, iso, u.say, env.get, env.read, env.fetch)
    except (DownloadError, FinishError) as exc:
        raise DriverError(str(exc)) from exc
    u.result.notes += notes
    u.plan.target.components["NullMoth"]["sha256"] = sha
    u.say(f"Building the payload disk for NullMoth {pick['tag']}")
    payload = work / f"nullmoth-payload-{u.vmid}.img"
    ok, msg = nm.build_payload(payload, nm.payload_files(tarball, sha, firmware, fw_sha))
    if not ok:
        raise DriverError(msg)
    recovery = resolve_recovery_or_installer_path(SimpleNamespace(installer_path="", macos=u.plan.macos))  # type: ignore[arg-type]
    if not recovery.exists():
        from .downloader import download_recovery
        u.say(f"Downloading the {u.plan.macos} recovery image")
        recovery = download_recovery(u.plan.macos, iso)
    return pick, payload, recovery, amfipass, tarball


def refresh_amfipass(u: Updater, amfipass: Path) -> None:
    """AMFIPass on the OpenCore disk is replaced only when the pinned one is another version (the disk is not touched
    otherwise). The Mac is stopped, so the disk may be mounted."""
    try:
        have = driver_oc.read_versions(u.pve, u.vmid, ["AMFIPass"]).get("AMFIPass", "")
        if have and have == AMFIPASS.version:
            return
        driver_oc.replace_kexts(u.pve, u.vmid, [("AMFIPass", amfipass, AMFIPASS.sha256)], u.result.notes)
    except driver_oc.OcError as exc:
        raise DriverError(str(exc)) from exc
    u.say(f"Updated AMFIPass on the OpenCore disk to {AMFIPASS.version}")
    u.result.installed["AMFIPass"] = {"version": AMFIPASS.version, "tag": AMFIPASS.version,
                                      "sha256": AMFIPASS.sha256, "source": "pinned"}


def run(u: Updater) -> None:
    env, pve, vmid = u.env, u.pve, u.vmid
    pick, payload, recovery, amfipass, _tarball = _assets(u)
    u.stop()
    u.snapshot()
    u.take_card_off()
    storage = _storage_of(u.plan.cfg)
    if u.begin("stage"):
        u.need_stopped()          # a resumed run goes on from here: the Mac stopped and the card off, whatever the rows say
        u.need_card_off()
        job = StageJob(vmid, pve, env.console, storage, lambda m: u.say(m.replace("graphics package", "NullMoth driver")),
                       env.clock, env.sleep, on_disk=u.track_disk)
        try:
            payload_ref = _stage(u, job, payload, recovery)
        except FinishError as exc:
            raise DriverError(str(exc)) from exc
        u.state.update(payload_ref=payload_ref, staged=True)      # the driver is inside macOS now, whatever comes next
        u.persist()
        refresh_amfipass(u, amfipass)
        u.end("stage")
    u.put_card_back()
    if u.begin("start"):
        u.start()
        u.say("First boot with the card: the guest installs the driver and restarts (about 10 minutes)")
        u.end("start")
    u.begin("verify")
    result = FinishResult(version=pick["tag"])
    result.result = _wait_result(pve, vmid, u.payload_ref(), FIRSTBOOT_TIMEOUT, u.say, env.clock, env.sleep)
    conclude(pve, vmid, payload, result, u.say, env.clock, env.sleep, env.find_ip, env.probe)
    u.result.notes += result.notes
    if not result.verified:
        raise DriverError("The NullMoth driver is not confirmed: "
                          + (result.detail or result.result or "the guest reported nothing") + ".")
    u.result.detail = f"{result.kexts} extensions loaded" + (f", the guest is at {result.ip}" if result.ip else "")
    u.end("verify")


def _stage(u: Updater, job: StageJob, payload: Path, recovery: Path) -> str:
    from .igpu_finish import stage_recovery
    if u.resumed:   # a run that was cut off may have left its two disks behind
        for slot in ("sata1", "sata2"):
            u.drop_disk(slot)
    payload_ref = stage_recovery(job, payload, recovery, u.staging_cfg())
    err = u.env.console.restore_picker_timeout() if u.env.console else ""
    if err:
        u.result.notes.append(f"Could not restore the picker's auto-boot ({err}); run post-install afterwards.")
    return payload_ref
