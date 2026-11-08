"""Attach the Intel iGPU to a macOS VM (beta) and undo it.

The VM becomes an i440fx machine (``machine: pc``) with the iGPU on hostpci0 in legacy IGD mode,
no emulated display, and QEMU args sized from the host's graphics memory layout. OpenCore gets
WhateverXe, AMFIPass and the iGPU boot-args through data/gpu/oc_igpu.py. The attach is recorded
like every other GPU attach, so gpu-setup --undo puts the VM back field by field.

Everything decided here comes from igpu_engine.py, which the bash installer embeds verbatim.
"""

from __future__ import annotations

from pathlib import Path
from shlex import quote as shquote

from . import igpu_engine
from .domain import PlanStep
from .gpu import (
    AttachRecord, GpuProfile, gpu_users, hostpci_bdfs, new_record, parse_config, record_is_stale, record_step,
    running_user_issue, shared_note, stop_step, verify_stopped_step,
)
from .infrastructure import ProxmoxAdapter
from .intel_host import check_intel_gpu
from .nullmoth import AttachPlan, detect_macos
from .planner import mount_oc_disk_script
from .services import fetch_vm_info, get_proxmox_adapter

__all__ = [
    "IGPU_NEXT_STEPS",
    "TAHOE_NOTE",
    "build_attach_steps",
    "build_fresh_vm_steps",
    "oc_igpu_script",
    "prepare_attach_igpu",
]

_PYEOF = "IGPU_OC_PY"
TAHOE_NOTE = ("macOS Tahoe 26 is experimental with the iGPU: every restart and shutdown from inside macOS hangs "
              "(WhateverXe issue 62). Restart the VM from Proxmox (qm stop, then qm start) instead.")
IGPU_NEXT_STEPS = (
    "Plug a monitor, or an HDMI dummy plug, into the host's native HDMI port (not an LSPCON HDMI or USB-C port) "
    "and start the VM. The OpenCore picker and the macOS desktop appear on that screen.",
    "The Proxmox console stays blank: the iGPU is the only display.",
    "The first boot installs the graphics package by itself and restarts once. Metal is active afterwards.",
)


def _oc_source() -> str:
    from importlib import resources
    return (resources.files("osx_proxmox_next") / "data/gpu/oc_igpu.py").read_text()


def oc_igpu_script(vid: str, action: str, wex_zip: Path | None = None, amfi_zip: Path | None = None) -> str:
    """Bash that mounts the VM's OpenCore disk and runs the iGPU patcher (attach, restore, fadt, stage, unstage)."""
    source = _oc_source()
    assert _PYEOF not in source
    env = ""
    if wex_zip and amfi_zip:
        wex, amfi = igpu_engine.PINS["whateverxe"], igpu_engine.PINS["amfipass"]
        env = (f"IGPU_WEX_ZIP={shquote(str(wex_zip))} IGPU_WEX_SHA256={wex[2]} "
               f"IGPU_AMFI_ZIP={shquote(str(amfi_zip))} IGPU_AMFI_SHA256={amfi[2]} ")
    return (
        mount_oc_disk_script(vid)
        + f"{env}python3 - \"$OC_MNT/EFI/OC\" {action} <<'{_PYEOF}' && sync\n"
        + source
        + ("" if source.endswith("\n") else "\n")
        + f"{_PYEOF}\n"
    )


def oc_step(vid: str, action: str, title: str, wex_zip: Path | None = None, amfi_zip: Path | None = None) -> PlanStep:
    return PlanStep(title=title, argv=["bash", "-c", oc_igpu_script(vid, action, wex_zip, amfi_zip)], risk="action")


def profile_for(args_add: list[str]) -> GpuProfile:
    return GpuProfile(vendor="intel", pcie=0, global_args=tuple(args_add), sidecar="igpu")


def rom_step(rom_path: Path, rom_file: str) -> PlanStep:
    return PlanStep(
        title=f"Put the option ROM in {igpu_engine.KVM_DIR}",
        argv=["install", "-m", "644", str(rom_path), f"{igpu_engine.KVM_DIR}/{rom_file}"],
    )


def build_fresh_vm_steps(vmid: int, macos_label: str = "") -> list[PlanStep]:
    """Turn a VM that was just created (q35, recovery on ide2, not started) into the i440fx shape the iGPU needs.

    PIIX IDE has no macOS driver, so the recovery disk moves to sata1; OpenCore gets FadtEnableReset, without
    which every guest restart hangs on i440fx."""
    vid = str(vmid)
    q = shquote(vid)
    describe = ([PlanStep(title="Note the macOS release in the VM description",
                          argv=["qm", "set", vid, "--description", f"{macos_label} VM"])] if macos_label else [])
    return [
        *describe,
        PlanStep(title="Use the i440fx machine type", argv=["qm", "set", vid, "--machine", "pc"]),
        PlanStep(
            title="Move the recovery disk to sata1 (i440fx has no macOS driver for IDE)",
            argv=["bash", "-c",
                  f"REC=$(qm config {q} | sed -n 's/^ide2: \\([^,]*\\),.*/\\1/p'); "
                  f"if [ -n \"$REC\" ]; then qm set {q} --delete ide2 && qm set {q} --sata1 \"$REC,media=disk\"; fi && "
                  f"qm set {q} --boot 'order=sata1;virtio0;ide0'"],
        ),
        oc_step(vid, "fadt", "Set FadtEnableReset in OpenCore (restarts hang without it)"),
    ]


