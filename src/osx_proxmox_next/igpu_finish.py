"""Finish the Intel iGPU setup of an installed macOS VM: the graphics package, with no steps inside macOS.

WhateverXe Graphics must only be installed while WhateverXe is loaded: without it the guest panics at
boot. So the package cannot simply be copied in and installed. The order is:

1. The host builds a small FAT32 payload disk (package, first-boot script, LaunchDaemon).
2. The VM boots macOS recovery with the emulated display (the iGPU is not attached yet) and the
   recovery Terminal is driven like the unattended install does. One command runs the staging script
   from the payload disk, which copies everything into the installed macOS offline and powers off.
3. The iGPU is attached (OpenCore with WhateverXe, hostpci0, args, no emulated display) and the VM boots
   normally with the payload disk still attached. The LaunchDaemon waits until WhateverXe is loaded,
   installs the package, leaves its result on the payload disk and powers the guest off.
4. The host reads that result and starts the VM again. Metal is active from then on.

If WhateverXe never loads the package stays uninstalled (three tries at most, one per boot) and the guest
stays usable without acceleration.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import driver_disks, driver_engine, igpu_engine, igpu_watchdog
from .defaults import DEFAULT_ISO_DIR
from .executor import apply_plan
from .finish_common import (
    POLL, FinishError, _drop_disk, _storage_of, note_picker_restore, recovery_image, require_stopped, save_snapshot, start_vm,
    vm_config, wait_stopped,
)
from .gpu import parse_config
from .igpu_attach import TAHOE_NOTE, TAHOE_STAGE_NOTE, UNSUPPORTED_MACOS, UNSUPPORTED_NOTE, oc_step, prepare_attach_igpu
from .infrastructure import ProxmoxAdapter
from .intel_host import check_intel_gpu
from .nullmoth import detect_macos
from .downloader import DownloadError
from .oc_builder import ensure_igpu_assets
from .planner import POST_INSTALL_BOOT_ORDER
from .services import get_proxmox_adapter
from .unattended import QmConsole, UnattendedError, open_recovery_terminal

__all__ = ["FinishError", "FinishResult", "StageJob", "choose_release", "finish", "stage_recovery", "wait_stopped"]

STAGE_TIMEOUT = 900          # staging copies ~95 MB and halts
STAGE_ATTEMPTS = 3           # the typed command is retyped when the script never reports "started"
START_WAIT = 60              # seconds after Return, plus the time the typing itself took
START_POLL = 5
STAGE_GRACE = 20             # after the script reported its result: time for its own halt before the host stops the VM
STAGE_DONE = ("staged", "failed:")
FIRSTBOOT_TIMEOUT = 1500     # boot, WhateverXe wait (up to 120 s), package install, halt
RESULT_POLL = 30             # Tahoe cannot halt itself (WhateverXe issue 62): look for the guest's result instead
RESULT_GRACE = 15            # let a result that was seen mid-write finish before it is trusted
KNOWN_RESULTS = ("installed", "failed:", "not installed:")


@dataclass
class FinishResult:
    result: str = ""           # what the guest reported: installed, not installed: ..., failed: ...
    installed: bool = False
    notes: list[str] = field(default_factory=list)
    accelerated: bool = False  # the host saw the guest use the GPU after the final boot
    accel_detail: str = ""
    watchdog: bool = False     # the host-side power watchdog is installed
    versions: list[str] = field(default_factory=list)  # what was installed, one plain line per component


def choose_release(kind: str, ask: Callable[[str], str], say: Callable[[str], None],
                   get: Callable[[str], object] | None = None) -> str:
    """Ask which published release of WhateverXe (kind "whateverxe") or its graphics package (kind "graphics")
    to install: a list, newest first, with the latest preselected (Enter). Returns "latest" or a tag.
    Raises igpu_engine.ReleaseError when GitHub cannot be asked."""
    label = igpu_engine.RELEASE_KINDS[kind][2]
    releases = igpu_engine.list_releases(kind, get)
    latest = next((r["tag"] for r in releases if not r["prerelease"]), "")
    say(f"{label} releases on GitHub, newest first:")
    for n, rel in enumerate(releases, 1):
        flags = (" (latest)" if rel["tag"] == latest else "") + (" (pre-release)" if rel["prerelease"] else "")
        say(f"  {n}. {rel['tag']}  {rel['published'][:10]}{flags}")
    for _attempt in range(5):
        try:
            answer = ask(f"{label} version, a number or a tag [Enter = latest]: ").strip()
        except EOFError:
            return igpu_engine.LATEST
        tag = ""
        if not answer:
            return igpu_engine.LATEST
        if answer.isdigit() and 1 <= int(answer) <= len(releases):
            tag = releases[int(answer) - 1]["tag"]
        elif any(answer in (r["tag"], r["version"]) for r in releases):
            tag = next(r["tag"] for r in releases if answer in (r["tag"], r["version"]))
        if tag:
            return igpu_engine.LATEST if tag == latest else tag
        say(f"{answer!r} is not in the list.")
    raise igpu_engine.ReleaseError(
        f"No {label} release was picked. Pass --wex-version / --graphics-version, or press Enter for the latest.")


def _import_disk(pve: ProxmoxAdapter, vmid: int, path: Path, storage: str) -> str:
    """Import a raw image as a disk of the VM and return its volume id."""
    sub = ["disk", "import"] if pve.qm("disk", "import", "--help").ok else ["importdisk"]
    res = pve.qm(*sub, str(vmid), str(path), storage, "--format", "raw")
    m = re.search(r"successfully imported disk '([^']+)'", res.output)
    if not res.ok or not m:
        raise FinishError(f"Could not import {path.name}: {res.output.strip()[-300:]}")
    return m.group(1)


def _wait_first_boot(pve: ProxmoxAdapter, vmid: int, timeout: float, tahoe: bool, payload_ref: str,
                     clock: Callable[[], float], sleep: Callable[[float], None]) -> bool:
    """Wait for the first iGPU boot to end. The guest powers itself off, except Tahoe, which hangs while it
    shuts down: there the result the guest left on the payload disk ends the wait (its LaunchDaemon unmounts the
    disk before it asks for the shutdown). Returns True when the VM stopped by itself."""
    deadline = clock() + timeout
    last_look = clock()
    while clock() < deadline:
        if "stopped" in pve.qm("status", str(vmid)).output:
            return True
        if tahoe and clock() - last_look >= RESULT_POLL:
            last_look = clock()
            if _read_payload_result(pve, payload_ref).startswith(KNOWN_RESULTS):
                sleep(RESULT_GRACE)
                return False
        sleep(POLL)
    return False


OPS_RE = re.compile(r"^\s*(?:rd|wr)_operations:\s*(\d+)\s*$", re.M)


def disk_ops(pve: ProxmoxAdapter, vmid: int) -> int | None:
    """Disk reads plus writes of every disk of the running Mac (`qm status --verbose`), None when it gives none. A
    network counter would tell nothing: a hung guest kernel still answers ARP."""
    res = pve.qm("status", str(vmid), "--verbose")
    found = OPS_RE.findall(res.output) if res.ok else []
    return sum(int(n) for n in found) if found else None


@dataclass
class FirstBoot:
    stopped: bool = False       # the Mac powered itself off (the package script's last step)
    waited: float = 0.0         # seconds the host waited
    idle: float = 0.0           # seconds the Mac's disks did nothing at the end
    stuck: bool = False         # the wait ended early: no result and no disk I/O for `stuck_after` seconds


def watch_first_boot(pve: ProxmoxAdapter, vmid: int, timeout: float, tahoe: bool, payload_ref: str,
                     clock: Callable[[], float], sleep: Callable[[float], None],
                     stuck_after: float = driver_engine.FIRST_BOOT_STUCK) -> FirstBoot:
    """Like _wait_first_boot, and it also watches whether the Mac does anything: a guest that never reaches the first
    boot script (macOS panicked at startup) does no disk I/O, and the host should not wait the whole timeout for it. The
    wait ends early, with `stuck` set, after *stuck_after* seconds without I/O and without a result on the payload disk."""
    start = clock()
    deadline = start + timeout
    last_look = start
    ops, moved = disk_ops(pve, vmid), start
    out = FirstBoot()
    while clock() < deadline:
        if "stopped" in pve.qm("status", str(vmid)).output:
            out.stopped = True
            break
        now = clock()
        if tahoe and now - last_look >= RESULT_POLL:
            last_look = now
            if _read_payload_result(pve, payload_ref).startswith(KNOWN_RESULTS):
                sleep(RESULT_GRACE)
                break
        now_ops = disk_ops(pve, vmid)
        if now_ops != ops:
            ops, moved = now_ops, now
        elif now_ops is not None and now - moved >= stuck_after and not _read_payload_result(pve, payload_ref).startswith(KNOWN_RESULTS):
            out.stuck = True
            break
        sleep(POLL)
    out.waited = clock() - start
    out.idle = clock() - moved
    return out


def _check_vm(vmid: int, pve: ProxmoxAdapter) -> dict[str, str]:
    info, cfg = vm_config(vmid, pve)
    if "isa-applesmc" not in cfg.get("args", ""):
        raise FinishError(f"VM {vmid} is not a macOS VM made by this tool (no isa-applesmc in its args).")
    if cfg.get("machine", "").split(",")[0] != "pc":
        raise FinishError(
            f"VM {vmid} uses the {cfg.get('machine') or 'default'} machine type. The iGPU needs i440fx (pc), and "
            "converting an installed VM has not been tested. Create a new VM with: osx-next-cli igpu-install")
    if igpu_engine.recovery_bus(cfg):
        raise FinishError(f"VM {vmid} still has a recovery disk attached, so the install is not finished. "
                          "Finish it and run post-install first.")
    if "hostpci0" in cfg:
        raise FinishError(f"VM {vmid} already has a PCI device on hostpci0. Run gpu-setup --undo first, then try again.")
    require_stopped(vmid, info)
    return cfg


def _macos_of(macos: str, cfg: dict[str, str]) -> str:
    """The macOS release the VM runs (given, else read from its config). FinishError when unknown or not supported."""
    version = (macos or detect_macos(cfg) or "").lower()
    if not version:
        raise FinishError("Cannot tell which macOS this VM runs. Pass --macos (ventura, sonoma, sequoia or tahoe).")
    if version in UNSUPPORTED_MACOS:
        raise FinishError(UNSUPPORTED_NOTE)
    return version


def _fetch_assets(iso: Path, wex: str, graphics: str, on_event: Callable[[str], None]):
    """Check the host's iGPU and download WhateverXe, the graphics package, AMFIPass and the ROM."""
    report = check_intel_gpu(wex=wex)
    if not report.ok:
        raise FinishError("The iGPU is not ready: " + " ".join(report.issues))
    on_event("Looking up the WhateverXe releases on GitHub")
    try:
        assets = ensure_igpu_assets(iso, report.engine["info"]["rom"], wex=wex, graphics=graphics)
    except (igpu_engine.ReleaseError, DownloadError, OSError) as exc:
        raise FinishError(str(exc)) from exc
    picks = assets.picks
    on_event("Using WhateverXe %s and WhateverXe Graphics %s" % (picks["whateverxe"]["version"], picks["graphics"]["version"]))
    return assets


