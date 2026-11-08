"""Attach an AMD GPU to an installed macOS VM, and undo any GPU attach exactly.

NVIDIA goes through nullmoth.py (it needs the third-party driver and its notice).
AMD needs no driver: the card, the PCIe placement, the QEMU hotplug flag, the
display choice and the WhateverGreen boot-args for the family.
"""

from __future__ import annotations

from pathlib import Path
from shlex import quote as shquote

from . import amd_host, igpu_attach, nullmoth
from .domain import PlanStep
from .gpu import (
    AttachRecord, GpuReport, build_hostpci_attach_steps, build_hostpci_detach_steps, gpu_users, hostpci_bdfs, running_user_issue, shared_note,
    hostpci_value, load_record, new_record, parse_config, record_is_stale, record_step, stop_step,
    verify_stopped_step,
)
from .infrastructure import ProxmoxAdapter
from .nullmoth import AttachPlan, detect_macos
from .planner import mount_oc_disk_script
from .services import fetch_vm_info, get_proxmox_adapter

__all__ = ["AMD_NEXT_STEPS", "prepare_attach_amd", "prepare_detach", "oc_boot_args_script"]

_PYEOF = "GPU_OC_PY"
AMD_NEXT_STEPS = (
    "Plug a monitor into the card (HDMI first: DisplayPort outputs have stayed black on several cards) and start the VM.",
    "The OpenCore picker and the macOS desktop appear on that monitor.",
    "If the screen stays black, run gpu-setup again: it walks through the usual causes.",
)


def _patch_source() -> str:
    from importlib import resources
    return (resources.files("osx_proxmox_next") / "data/gpu/oc_boot_args.py").read_text()


def oc_boot_args_script(vid: str, action: str, boot_args: tuple[str, ...] = ()) -> str:
    """Bash that mounts the VM's OpenCore disk and runs the shared boot-args patcher."""
    source = _patch_source()
    assert _PYEOF not in source
    env = f"GPU_BOOT_ARGS={shquote(' '.join(boot_args))} " if boot_args else ""
    return (
        mount_oc_disk_script(vid)
        + f"{env}python3 - \"$OC_MNT/EFI/OC\" {action} <<'{_PYEOF}' && sync\n"
        + source
        + ("" if source.endswith("\n") else "\n")
        + f"{_PYEOF}\n"
    )


def _oc_step(vid: str, action: str, boot_args: tuple[str, ...] = ()) -> PlanStep:
    return PlanStep(
        title="Add the display boot-args to OpenCore (backup first)" if action == "attach"
        else "Restore the previous OpenCore config",
        argv=["bash", "-c", oc_boot_args_script(vid, action, boot_args)],
        risk="action",
    )


def build_attach_steps(
    vmid: int, bdf: str, current_args: str, device_id: int, vga_none: bool, record: AttachRecord,
    stop_first: bool = False, replace_record: bool = False,
) -> list[PlanStep]:
    vid = str(vmid)
    steps = [stop_step(vid)] if stop_first else []
    steps.append(verify_stopped_step(vid))
    boot_args = amd_host.boot_args_for(device_id)
    if boot_args:
        record.oc = "gpu"
    # The record goes first: it only writes a sidecar file, and a failure later on still finds it.
    steps.append(record_step(record, replace=replace_record))
    if boot_args:
        steps.append(_oc_step(vid, "attach", boot_args))
    steps += build_hostpci_attach_steps(
        vmid, bdf, current_args, amd_host.AMD_PROFILE, vga_none=vga_none,
        extra_hostpci=amd_host.hostpci_extra(device_id),
    )
    return steps


def build_detach_steps(
    vmid: int, current_args: str, has_hostpci0: bool, record: AttachRecord, stop_first: bool = False,
    cfg: dict[str, str] | None = None, notes: list[str] | None = None,
) -> list[PlanStep]:
    vid = str(vmid)
    steps = [stop_step(vid)] if stop_first else []
    steps.append(verify_stopped_step(vid))
    if record.oc == "gpu":
        steps.append(_oc_step(vid, "restore"))
    elif record.oc == "igpu":
        steps.append(igpu_attach.oc_step(vid, "restore", "Restore the previous OpenCore config"))
    steps += build_hostpci_detach_steps(vmid, current_args, has_hostpci0, record=record,
                                        profile=amd_host.AMD_PROFILE, cfg=cfg, notes=notes)
    return steps


