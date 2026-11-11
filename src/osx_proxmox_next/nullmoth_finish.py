"""Attach an NVIDIA card and install the NullMoth driver in an installed macOS VM, with no steps inside macOS.

The driver's installer needs the card present in a running macOS, and the approval prompt for its kernel
extensions can only be switched off from recovery. So, like igpu_finish.py (whose staging code this reuses):

1. The host downloads the newest NullMoth release (or the one the user picked), verifies it against the
   release's SHA256SUMS and the GitHub digest, and builds a payload disk (driver, NVIDIA's GPU firmware package
   when the release pins one, first-boot script, LaunchDaemon).
2. The VM boots macOS recovery with the emulated display (the card is not attached yet) and the recovery
   Terminal is driven by key presses. One command runs the staging script: it copies everything into the
   installed macOS, runs `spctl kext-consent disable` and powers off.
3. The card is attached (OpenCore for NullMoth, hostpci0, MMIO window) and the VM boots normally with the
   payload disk still attached. The LaunchDaemon turns on Remote Login and Screen Sharing, keeps the Mac awake,
   runs the driver's installer and restarts; after the restart it waits until the driver is loaded, reports
   on the payload disk and powers off.
4. The host reads the result, drops the payload disk, starts the VM and looks up the guest's address.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from . import nullmoth, nullmoth_engine as engine
from .assets import resolve_recovery_or_installer_path
from .defaults import DEFAULT_ISO_DIR
from .downloader import DownloadError
from .executor import apply_plan
from .gpu import parse_config
from .igpu_attach import oc_step
from .igpu_finish import (
    FinishError, _drop_disk, _import_disk, _put_back, _stage, _storage_of, wait_stopped,
)
from .infrastructure import ProxmoxAdapter
from .oc_builder import ensure_amfipass
from .planner import POST_INSTALL_BOOT_ORDER
from .rollback import create_snapshot
from .services import fetch_vm_info, get_proxmox_adapter
from .unattended import QmConsole

__all__ = ["FinishError", "FinishResult", "finish", "versions"]

FIRSTBOOT_TIMEOUT = 2700     # boot, the driver's installer (it builds two kernel collections), restart, load wait
RESULT_POLL = 30
RESULT_GRACE = 15            # let a result that was seen mid-write finish before it is trusted
REBOOT_GRACE = 420           # after "installed": the guest restarts by itself; past this it is stopped and started
HALT_WAIT = 90               # after the final result: time for the guest's own power off
IP_TIMEOUT = 300             # the restarted guest needs a minute or two to boot and get its address
IP_POLL = 15
SSH_PORT, VNC_PORT = 22, 5900


@dataclass
class FinishResult:
    version: str = ""
    verified: bool = False       # the driver is loaded (4+ NullMoth extensions), as the guest reported it
    result: str = ""             # the guest's last report
    detail: str = ""             # why it failed, when it did
    kexts: int = 0
    display: str = ""            # yes or no: a monitor (EDID) was seen on the card; empty when unknown
    ip: str = ""
    ssh_up: bool = False
    vnc_up: bool = False
    notes: list[str] = field(default_factory=list)


def _read_payload_result(pve: ProxmoxAdapter, ref: str) -> str:
    path = pve.pvesm("path", ref).output.strip()
    return engine.read_result(path) if path else ""


def versions(get: Callable = engine.github_get) -> list[dict]:
    """Every selectable NullMoth release, newest first. The first stable one is the default."""
    try:
        return engine.list_releases(get)
    except engine.ReleaseError as exc:
        raise FinishError(str(exc)) from exc


def _check_vm(vmid: int, pve: ProxmoxAdapter) -> dict[str, str]:
    info = fetch_vm_info(vmid, adapter=pve)
    if info is None:
        raise FinishError(f"VM {vmid} not found.")
    cfg = parse_config(info.config_raw)
    if "hostpci0" in cfg:
        raise FinishError(f"VM {vmid} already has a PCI device on hostpci0. Run nvidia-detach (or gpu-setup --undo) "
                          "first, then try again.")
    if info.status != "stopped":
        raise FinishError(f"VM {vmid} is running. Stop it first: qm stop {vmid}")
    return cfg


def _event(on_event: Callable[[str], None]) -> Callable[[str], None]:
    """The staging code is shared with the iGPU finish and words its progress for that package."""
    return lambda m: on_event(m.replace("graphics package", "NullMoth driver"))


def _download(choice: str, iso: Path, on_event: Callable[[str], None], get: Callable, read: Callable,
              fetch: Callable) -> tuple[dict, Path, str, Path | None, str, list[str]]:
    """Resolve, download and verify the driver and, when the release pins it, NVIDIA's firmware package."""
    notes: list[str] = []
    try:
        pick = engine.resolve(choice, get)
        on_event(f"NullMoth {pick['tag']} ({'latest' if pick['source'] == 'latest' else 'chosen'}): downloading and verifying")
        tarball, sha = engine.fetch_release(pick, iso / "nullmoth", read, fetch)
        on_event(f"Verified {tarball.name}: sha256 {sha[:16]}... matches SHA256SUMS and the GitHub digest")
    except engine.ReleaseError as exc:
        raise FinishError(str(exc)) from exc
    firmware: Path | None = None
    fw_sha = ""
    pins = engine.firmware_pins(tarball)
    if pins is None:
        notes.append("This release does not pin NVIDIA's firmware package, so the guest downloads it itself.")
    else:
        on_event(f"Downloading NVIDIA's {pins['version']} firmware package (the guest then needs no network)")
        try:
            firmware = engine.fetch_firmware(pins, iso / "nullmoth", fetch)
            fw_sha = pins["sha256"]
        except engine.ReleaseError as exc:
            notes.append(f"Could not download NVIDIA's firmware package here ({exc}). The guest tries it itself.")
    return pick, tarball, sha, firmware, fw_sha, notes


