"""Attach an NVIDIA Turing-or-newer GPU to an installed macOS VM (beta).

Prepares the VM and its OpenCore disk for the third-party NullMoth driver.
The driver itself is never downloaded, bundled or installed by this tool.
The notice text below is used verbatim by the CLI, the TUI, the bash
installer and the docs; tests/test_nullmoth_parity.py diffs the bash copy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from shlex import quote as shquote
from urllib.parse import unquote

from .domain import PlanStep
from .infrastructure import ProxmoxAdapter
from .gpu import (
    MMIO_ARG, AttachRecord, GpuReport, build_hostpci_attach_steps, build_hostpci_detach_steps, gpu_users,
    hostpci_bdfs, load_record, normalize_bdf, parse_config, record_step,
    stop_step as _stop_step, verify_stopped_step as _verify_stopped_step, with_mmio_arg, without_mmio_arg,
)
from .nvidia_host import check_nvidia_gpu
from .planner import mount_oc_disk_script
from .services import fetch_vm_info, get_proxmox_adapter

__all__ = [
    "ACK_LABEL",
    "ACK_PHRASE",
    "AttachPlan",
    "MMIO_ARG",
    "NEXT_STEPS",
    "NOTICE_BODY",
    "NOTICE_TITLE",
    "NULLMOTH_URL",
    "build_attach_steps",
    "build_detach_steps",
    "detect_macos",
    "notice_text",
    "prepare_attach",
    "prepare_detach",
    "with_mmio_arg",
    "without_mmio_arg",
]

NULLMOTH_URL = "https://github.com/nullmoth/nvidia-macos-driver"
NOTICE_TITLE = "NVIDIA GPU support (Beta)"
NOTICE_BODY = (
    "This feature prepares your VM for the NullMoth NVIDIA driver, a third-party project that "
    "osx-proxmox-next does not make, review or support. You download and install that driver "
    "yourself, at your own risk. We are not responsible for the driver, its installation, or any "
    "damage, data loss or instability that follows.\n"
    "\n"
    "NullMoth only runs with core macOS security turned off: code-signing checks (AMFI) are "
    "bypassed, System Integrity Protection is relaxed and Secure Boot is disabled. This is a real "
    "security threat: malware has a much easier time on a VM set up this way. Only enable it on a "
    "VM you are prepared to treat as untrusted, and keep personal accounts and sensitive data off "
    "it.\n"
    "\n"
    "NullMoth is licensed PolyForm Noncommercial 1.0.0. Driver and docs: " + NULLMOTH_URL
)
ACK_LABEL = (
    "I understand the risks and that osx-proxmox-next is not responsible for the NullMoth driver."
)
ACK_PHRASE = "I accept"

_MACOS_RE = re.compile(r"\b(ventura|sonoma|sequoia|tahoe)\b", re.IGNORECASE)
_ALLOWED_MACOS = ("sequoia", "tahoe")
_PYEOF = "NULLMOTH_PY"
# The only Proxmox report of NullMoth ran with a 256 MiB BAR1 (nullmoth issue #275).
_BAR_NOTE = " NullMoth's one Proxmox report (nullmoth issue #275) ran at 256 MiB."

NEXT_STEPS = (
    "Start the VM. The desktop stays on the emulated display (vga std); apps get the NVIDIA GPU.",
    f"Download the NullMoth release yourself from {NULLMOTH_URL}/releases and check it with "
    "shasum -a 256 -c SHA256SUMS, then run sudo ./install.sh in the guest.",
    "Approve the extensions in System Settings > Privacy & Security, then reboot the guest.",
    "Verify with: kmutil showloaded --list-only | grep nullmoth",
    "Turn off system sleep in the guest: sudo pmset -a sleep 0",
    "Escape hatch: add -nvoff to the boot-args, or run nvidia-detach on the host.",
    "Warning: the NullMoth 1401 app edits the OpenCore config itself. Rebuilding the OpenCore "
    "disk with this tool later (or running nvidia-detach) wipes those edits.",
)


def notice_text() -> str:
    return f"{NOTICE_TITLE}\n\n{NOTICE_BODY}"


def detect_macos(cfg: dict[str, str]) -> str | None:
    """macOS release a VM was built for, from its description or name.

    The bash installer writes "macOS <Release> <n> VM" into the description
    and the TUI names new VMs macos-<release>. Anything else is unknown."""
    for key in ("description", "name"):
        m = _MACOS_RE.search(unquote(cfg.get(key, "")))
        if m:
            return m.group(1).lower()
    return None


@dataclass
class AttachPlan:
    vmid: int
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    steps: list[PlanStep] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def _oc_patch_source() -> str:
    return (resources.files("osx_proxmox_next") / "data/nullmoth/oc_patch.py").read_text()


def oc_patch_script(vid: str, action: str, amfipass_zip: Path | None = None) -> str:
    """Bash that mounts the VM's OpenCore disk and runs the shared patcher."""
    source = _oc_patch_source()
    assert _PYEOF not in source
    env = f"AMFIPASS_ZIP={shquote(str(amfipass_zip))} " if amfipass_zip else ""
    return (
        mount_oc_disk_script(vid)
        + f"{env}python3 - \"$OC_MNT/EFI/OC\" {action} <<'{_PYEOF}' && sync\n"
        + source
        + ("" if source.endswith("\n") else "\n")
        + f"{_PYEOF}\n"
    )