def _vm_issues(vmid: int, cfg: dict[str, str], running: bool, allow_stop: bool) -> list[str]:
    issues: list[str] = []
    if "isa-applesmc" not in cfg.get("args", ""):
        issues.append(f"VM {vmid} is not a macOS VM made by this tool (no isa-applesmc in its args).")
    if "ide2" in cfg:
        issues.append(
            f"VM {vmid} still has the recovery disk attached, so the install is not finished. Finish it, "
            "run post-install, then attach the card.")
    if running and not allow_stop:
        issues.append(f"VM {vmid} is running. Stop it first: qm stop {vmid}")
    return issues


def prepare_attach_amd(
    vmid: int,
    gpu: str,
    display: str = "gpu",
    allow_stop: bool = False,
    adapter: ProxmoxAdapter | None = None,
    sysfs_root: Path = Path("/sys"),
    cmdline_path: Path | None = None,
) -> AttachPlan:
    """Preflight an AMD attach. *display* is "gpu" (monitor on the card, vga none) or "console"."""
    plan = AttachPlan(vmid)
    pve = adapter or get_proxmox_adapter()
    info = fetch_vm_info(vmid, adapter=pve)
    if info is None:
        plan.issues.append(f"VM {vmid} not found.")
        return plan
    cfg = parse_config(info.config_raw)
    running = info.status == "running"
    plan.issues += _vm_issues(vmid, cfg, running, allow_stop)
    version = detect_macos(cfg)
    if version is None:
        plan.warnings.append("Cannot tell which macOS this VM runs. AMD cards work on Ventura to Tahoe.")

    report: GpuReport = amd_host.check_amd_gpu(gpu, sysfs_root, cmdline_path)
    plan.issues += report.issues
    plan.warnings += report.warnings
    plan.hints += report.hints
    existing = hostpci_bdfs(cfg).get("hostpci0")
    if cfg.get("hostpci0") is not None and existing != {report.bdf}:
        plan.issues.append(f"hostpci0 is already in use on this VM ({cfg['hostpci0']}). Remove it first or run gpu-setup --undo.")
    for other, other_running in gpu_users(pve, report.bdf, vmid) if report.ok else []:
        if other_running:
            plan.issues.append(running_user_issue(other))
        else:
            plan.warnings.append(shared_note(other))

    if plan.ok:
        dev_path = sysfs_root / "bus/pci/devices" / f"{report.bdf}.0" / "device"
        device_id = int(dev_path.read_text().strip(), 16)
        extra = amd_host.hostpci_extra(device_id)
        record = new_record(vmid, report.bdf, "amd", cfg, amd_host.AMD_PROFILE,
                            hostpci_value(report.bdf, amd_host.AMD_PROFILE, extra), vga_none=display == "gpu")
        plan.steps = build_attach_steps(
            vmid, report.bdf, cfg.get("args", ""), device_id, display == "gpu", record,
            stop_first=running and allow_stop, replace_record=record_is_stale(record),
        )
    return plan


def prepare_detach(
    vmid: int, adapter: ProxmoxAdapter | None = None, allow_stop: bool = False, missing_ok: bool = False,
) -> AttachPlan:
    """Undo a GPU attach for either vendor (nvidia-detach and gpu-setup --undo share this)."""
    record = load_record(vmid)
    if record is None or record.vendor not in ("amd", "intel"):
        return nullmoth.prepare_detach(vmid, adapter=adapter, allow_stop=allow_stop, missing_ok=missing_ok)
    plan = AttachPlan(vmid)
    info = fetch_vm_info(vmid, adapter=adapter or get_proxmox_adapter())
    if info is None:
        if missing_ok:
            plan.warnings.append(f"VM {vmid} no longer exists, so there is nothing to revert on it.")
        else:
            plan.issues.append(f"VM {vmid} not found.")
        return plan
    cfg = parse_config(info.config_raw)
    stale = nullmoth.record_issue(vmid, info, cfg)
    if stale:
        plan.issues.append(stale)
        return plan
    running = info.status == "running"
    if running and not allow_stop:
        plan.issues.append(f"VM {vmid} is running. Stop it first: qm stop {vmid}")
    if plan.ok:
        plan.steps = build_detach_steps(vmid, cfg.get("args", ""), "hostpci0" in cfg, record,
                                        stop_first=running and allow_stop, cfg=cfg, notes=plan.warnings)
    return plan