def _wait_result(pve: ProxmoxAdapter, vmid: int, ref: str, timeout: float, on_event: Callable[[str], None],
                 clock: Callable[[], float], sleep: Callable[[float], None]) -> str:
    """Wait for the guest's final report (verified or failed). After "installed" the guest restarts by itself; a
    restart that hangs is ended once by stopping and starting the VM. Returns the last report seen."""
    deadline = clock() + timeout
    installed_at: float | None = None
    kicked = False
    last = ""
    while clock() < deadline:
        last = _read_payload_result(pve, ref)
        if engine.is_final(last):
            sleep(RESULT_GRACE)
            return _read_payload_result(pve, ref) or last
        if last == "installed" and installed_at is None:
            installed_at = clock()
            on_event("The driver is installed. The guest restarts to load it")
        if last == "verifying" and installed_at is not None:
            installed_at = None  # the restart worked: the guest is waiting for the driver to load, never kick that
            kicked = True
        if installed_at is not None and not kicked and clock() - installed_at > REBOOT_GRACE:
            on_event("The restart hung; stopping and starting the VM")
            pve.qm("stop", str(vmid))
            wait_stopped(pve, vmid, 120, clock, sleep)
            pve.qm("start", str(vmid))
            kicked = True
        sleep(RESULT_POLL)
    return last


def _guest_address(pve: ProxmoxAdapter, vmid: int, reported: str, result: FinishResult,
                   clock: Callable[[], float], sleep: Callable[[float], None],
                   find: Callable[[str], str], probe: Callable[[str, int], bool]) -> None:
    """The guest's address (what it reported, else the host's neighbour table) and whether SSH and Screen Sharing answer."""
    mac = engine.vm_mac(parse_config(pve.qm("config", str(vmid)).output))
    deadline = clock() + IP_TIMEOUT
    while True:
        ip = find(mac) or reported
        if ip:
            result.ip = ip
            result.ssh_up, result.vnc_up = probe(ip, SSH_PORT), probe(ip, VNC_PORT)
            if result.ssh_up:
                return
        if clock() >= deadline:
            return
        sleep(IP_POLL)


