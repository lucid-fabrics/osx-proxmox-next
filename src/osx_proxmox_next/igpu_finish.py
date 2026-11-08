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
from types import SimpleNamespace

from . import igpu_engine
from .assets import resolve_recovery_or_installer_path
from .defaults import DEFAULT_ISO_DIR
from .executor import apply_plan
from .gpu import parse_config
from .igpu_attach import TAHOE_NOTE, oc_step, prepare_attach_igpu
from .infrastructure import ProxmoxAdapter
from .intel_host import check_intel_gpu
from .nullmoth import detect_macos
from .oc_builder import ensure_igpu_assets
from .planner import POST_INSTALL_BOOT_ORDER
from .rollback import create_snapshot
from .services import fetch_vm_info, get_proxmox_adapter
from .unattended import QmConsole, UnattendedError, open_recovery_terminal

__all__ = ["FinishError", "FinishResult", "finish", "wait_stopped"]

STAGE_TIMEOUT = 900          # staging copies ~95 MB and halts
FIRSTBOOT_TIMEOUT = 1500     # boot, WhateverXe wait (up to 120 s), package install, halt
RESULT_POLL = 30             # Tahoe cannot halt itself (WhateverXe issue 62): look for the guest's result instead
RESULT_GRACE = 15            # let a result that was seen mid-write finish before it is trusted
POLL = 10
KNOWN_RESULTS = ("installed", "failed:", "not installed:")


class FinishError(Exception):
    """A phase of the iGPU finish did not reach its expected state."""


@dataclass
class FinishResult:
    result: str = ""           # what the guest reported: installed, not installed: ..., failed: ...
    installed: bool = False
    notes: list[str] = field(default_factory=list)


def _storage_of(cfg: dict[str, str]) -> str:
    for key in ("virtio0", "efidisk0", "ide0"):
        m = re.match(r"([^:,]+):", cfg.get(key, ""))
        if m:
            return m.group(1)
    raise FinishError("Could not tell which storage the VM disks are on.")


def _import_disk(pve: ProxmoxAdapter, vmid: int, path: Path, storage: str) -> str:
    """Import a raw image as a disk of the VM and return its volume id."""
    sub = ["disk", "import"] if pve.qm("disk", "import", "--help").ok else ["importdisk"]
    res = pve.qm(*sub, str(vmid), str(path), storage, "--format", "raw")
    m = re.search(r"successfully imported disk '([^']+)'", res.output)
    if not res.ok or not m:
        raise FinishError(f"Could not import {path.name}: {res.output.strip()[-300:]}")
    return m.group(1)


def _drop_disk(pve: ProxmoxAdapter, vmid: int, slot: str) -> None:
    """Detach *slot* and delete the volume it held (the unused entry it leaves behind)."""
    cfg = parse_config(pve.qm("config", str(vmid)).output)
    ref = cfg.get(slot, "").split(",", 1)[0]
    if not ref:
        return
    pve.qm("set", str(vmid), "--delete", slot)
    cfg = parse_config(pve.qm("config", str(vmid)).output)
    for key, value in cfg.items():
        if re.fullmatch(r"unused\d+", key) and value.split(",", 1)[0] == ref:
            pve.qm("set", str(vmid), "--delete", key)


def wait_stopped(pve: ProxmoxAdapter, vmid: int, timeout: float, clock: Callable[[], float],
                  sleep: Callable[[float], None]) -> bool:
    deadline = clock() + timeout
    while clock() < deadline:
        if "stopped" in pve.qm("status", str(vmid)).output:
            return True
        sleep(POLL)
    return False


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