def _attach_plan(vmid: int, assets, pve: ProxmoxAdapter, wex: str):
    """The plan that attaches the iGPU. FinishError when the VM or host cannot take it."""
    plan = prepare_attach_igpu(vmid, assets["whateverxe"], assets["amfipass"], assets["rom"], adapter=pve,
                               wex_sha=assets.picks["whateverxe"]["sha256"], amfi_sha=igpu_engine.PINS["amfipass"][2],
                               wex=wex)
    if not plan.ok:
        raise FinishError("Cannot attach the iGPU: " + " ".join(plan.issues))
    return plan


def _build_payload(vmid: int, assets, work: Path, on_event: Callable[[str], None]) -> Path:
    payload = work / f"igpu-payload-{vmid}.img"
    on_event("Building the payload disk (graphics package, first-boot script)")
    ok, msg = igpu_engine.build_payload(str(assets["graphics"]), str(payload))
    if not ok:
        raise FinishError(msg)
    return payload


def _attach_igpu(vmid: int, assets, wex: str, state: dict, result: FinishResult, pve: ProxmoxAdapter,
                 on_event: Callable[[str], None]) -> None:
    """Attach the iGPU (OpenCore with WhateverXe, hostpci0, args), set the boot order and install the watchdog."""
    on_event("Attaching the iGPU")
    plan = _attach_plan(vmid, assets, pve, wex)
    applied = apply_plan(plan.steps, execute=True, adapter=pve)
    if not applied.ok:
        failed = next((r for r in applied.results if not r.ok), None)
        raise FinishError("Attaching the iGPU failed" + (f" at: {failed.title}" if failed else "")
                          + f". Log: {applied.log_path}. Undo with: gpu-setup --undo")
    pve.qm("set", str(vmid), "--boot", POST_INSTALL_BOOT_ORDER)
    _record_state(vmid, dict(state, package_installed=False), result, pve)
    _enable_watchdog(pve, vmid, result, on_event)