def _attach_and_boot(vmid: int, gpu: str, amfipass: Path, macos: str, pve: ProxmoxAdapter, payload_ref: str,
                     result: FinishResult, on_event: Callable[[str], None], clock: Callable[[], float],
                     sleep: Callable[[float], None], sysfs_root: Path) -> None:
    """Attach the card, boot with the payload disk and wait for the guest's last report (stored in result.result)."""
    on_event("Attaching the card")
    plan = nullmoth.prepare_attach(vmid, gpu, amfipass, macos=macos or None, adapter=pve, sysfs_root=sysfs_root)
    if not plan.ok:
        raise FinishError("Cannot attach the card: " + " ".join(plan.issues))
    applied = apply_plan(plan.steps, execute=True, adapter=pve)
    if not applied.ok:
        failed = next((r for r in applied.results if not r.ok), None)
        raise FinishError("Attaching the card failed" + (f" at: {failed.title}" if failed else "")
                          + f". Log: {applied.log_path}. Undo with: nvidia-detach {vmid}")
    pve.qm("set", str(vmid), "--boot", POST_INSTALL_BOOT_ORDER)

    on_event("First boot with the card: the guest turns on remote access, installs the driver and restarts "
             "(about 10 minutes)")
    res = pve.qm("start", str(vmid))
    if not res.ok:
        raise FinishError(f"Could not start VM {vmid}: {res.output.strip()[-300:]}")
    result.result = _wait_result(pve, vmid, payload_ref, FIRSTBOOT_TIMEOUT, on_event, clock, sleep)


def _cleanup(pve: ProxmoxAdapter, vmid: int, payload: Path, clock: Callable[[], float],
             sleep: Callable[[float], None]) -> None:
    """A failure after staging: stop the VM if it runs, drop the payload disk and delete the image. The card, if it was
    attached, stays (undo with nvidia-detach); the first-boot job in the guest then finds no card and ends itself."""
    if "stopped" not in pve.qm("status", str(vmid)).output:
        pve.qm("stop", str(vmid))
        wait_stopped(pve, vmid, 120, clock, sleep)
    _drop_disk(pve, vmid, "sata2")
    payload.unlink(missing_ok=True)


