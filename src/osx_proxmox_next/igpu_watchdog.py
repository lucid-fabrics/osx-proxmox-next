"""The host-side power watchdog for macOS Tahoe VMs with the Intel iGPU (beta).

Tahoe hangs at the very end of every restart and shut down while the iGPU is attached (WhateverXe issue 62).
data/gpu/igpu_power_watchdog.py runs on the host as the systemd unit osx-next-power-watchdog@<vmid>, reads
the kernel console from the VM's serial socket and stops the VM (and starts it again for a restart) once
macOS has asked for it and then hung. This module installs, enables and removes it; the bash installer has
the same steps (igpu_watchdog_enable / igpu_watchdog_disable).
"""

from __future__ import annotations

from pathlib import Path

from . import igpu_engine
from .domain import PlanStep
from .gpu import parse_config
from .igpu_attach import oc_step
from .infrastructure import ProxmoxAdapter
from .nullmoth import detect_macos
from .services import fetch_vm_info, get_proxmox_adapter

__all__ = ["DAEMON_PATH", "UNIT_PATH", "applies_to", "disable_steps", "enable_steps", "prepare", "unit_name"]

LIB_DIR = "/usr/local/lib/osx-proxmox-next"
DAEMON_FILE = "igpu_power_watchdog.py"
UNIT_FILE = "osx-next-power-watchdog@.service"
DAEMON_PATH = f"{LIB_DIR}/{DAEMON_FILE}"
UNIT_PATH = f"/etc/systemd/system/{UNIT_FILE}"
WATCHDOG_NOTE = ("macOS Tahoe hangs at the end of every restart and shut down while the iGPU is attached "
                 "(WhateverXe issue 62). A power watchdog on the host finishes them: about a minute after macOS "
                 "hangs, the host stops the VM, and starts it again when you chose Restart. It reads the VM's "
                 "serial port, so the Proxmox serial console of this VM stays busy.")


def unit_name(vmid: int) -> str:
    return f"osx-next-power-watchdog@{vmid}.service"


def applies_to(version: str | None) -> bool:
    """Only Tahoe needs it: Ventura, Sonoma and Sequoia restart and shut down normally with the iGPU."""
    return version == "tahoe"


def enable_steps(vmid: int, cfg: dict[str, str], data: Path | None = None) -> list[PlanStep]:
    """Install the watchdog, give the VM a serial socket and serial=3, enable the unit. The VM must be stopped."""
    src = data or igpu_engine.data_dir()
    vid = str(vmid)
    steps = [
        PlanStep(title="Install the power watchdog on the host",
                 argv=["install", "-D", "-m", "755", str(src / DAEMON_FILE), DAEMON_PATH]),
        PlanStep(title="Install its systemd unit",
                 argv=["install", "-D", "-m", "644", str(src / UNIT_FILE), UNIT_PATH]),
        PlanStep(title="Reload systemd", argv=["systemctl", "daemon-reload"]),
    ]
    if cfg.get("serial0") != "socket":
        steps.append(PlanStep(title="Give the VM a serial port (socket) for the kernel console",
                              argv=["qm", "set", vid, "--serial0", "socket"]))
    steps.append(oc_step(vid, "serial", "Send the macOS kernel console to the serial port (boot-arg serial=3)"))
    steps.append(PlanStep(title=f"Start the power watchdog for VM {vid} at every host boot",
                          argv=["systemctl", "enable", unit_name(vmid)]))
    # restart, not start: a watchdog that is already running must pick up the daemon installed above
    steps.append(PlanStep(title=f"Start the power watchdog for VM {vid} now",
                          argv=["systemctl", "restart", unit_name(vmid)], risk="action"))
    return steps


def disable_steps(vmid: int) -> list[PlanStep]:
    """Stop and disable the unit and drop serial=3. The serial socket stays: it does no harm."""
    vid = str(vmid)
    return [
        PlanStep(title=f"Stop the power watchdog for VM {vid}",
                 argv=["bash", "-c", f"systemctl disable --now {unit_name(vmid)} 2>/dev/null || true"]),
        oc_step(vid, "unserial", "Take the kernel console off the serial port (boot-arg serial=3)"),
    ]


def prepare(vmid: int, remove: bool = False, adapter: ProxmoxAdapter | None = None) -> tuple[list[str], list[PlanStep]]:
    """Preflight `igpu-watchdog <vmid> [--remove]`: (issues, steps). Steps only when there are no issues."""
    pve = adapter or get_proxmox_adapter()
    info = fetch_vm_info(vmid, adapter=pve)
    if info is None:
        return [f"VM {vmid} not found."], []
    cfg = parse_config(info.config_raw)
    issues: list[str] = []
    if info.status != "stopped":
        issues.append(f"VM {vmid} is running. Stop it first: qm stop {vmid}")
    if remove:
        return issues, ([] if issues else disable_steps(vmid))
    if not cfg.get("hostpci0", "").split(",")[0].endswith(igpu_engine.IGD_SLOT):
        issues.append(f"VM {vmid} does not have the iGPU on hostpci0. Set it up first: osx-next-cli igpu-finish {vmid}")
    version = detect_macos(cfg)
    if version is not None and not applies_to(version):
        issues.append(f"VM {vmid} runs macOS {version.capitalize()}, which restarts and shuts down normally with the "
                      "iGPU. The watchdog is only for Tahoe.")
    return issues, ([] if issues else enable_steps(vmid, cfg))