def _check_vm(vmid: int, pve: ProxmoxAdapter) -> dict[str, str]:
    info = fetch_vm_info(vmid, adapter=pve)
    if info is None:
        raise FinishError(f"VM {vmid} not found.")
    cfg = parse_config(info.config_raw)
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
    if info.status != "stopped":
        raise FinishError(f"VM {vmid} is running. Stop it first: qm stop {vmid}")
    return cfg


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
) -> FinishResult:
    """Run the whole finish for *vmid* (stopped, installed, i440fx). Raises FinishError when it cannot go on."""
    pve = pve or get_proxmox_adapter()
    console = console or QmConsole(vmid)
    iso = iso_dir or Path(DEFAULT_ISO_DIR)
    work = work_dir or iso
    cfg = _check_vm(vmid, pve)
    version = (macos or detect_macos(cfg) or "").lower()
    if not version:
        raise FinishError("Cannot tell which macOS this VM runs. Pass --macos (ventura, sonoma, sequoia or tahoe).")

    report = check_intel_gpu()
    if not report.ok:
        raise FinishError("The iGPU is not ready: " + " ".join(report.issues))
    assets = ensure_igpu_assets(iso, report.engine["info"]["rom"])
    # the same preflight the attach will run, so nothing is staged for a VM that could not take the card
    plan = prepare_attach_igpu(vmid, assets["whateverxe"], assets["amfipass"], assets["rom"], adapter=pve)
    if not plan.ok:
        raise FinishError("Cannot attach the iGPU: " + " ".join(plan.issues))
    result = FinishResult(notes=list(plan.warnings))
    storage = _storage_of(cfg)
    snapshot = create_snapshot(vmid, adapter=pve)
    on_event(f"Saved the VM config to {snapshot.path}")

    payload = work / f"igpu-payload-{vmid}.img"
    on_event("Building the payload disk (graphics package, first-boot script)")
    ok, msg = igpu_engine.build_payload(str(assets["graphics"]), str(payload))
    if not ok:
        raise FinishError(msg)
    recovery = resolve_recovery_or_installer_path(SimpleNamespace(installer_path="", macos=version))  # type: ignore[arg-type]
    if not recovery.exists():
        from .downloader import download_recovery
        on_event(f"Downloading the {version} recovery image")
        recovery = download_recovery(version, iso)
    payload_ref = recovery_ref = ""
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
        # a -set device.hostpci0 arg left over from an earlier attach would stop QEMU without the device
        leftover = igpu_engine.strip_args(cfg.get("args", ""), igpu_engine.vm_settings(0, "")["args_add"])
        if leftover != cfg.get("args", ""):
            pve.qm("set", str(vmid), "--args", leftover)
        _stage(vmid, pve, console, payload_ref, on_event, clock, sleep)
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

    on_event("Attaching the iGPU")
    plan = prepare_attach_igpu(vmid, assets["whateverxe"], assets["amfipass"], assets["rom"], adapter=pve)
    if not plan.ok:
        raise FinishError("Cannot attach the iGPU: " + " ".join(plan.issues))
    applied = apply_plan(plan.steps, execute=True, adapter=pve)
    if not applied.ok:
        failed = next((r for r in applied.results if not r.ok), None)
        raise FinishError("Attaching the iGPU failed" + (f" at: {failed.title}" if failed else "")
                          + f". Log: {applied.log_path}. Undo with: gpu-setup --undo")
    pve.qm("set", str(vmid), "--boot", POST_INSTALL_BOOT_ORDER)

    on_event("First boot with the iGPU: the guest installs the graphics package and powers off")
    tahoe = version == "tahoe"
    res = pve.qm("start", str(vmid))
    if not res.ok:
        raise FinishError(f"Could not start VM {vmid}: {res.output.strip()[-300:]}")
    if not _wait_first_boot(pve, vmid, FIRSTBOOT_TIMEOUT, tahoe, payload_ref, clock, sleep):
        pve.qm("stop", str(vmid))
        if tahoe:
            result.notes.append(TAHOE_NOTE)
        else:
            result.notes.append(f"The guest did not power off within {FIRSTBOOT_TIMEOUT // 60} minutes, so it was stopped.")
        wait_stopped(pve, vmid, 120, clock, sleep)
    result.result = _read_payload_result(pve, payload_ref)
    result.installed = result.result == "installed"
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
    return result


def _put_back(pve: ProxmoxAdapter, vmid: int, original: dict[str, str | None]) -> None:
    """After a failed staging: the settings that staging changed go back to what the VM had."""
    for key, value in original.items():
        if value is None:
            pve.qm("set", str(vmid), "--delete", key)
        else:
            pve.qm("set", str(vmid), f"--{key}", value)


def _read_payload_result(pve: ProxmoxAdapter, ref: str) -> str:
    path = pve.pvesm("path", ref).output.strip()
    return igpu_engine.read_result(path) if path else ""


def _stage(vmid: int, pve: ProxmoxAdapter, console: QmConsole, payload_ref: str,
           on_event: Callable[[str], None], clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
    """Boot recovery, run the staging script from the payload disk and wait for the guest to power off."""
    step = oc_step(str(vmid), "stage", "Make the OpenCore picker show recovery only and wait for a key")
    if not apply_plan([step], execute=True, adapter=pve).ok:
        raise FinishError("Could not patch the OpenCore disk (picker).")
    res = pve.qm("start", str(vmid))
    if not res.ok:
        raise FinishError(f"Could not start VM {vmid}: {res.output.strip()[-300:]}")
    try:
        open_recovery_terminal(console, on_event, clock, sleep)
    except UnattendedError as exc:
        raise FinishError(str(exc)) from exc
    on_event("Staging the graphics package inside macOS (offline)")
    console.type_text(igpu_engine.STAGE_COMMAND)
    sleep(1)
    console.sendkey("ret")
    if not wait_stopped(pve, vmid, STAGE_TIMEOUT, clock, sleep):
        pve.qm("stop", str(vmid))
        wait_stopped(pve, vmid, 120, clock, sleep)
        raise FinishError(f"The staging script did not finish within {STAGE_TIMEOUT // 60} minutes.")
    staged = _read_payload_result(pve, payload_ref)
    if staged != "staged":
        raise FinishError("Staging failed: " + (staged or "the script left no result") +
                          ". Is the macOS disk encrypted (FileVault)? Staging needs the data volume unlocked.")