def finish(
    vmid: int,
    gpu: str,
    choice: str = engine.LATEST,
    console: QmConsole | None = None,
    pve: ProxmoxAdapter | None = None,
    iso_dir: Path | None = None,
    on_event: Callable[[str], None] = lambda _m: None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    macos: str = "",
    work_dir: Path | None = None,
    get: Callable = engine.github_get,
    read: Callable = engine._read_url,
    fetch: Callable = engine.download,
    find_ip: Callable[[str], str] = engine.find_guest_ip,
    probe: Callable[[str, int], bool] = engine.port_open,
    sysfs_root: Path = Path("/sys"),
) -> FinishResult:
    """Run the whole setup for *vmid* (stopped, installed). Raises FinishError when it cannot go on."""
    pve = pve or get_proxmox_adapter()
    console = console or QmConsole(vmid)
    iso = iso_dir or Path(DEFAULT_ISO_DIR)
    work = work_dir or iso / "nullmoth"  # not the ISO folder itself: Proxmox would list the payload image there
    work.mkdir(parents=True, exist_ok=True)
    say = _event(on_event)
    cfg = _check_vm(vmid, pve)
    try:
        amfipass = ensure_amfipass(iso)
    except DownloadError as exc:
        raise FinishError(f"Could not fetch AMFIPass: {exc}") from exc
    # the same preflight the attach will run, so nothing is downloaded or staged for a VM that could not take the card
    plan = nullmoth.prepare_attach(vmid, gpu, amfipass, macos=macos or None, adapter=pve, sysfs_root=sysfs_root)
    if not plan.ok:
        raise FinishError("Cannot attach the card: " + " ".join(plan.issues))
    version = (macos or nullmoth.detect_macos(cfg) or "").lower()
    result = FinishResult(notes=list(plan.warnings))
    pick, tarball, tar_sha, firmware, fw_sha, notes = _download(choice, iso, say, get, read, fetch)
    result.version, result.notes = pick["tag"], result.notes + notes
    cfg = _check_vm(vmid, pve)  # the downloads took a while: the VM must still be stopped
    storage = _storage_of(cfg)
    snapshot = create_snapshot(vmid, adapter=pve)
    say(f"Saved the VM config to {snapshot.path}")

    payload = work / f"nullmoth-payload-{vmid}.img"
    say("Building the payload disk (driver, first-boot script)")
    files = engine.payload_files(tarball, tar_sha, firmware, fw_sha)
    ok, msg = engine.build_payload(payload, files)
    if not ok:
        raise FinishError(msg)
    recovery = resolve_recovery_or_installer_path(SimpleNamespace(installer_path="", macos=version))  # type: ignore[arg-type]
    if not recovery.exists():
        from .downloader import download_recovery
        say(f"Downloading the {version} recovery image")
        recovery = download_recovery(version, iso)
    payload_ref = ""
    original = {"vga": cfg.get("vga"), "args": cfg.get("args"), "boot": cfg.get("boot")}
    try:
        payload_ref = _import_disk(pve, vmid, payload, storage)
        recovery_ref = _import_disk(pve, vmid, recovery, storage)
        for argv in (["--sata1", f"{recovery_ref},media=disk"], ["--sata2", f"{payload_ref},media=disk"],
                     ["--boot", "order=sata1;virtio0;ide0"]):
            res = pve.qm("set", str(vmid), *argv)
            if not res.ok:
                raise FinishError(f"qm set {argv[0]} failed: {res.output.strip()[-300:]}")
        if cfg.get("vga") not in (None, "std"):
            pve.qm("set", str(vmid), "--vga", "std")
        _stage(vmid, pve, console, payload_ref, say, clock, sleep)
    except BaseException:
        _drop_disk(pve, vmid, "sata2")
        payload.unlink(missing_ok=True)
        _put_back(pve, vmid, original)
        console.restore_picker_timeout()
        raise
    finally:
        _drop_disk(pve, vmid, "sata1")
        # best effort: the picker must show every volume again whatever happened above
        apply_plan([oc_step(str(vmid), "unstage", "Show every volume in the OpenCore picker again")],
                   execute=True, adapter=pve)
    err = console.restore_picker_timeout()
    if err:
        result.notes.append(f"Could not restore the picker's auto-boot ({err}); run post-install afterwards.")

    try:
        _attach_and_boot(vmid, gpu, amfipass, macos, pve, payload_ref, result, on_event, clock, sleep, sysfs_root)
    except BaseException:
        _cleanup(pve, vmid, payload, clock, sleep)
        raise
    last = result.result
    report = engine.parse_result(last)
    if not engine.is_final(last):
        result.notes.append(f"The guest gave no final report within {FIRSTBOOT_TIMEOUT // 60} minutes "
                            f"(last report: {last or 'none'}), so the VM was restarted without the payload disk. Whether the driver "
                            "installed is not known: check `kmutil showloaded --list-only | grep -i nullmoth` in the guest "
                            "(its first-boot job may run the installer once more).")
    if "stopped" not in pve.qm("status", str(vmid)).output:
        wait_stopped(pve, vmid, HALT_WAIT, clock, sleep)
    if "stopped" not in pve.qm("status", str(vmid)).output:
        pve.qm("stop", str(vmid))  # the guest's own power off can hang with the card; its result was read already
        wait_stopped(pve, vmid, 120, clock, sleep)
    _drop_disk(pve, vmid, "sata2")
    payload.unlink(missing_ok=True)
    result.verified = report["state"] == "verified"
    result.kexts, result.display = int(report["kexts"] or 0), report["display"]
    result.detail = report["detail"] if report["state"] == "failed" else ""
    pve.qm("start", str(vmid))
    if result.verified:
        on_event("Started the VM again; looking for its address")
        _guest_address(pve, vmid, report["ip"], result, clock, sleep, find_ip, probe)
    return result