def build_attach_steps(
    vmid: int, bdf: str, current_args: str, settings: dict, record: AttachRecord, rom_path: Path,
    wex_zip: Path, amfi_zip: Path, stop_first: bool = False, replace_record: bool = False,
) -> list[PlanStep]:
    """Record first (a sidecar file only), then OpenCore (the step that can refuse), then the VM."""
    vid = str(vmid)
    steps = [stop_step(vid)] if stop_first else []
    steps.append(verify_stopped_step(vid))
    steps.append(record_step(record, replace=replace_record))
    steps.append(oc_step(vid, "attach", "Add WhateverXe, AMFIPass and the iGPU boot-args to OpenCore (backup first)",
                         wex_zip, amfi_zip))
    steps.append(rom_step(rom_path, settings["hostpci0"].rsplit("romfile=", 1)[1]))
    steps.append(PlanStep(title="Attach the iGPU (hostpci0, legacy IGD mode)",
                          argv=["qm", "set", vid, "--hostpci0", settings["hostpci0"]], risk="action"))
    new_args = igpu_engine.merge_args(current_args, settings["args_add"])
    steps.append(PlanStep(title="Size the guest's low RAM for the iGPU memory layout",
                          argv=["qm", "set", vid, "--args", new_args]))
    steps.append(PlanStep(title="Turn the emulated display off (vga none)",
                          argv=["qm", "set", vid, "--vga", "none"], risk="warn"))
    return steps


def vm_issues(vmid: int, cfg: dict[str, str], running: bool, allow_stop: bool) -> list[str]:
    issues: list[str] = []
    if "isa-applesmc" not in cfg.get("args", ""):
        issues.append(f"VM {vmid} is not a macOS VM made by this tool (no isa-applesmc in its args).")
    if igpu_engine.recovery_bus(cfg):
        issues.append(f"VM {vmid} still has the recovery disk attached, so the install is not finished. Finish it, "
                      "run post-install, then attach the iGPU.")
    if cfg.get("machine", "").split(",")[0] != "pc":
        issues.append(
            f"VM {vmid} uses the {cfg.get('machine') or 'default'} machine type. The iGPU needs i440fx (pc), and "
            "converting an installed VM has not been tested. Create a new VM with: osx-next-cli igpu-install")
    if running and not allow_stop:
        issues.append(f"VM {vmid} is running. Stop it first: qm stop {vmid}")
    return issues


def prepare_attach_igpu(
    vmid: int,
    wex_zip: Path,
    amfi_zip: Path,
    rom_path: Path,
    allow_stop: bool = False,
    adapter: ProxmoxAdapter | None = None,
    root: Path = Path("/"),
    run=igpu_engine.default_run,
) -> AttachPlan:
    """Preflight an iGPU attach and, when it passes, build the plan."""
    plan = AttachPlan(vmid)
    pve = adapter or get_proxmox_adapter()
    info = fetch_vm_info(vmid, adapter=pve)
    if info is None:
        plan.issues.append(f"VM {vmid} not found.")
        return plan
    cfg = parse_config(info.config_raw)
    running = info.status == "running"
    plan.issues += vm_issues(vmid, cfg, running, allow_stop)
    version = detect_macos(cfg)
    if version is None:
        plan.warnings.append("Cannot tell which macOS this VM runs. The iGPU works on Ventura to Sequoia; "
                             "Tahoe is experimental.")
    elif version == "tahoe":
        plan.warnings.append(TAHOE_NOTE)

    report = check_intel_gpu(root, run)
    plan.issues += report.issues
    plan.warnings += report.warnings
    plan.hints += report.hints
    existing = hostpci_bdfs(cfg).get("hostpci0")
    if cfg.get("hostpci0") is not None and existing != {report.bdf}:
        plan.issues.append(f"hostpci0 is already in use on this VM ({cfg['hostpci0']}). "
                           "Remove it first or run gpu-setup --undo.")
    for other, other_running in gpu_users(pve, report.bdf, vmid) if report.ok else []:
        if other_running:
            plan.issues.append(running_user_issue(other))
        else:
            plan.warnings.append(shared_note(other))

    if plan.ok and report.layout:
        settings = igpu_engine.vm_settings(report.layout["max_ram_below_4g_mb"], report.rom_file)
        record = new_record(vmid, report.bdf, "intel", cfg, profile_for(settings["args_add"]),
                            settings["hostpci0"], vga_none=True, oc="igpu")
        plan.steps = build_attach_steps(
            vmid, report.bdf, cfg.get("args", ""), settings, record, rom_path, wex_zip, amfi_zip,
            stop_first=running and allow_stop, replace_record=record_is_stale(record),
        )
    return plan