def _first_boot(vmid: int, pve: ProxmoxAdapter, payload_ref: str, tahoe: bool, result: FinishResult,
                on_event: Callable[[str], None], clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
    """Boot with the iGPU, wait for the guest to install the package and power off, and read what it reported."""
    on_event("First boot with the iGPU: the guest installs the graphics package and powers off")
    start_vm(pve, vmid)
    if not _wait_first_boot(pve, vmid, FIRSTBOOT_TIMEOUT, tahoe, payload_ref, clock, sleep):
        pve.qm("stop", str(vmid))
        if tahoe:
            result.notes.append(TAHOE_NOTE)
        else:
            result.notes.append(f"The guest did not power off within {FIRSTBOOT_TIMEOUT // 60} minutes, so it was stopped.")
        wait_stopped(pve, vmid, 120, clock, sleep)
    result.result = _read_payload_result(pve, payload_ref)
    result.installed = result.result == "installed"


def _restart(vmid: int, pve: ProxmoxAdapter, payload: Path, result: FinishResult,
             on_event: Callable[[str], None], clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
    """Record the outcome, drop the payload disk, start the VM again and, when the package is in, check the iGPU works."""
    _record_state(vmid, {"package_installed": result.installed, "package_result": result.result}, result, pve)
    _drop_disk(pve, vmid, "sata2")
    payload.unlink(missing_ok=True)
    if result.installed and not apply_plan(
            [oc_step(str(vmid), "mark", "Note in OpenCore that the graphics package is installed")],
            execute=True, adapter=pve).ok:
        result.notes.append("Could not note the installed package on the OpenCore disk: gpu-setup --undo would "
                            "remove WhateverXe, which the installed package needs.")
    pve.qm("start", str(vmid))
    on_event("Started the VM again" if result.installed else
             "Started the VM again; the graphics package is not installed (see the result above)")
    if result.installed:
        on_event(f"Checking from the host that macOS uses the iGPU (up to {igpu_engine.ACCEL_TIMEOUT // 60} minutes)")
        result.accelerated, result.accel_detail = igpu_engine.check_accel(clock=clock, sleep=sleep)


def finish(
    vmid: int,
    console: QmConsole | None = None,
    pve: ProxmoxAdapter | None = None,
    iso_dir: Path | None = None,
    on_event: Callable[[str], None] = lambda _m: None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    macos: str = "",
    work_dir: Path | None = None,
    wex: str = igpu_engine.LATEST,
    graphics: str = igpu_engine.LATEST,
) -> FinishResult:
    """Run the whole finish for *vmid* (stopped, installed, i440fx). Raises FinishError when it cannot go on."""
    pve = pve or get_proxmox_adapter()
    console = console or QmConsole(vmid)
    iso = iso_dir or Path(DEFAULT_ISO_DIR)
    work = work_dir or iso
    cfg = _check_vm(vmid, pve)
    version = _macos_of(macos, cfg)

    assets = _fetch_assets(iso, wex, graphics, on_event)
    picks = assets.picks
    # the same preflight the attach will run, so nothing is staged for a VM that could not take the card
    plan = _attach_plan(vmid, assets, pve, wex)
    result = FinishResult(notes=list(plan.warnings))
    state = igpu_engine.picks_state(picks, version)
    result.versions = igpu_engine.describe_state(state)
    storage = _storage_of(cfg)
    save_snapshot(vmid, pve, on_event)

    payload = _build_payload(vmid, assets, work, on_event)
    recovery = recovery_image(version, iso, on_event)
    job = StageJob(vmid, pve, console, storage, on_event, clock, sleep, version == "tahoe",
                   tuple(igpu_engine.vm_settings(0, "")["args_add"]))
    payload_ref = stage_recovery(job, payload, recovery, cfg)
    note_picker_restore(console, result.notes)

    _attach_igpu(vmid, assets, wex, state, result, pve, on_event)
    _first_boot(vmid, pve, payload_ref, version == "tahoe", result, on_event, clock, sleep)
    _restart(vmid, pve, payload, result, on_event, clock, sleep)
    return result


def _record_state(vmid: int, update: dict, result: FinishResult, pve: ProxmoxAdapter | None = None) -> None:
    """Remember what was installed in this VM (igpu-report and the status output read it)."""
    try:
        data = igpu_engine.write_state(vmid, update)
        record = driver_engine.from_igpu_state(vmid, data)
        if record:
            fingerprint = driver_disks.fingerprint(pve, parse_config(pve.qm("config", str(vmid)).output)) if pve else ""
            driver_engine.write_record(vmid, "intel", record["components"], fingerprint=fingerprint)
    except OSError as exc:
        result.notes.append(f"Could not record the installed versions ({exc}); igpu-report will not list them.")


def _enable_watchdog(pve: ProxmoxAdapter, vmid: int, result: FinishResult, on_event: Callable[[str], None]) -> None:
    """The host-side watchdog: records crashes on every macOS version and finishes the Tahoe restarts and shut downs."""
    cfg = parse_config(pve.qm("config", str(vmid)).output)
    applied = apply_plan(igpu_watchdog.enable_steps(vmid, cfg), execute=True, adapter=pve)
    if applied.ok:
        result.watchdog = True
        on_event("Installed the watchdog: it records crashes and finishes the restarts and shut downs that hang on macOS Tahoe")
    else:
        result.notes.append(f"Could not install the watchdog (log: {applied.log_path}), so crashes are not recorded and "
                            f"restarts and shut downs from inside macOS Tahoe hang. Install it with: "
                            f"osx-next-cli igpu-watchdog {vmid}")


def _put_back(pve: ProxmoxAdapter, vmid: int, original: dict[str, str | None]) -> None:
    """After a failed staging: the settings that staging changed go back to what the VM had."""
    for key, value in original.items():
        if value is None:
            pve.qm("set", str(vmid), "--delete", key)
        else:
            pve.qm("set", str(vmid), f"--{key}", value)


@dataclass
class StageJob:
    """What the recovery staging of one Mac needs: the Mac, how to reach Proxmox and its console, and the pacing."""
    vmid: int
    pve: ProxmoxAdapter
    console: QmConsole
    storage: str
    on_event: Callable[[str], None]
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    tahoe: bool = False
    leftover_args: tuple[str, ...] = ()   # -set device.hostpci0 args of an earlier attach: QEMU stops without the device
    on_disk: Callable[[str], None] = lambda _ref: None    # told the volume id of each disk the staging creates


def stage_recovery(job: StageJob, payload: Path, recovery: Path, cfg: dict[str, str]) -> str:
    """Boot macOS recovery with the emulated display, run the staging script from *payload* and wait for the guest
    to power off. Both disks are imported and attached (sata2 payload, sata1 recovery); afterwards the recovery
    disk is gone again and the picker shows every volume. Returns the payload disk's volume id; on a failure the
    payload disk is dropped, the image deleted and the settings staging changed put back before the error goes on."""
    pve, vmid = job.pve, job.vmid
    original = {"vga": cfg.get("vga"), "args": cfg.get("args"), "boot": cfg.get("boot")}
    payload_ref = ""
    try:
        payload_ref = _import_disk(pve, vmid, payload, job.storage)
        job.on_disk(payload_ref)
        recovery_ref = _import_disk(pve, vmid, recovery, job.storage)
        job.on_disk(recovery_ref)
        for argv in (["--sata1", f"{recovery_ref},media=disk"], ["--sata2", f"{payload_ref},media=disk"],
                     ["--boot", "order=sata1;virtio0;ide0"]):
            res = pve.qm("set", str(vmid), *argv)
            if not res.ok:
                raise FinishError(f"qm set {argv[0]} failed: {res.output.strip()[-300:]}")
        if cfg.get("vga") not in (None, "std"):
            pve.qm("set", str(vmid), "--vga", "std")
        if job.leftover_args:
            leftover = igpu_engine.strip_args(cfg.get("args", ""), list(job.leftover_args))
            if leftover != cfg.get("args", ""):
                pve.qm("set", str(vmid), "--args", leftover)
        _stage(vmid, pve, job.console, payload_ref, job.on_event, job.clock, job.sleep, job.tahoe)
    except BaseException:
        _drop_disk(pve, vmid, "sata2")
        payload.unlink(missing_ok=True)
        _put_back(pve, vmid, original)
        job.console.restore_picker_timeout()
        raise
    finally:
        _drop_disk(pve, vmid, "sata1")
        # best effort: the picker must show every volume again whatever happened above
        apply_plan([oc_step(str(vmid), "unstage", "Show every volume in the OpenCore picker again")],
                   execute=True, adapter=pve)
    return payload_ref


def _read_payload_result(pve: ProxmoxAdapter, ref: str) -> str:
    path = pve.volume_path(ref)
    return igpu_engine.read_result(path) if path else ""


def _staging_started(pve: ProxmoxAdapter, vmid: int, ref: str, wait: float,
                     clock: Callable[[], float], sleep: Callable[[float], None]) -> bool:
    """True once stage.sh left any result on the payload disk (it writes "started" first) or the VM halted."""
    deadline = clock() + wait
    while True:
        if "stopped" in pve.qm("status", str(vmid)).output or _read_payload_result(pve, ref):
            return True
        if clock() >= deadline:
            return False
        sleep(START_POLL)


def _wait_staged(pve: ProxmoxAdapter, vmid: int, ref: str, timeout: float,
                 clock: Callable[[], float], sleep: Callable[[float], None]) -> bool:
    """Wait until the staging script is finished: the VM halted, or the script left its result on the payload disk.

    stage.sh syncs before it writes "staged" (or "failed: ..."), then halts. On Tahoe that halt can wedge the
    recovery (every vCPU spins at 100% and the VM never stops), so the result ends the wait; the VM gets
    STAGE_GRACE seconds to halt by itself first. False when neither happened within *timeout*."""
    deadline = clock() + timeout
    while clock() < deadline:
        if "stopped" in pve.qm("status", str(vmid)).output:
            return True
        if _read_payload_result(pve, ref).startswith(STAGE_DONE):
            wait_stopped(pve, vmid, STAGE_GRACE, clock, sleep)
            return True
        sleep(START_POLL)
    return False


def _stage(vmid: int, pve: ProxmoxAdapter, console: QmConsole, payload_ref: str,
           on_event: Callable[[str], None], clock: Callable[[], float], sleep: Callable[[float], None],
           tahoe: bool = False) -> None:
    """Boot recovery, run the staging script from the payload disk and wait for the guest to power off."""
    step = oc_step(str(vmid), "stage", "Make the OpenCore picker show recovery only and wait for a key")
    if not apply_plan([step], execute=True, adapter=pve).ok:
        raise FinishError("Could not patch the OpenCore disk (picker).")
    start_vm(pve, vmid)
    try:
        open_recovery_terminal(console, on_event, clock, sleep)
    except UnattendedError as exc:
        raise FinishError(str(exc)) from exc
    on_event("Staging the graphics package inside macOS (offline)")
    for attempt in range(1, STAGE_ATTEMPTS + 1):
        began = clock()
        console.type_text(igpu_engine.STAGE_COMMAND)
        typing = clock() - began  # the Terminal consumes keys at this pace, so the shell needs about as long again
        sleep(1)
        console.sendkey("ret")
        if _staging_started(pve, vmid, payload_ref, START_WAIT + typing, clock, sleep):
            break
        # keep what the Terminal showed: a garbled or failed command is read from this, not guessed
        shot = console.save_frame(f"/tmp/osx-next-igpu-stage-{vmid}-try{attempt}.png") if hasattr(console, "save_frame") else ""
        if attempt < STAGE_ATTEMPTS:
            on_event("The staging command did not start (a key was probably dropped); typing it again")
            console.sendkey("ctrl-c")
            sleep(1)
    else:
        pve.qm("stop", str(vmid))
        wait_stopped(pve, vmid, 120, clock, sleep)
        raise FinishError(f"The staging command did not start after {STAGE_ATTEMPTS} tries."
                          + (f" The screen at the last try is in {shot}." if shot else "")
                          + (" " + TAHOE_STAGE_NOTE if tahoe else ""))
    if not _wait_staged(pve, vmid, payload_ref, STAGE_TIMEOUT, clock, sleep):
        pve.qm("stop", str(vmid))
        wait_stopped(pve, vmid, 120, clock, sleep)
        raise FinishError(f"The staging script did not finish within {STAGE_TIMEOUT // 60} minutes."
                          + (" " + TAHOE_STAGE_NOTE if tahoe else ""))
    if "stopped" not in pve.qm("status", str(vmid)).output:
        # the script is done but its halt wedged (Tahoe recovery: every vCPU spins in the firmware's shutdown call)
        pve.qm("stop", str(vmid))
        wait_stopped(pve, vmid, 120, clock, sleep)
    staged = _read_payload_result(pve, payload_ref)
    if staged != "staged":
        raise FinishError("Staging failed: " + (staged or "the script left no result") +
                          ". Is the macOS disk encrypted (FileVault)? Staging needs the data volume unlocked.")