def build_attach_steps(
    vmid: int, bdf: str, current_args: str, amfipass_zip: Path, stop_first: bool = False,
    record: AttachRecord | None = None,
) -> list[PlanStep]:
    """Mode H: all functions on hostpci0, MMIO window, NullMoth OpenCore changes.

    The OpenCore step runs before any qm change: it is the one that can refuse
    (SIP bits, SecureBootModel), and a refusal must leave the VM untouched."""
    vid = str(vmid)
    steps = [_stop_step(vid)] if stop_first else []
    steps.append(_verify_stopped_step(vid))
    steps.append(PlanStep(
        title="Prepare OpenCore for NullMoth (backup, AMFIPass, boot-args, IONDRVSupport block)",
        argv=["bash", "-c", oc_patch_script(vid, "attach", amfipass_zip)],
        risk="action",
    ))
    if record is not None:
        steps.append(record_step(record))
    steps += build_hostpci_attach_steps(vmid, bdf, current_args)
    return steps


def build_detach_steps(
    vmid: int, current_args: str, has_hostpci0: bool, stop_first: bool = False,
    record: AttachRecord | None = None,
) -> list[PlanStep]:
    vid = str(vmid)
    steps = [_stop_step(vid)] if stop_first else []
    steps.append(_verify_stopped_step(vid))
    # Restore OpenCore first: with no config.plist.pre-nullmoth this step
    # refuses, so a VM that was never attached keeps its hostpci0 and args.
    steps.append(PlanStep(
        title="Restore the previous OpenCore config",
        argv=["bash", "-c", oc_patch_script(vid, "detach")],
        risk="action",
    ))
    steps += build_hostpci_detach_steps(vmid, current_args, has_hostpci0, record=record)
    return steps


def _vm_issues(vmid: int, cfg: dict[str, str], running: bool, allow_stop: bool) -> list[str]:
    issues: list[str] = []
    if "isa-applesmc" not in cfg.get("args", ""):
        issues.append(f"VM {vmid} is not a macOS VM made by this tool (no isa-applesmc in its args).")
    if running and not allow_stop:
        issues.append(f"VM {vmid} is running. Stop it first: qm stop {vmid}")
    return issues


def prepare_attach(
    vmid: int,
    gpu: str,
    amfipass_zip: Path,
    macos: str | None = None,
    allow_stop: bool = False,
    adapter: ProxmoxAdapter | None = None,
    sysfs_root: Path = Path("/sys"),
    cmdline_path: Path | None = None,
) -> AttachPlan:
    """Run every preflight check and, when they pass, build the plan."""
    plan = AttachPlan(vmid)
    pve = adapter or get_proxmox_adapter()
    info = fetch_vm_info(vmid, adapter=pve)
    if info is None:
        plan.issues.append(f"VM {vmid} not found.")
        return plan
    cfg = parse_config(info.config_raw)
    running = info.status == "running"
    plan.issues += _vm_issues(vmid, cfg, running, allow_stop)

    version = (macos or "").lower() or detect_macos(cfg)
    if version is None:
        plan.issues.append(
            "Cannot tell which macOS this VM runs. Pass the version explicitly (sequoia or tahoe)."
        )
    elif version not in _ALLOWED_MACOS:
        plan.issues.append(f"NullMoth needs macOS 15.5 Sequoia or newer (this VM is {version}).")
    elif version == "sequoia":
        plan.warnings.append("The guest must run macOS 15.5 or newer. Older Sequoia builds are not supported.")

    report: GpuReport = check_nvidia_gpu(gpu, sysfs_root, cmdline_path)
    plan.issues += report.issues
    plan.warnings += [w + _BAR_NOTE if w.startswith("BAR1") else w for w in report.warnings]
    plan.hints += report.hints
    plan.issues += _slot_issues(cfg, report.bdf)
    for other, other_running in gpu_users(pve, report.bdf, vmid) if report.ok else []:
        if other_running:
            plan.issues.append(f"The card is in use by running VM {other}.")
        else:
            plan.warnings.append(f"VM {other} also has this card assigned; only one can run at a time.")

    if plan.ok:
        record = AttachRecord(vmid, report.bdf, "nvidia", hostpci0=cfg.get("hostpci0"), args=cfg.get("args"),
                              vga=cfg.get("vga"), oc="nullmoth")
        plan.steps = build_attach_steps(
            vmid, report.bdf, cfg.get("args", ""), amfipass_zip, stop_first=running and allow_stop, record=record,
        )
    return plan


def _slot_issues(cfg: dict[str, str], bdf: str) -> list[str]:
    existing = hostpci_bdfs(cfg).get("hostpci0")
    if cfg.get("hostpci0") is not None and existing != {bdf}:
        return [f"hostpci0 is already in use on this VM ({cfg['hostpci0']}). "
                "Remove it first or run nvidia-detach."]
    return []


def prepare_detach(
    vmid: int, adapter: ProxmoxAdapter | None = None, allow_stop: bool = False,
) -> AttachPlan:
    plan = AttachPlan(vmid)
    info = fetch_vm_info(vmid, adapter=adapter or get_proxmox_adapter())
    if info is None:
        plan.issues.append(f"VM {vmid} not found.")
        return plan
    cfg = parse_config(info.config_raw)
    plan.issues += _vm_issues(vmid, cfg, info.status == "running", allow_stop)
    if plan.ok:
        plan.steps = build_detach_steps(
            vmid, cfg.get("args", ""), "hostpci0" in cfg,
            stop_first=info.status == "running" and allow_stop, record=load_record(vmid),
        )
    return plan
