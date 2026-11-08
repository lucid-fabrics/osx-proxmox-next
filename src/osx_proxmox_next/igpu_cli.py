"""Command line for the Intel iGPU passthrough (beta): igpu-check, igpu-install and igpu-finish."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from . import igpu_attach, igpu_engine, igpu_finish
from .defaults import DEFAULT_ISO_DIR
from .domain import MAX_VMID, MIN_VMID
from .intel_host import check_intel_gpu

__all__ = ["add_parsers", "dispatch", "run_check", "run_finish", "run_install"]

DOCS = "https://lucid-fabrics.github.io/osx-proxmox-next/docs/guides/intel-igpu-passthrough"
NOTICE = (
    "BETA. The host gives up its Intel graphics for good: the screen on the host stays dark and the Proxmox "
    "console works only over the network. The VM needs a monitor or an HDMI dummy plug on the host's native "
    "HDMI port (LSPCON HDMI and USB-C do not work). The iGPU shares the CPU's power budget, so heavy host load "
    "slows it. macOS Tahoe is experimental: restart it from Proxmox, not from inside macOS."
)


def add_parsers(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    sub.add_parser("igpu-check", help="BETA: check whether this host can pass its Intel iGPU to a macOS VM")
    install = sub.add_parser(
        "igpu-install", parents=[common],
        help=("BETA: create a macOS VM with the Intel iGPU passed through and install macOS hands-off, "
              "including the graphics package. ERASES the new VM's disk. The host must already be prepared "
              "(gpu-setup)."),
    )
    install.add_argument("--yes", action="store_true", help="Skip the confirmation question")
    finish = sub.add_parser(
        "igpu-finish",
        help=("BETA: attach the Intel iGPU to an installed i440fx macOS VM and install the graphics package "
              "inside it, with no steps in macOS. The VM must be stopped."),
    )
    finish.add_argument("vmid", type=int, help="VM ID (stopped, installed, machine type pc)")
    finish.add_argument("--macos", default="", help="ventura, sonoma, sequoia or tahoe, when it cannot be read from the VM")
    finish.add_argument("--iso-dir", default="", help="Directory for the downloads (default: the ISO storage)")


def _print_report(report) -> None:
    for line in report.issues:
        print(f"REFUSED: {line}")
    for line in report.hints:
        print(f"  {line}")
    for line in report.warnings:
        print(f"WARN: {line}")


def run_check() -> int:
    report = check_intel_gpu()
    info = report.engine["info"]
    if info:
        kind, head, detail = igpu_engine.verdict(info["device"])
        print(f"{info['model']} (8086:{info['device']:04x}, {info['family_label']})\n{head}\n{detail}")
    _print_report(report)
    layout = report.layout
    if layout and layout["ok"]:
        print(f"Host graphics memory ends at 0x{layout['host_top']:X}; the VM gets "
              f"max-ram-below-4g={layout['max_ram_below_4g_mb']}M and ROM {report.rom_file}.")
    if report.ok:
        print("The host is ready.")
        return 0
    return 2


def _on_event(message: str) -> None:
    print(f"  {message}", flush=True)


def _report_finish(vmid: int, result: igpu_finish.FinishResult) -> int:
    for note in result.notes:
        print(f"NOTE: {note}")
    if result.installed:
        print(f"Done. VM {vmid} has Metal acceleration. Plug a monitor or an HDMI dummy plug into the host's "
              "native HDMI port: the macOS screen appears there, the Proxmox console stays blank.")
        return 0
    print(f"The graphics package is not installed yet: {result.result or 'the guest reported nothing'}.")
    print("The VM is usable without acceleration. The package is tried again on the next boot (three tries at most).")
    return 1


def run_finish(args: argparse.Namespace) -> int:
    vmid = args.vmid
    if vmid < MIN_VMID or vmid > MAX_VMID:
        print(f"ERROR: VMID must be between {MIN_VMID} and {MAX_VMID}.")
        return 2
    print(NOTICE)
    try:
        result = igpu_finish.finish(vmid, iso_dir=Path(args.iso_dir or DEFAULT_ISO_DIR), on_event=_on_event,
                                    macos=args.macos)
    except igpu_finish.FinishError as exc:
        print(f"ERROR: {exc}")
        return 2
    return _report_finish(vmid, result)


def run_install(args: argparse.Namespace) -> int:
    from . import cli
    from .executor import apply_plan
    from .planner import build_plan
    from .rollback import create_snapshot, rollback_hints
    from .services import get_proxmox_adapter
    from .unattended import QmConsole, UnattendedError, run_unattended_install

    config = cli._config_from_args(args)
    rc = cli._validate_and_fetch_assets(args, config)
    if rc is not None:
        return rc
    report = check_intel_gpu()
    _print_report(report)
    if not report.ok:
        print("Nothing was changed. Prepare the host first: osx-next-cli gpu-setup")
        return 2
    print(NOTICE)
    print(f"VM {config.vmid} ({config.macos}) will be created and its {config.disk_gb} GB disk ERASED by the install.")
    if not args.yes and input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing was changed.")
        return 2
    snapshot = create_snapshot(config.vmid)
    # everything build_plan does except its final "Start VM"
    steps = [s for s in build_plan(config) if s.title != "Start VM"] + igpu_attach.build_fresh_vm_steps(config.vmid)
    result = apply_plan(steps, execute=True)
    if not result.ok:
        failed = next((r for r in result.results if not r.ok), None)
        print("Create FAILED" + (f" at: {failed.title}" if failed else "") + f". Log: {result.log_path}")
        for hint in rollback_hints(snapshot):
            print(f"ROLLBACK: {hint}")
        return 4
    pve = get_proxmox_adapter()
    started = pve.qm("start", str(config.vmid))
    if not started.ok:
        print(f"ERROR: could not start VM {config.vmid}: {started.output.strip()[-300:]}")
        return 4
    console = QmConsole(config.vmid)
    print("Installing macOS hands-off (30 to 60 minutes) ...")
    try:
        run_unattended_install(console, config.disk_gb, on_event=_on_event, recovery_slot="sata1")
    except UnattendedError as exc:
        print(f"ERROR: {exc}")
        shot = console.save_frame(f"/var/log/osx-next-unattended-{config.vmid}.png")
        if shot:
            print(f"The console at that moment was saved to {shot}.")
        print(f"The VM was left as it is. Once macOS is installed, run: osx-next-cli igpu-finish {config.vmid}")
        return 1
    pve.qm("stop", str(config.vmid))
    igpu_finish.wait_stopped(pve, config.vmid, 120, time.monotonic, time.sleep)
    print("macOS is installed. Setting up the iGPU ...")
    try:
        finished = igpu_finish.finish(config.vmid, console=console, pve=pve, macos=config.macos,
                                      on_event=_on_event)
    except igpu_finish.FinishError as exc:
        print(f"ERROR: {exc}")
        print(f"macOS is installed. Fix the problem above, then run: osx-next-cli igpu-finish {config.vmid}")
        return 2
    return _report_finish(config.vmid, finished)


def dispatch(args: argparse.Namespace) -> int | None:
    if args.cmd == "igpu-check":
        return run_check()
    if args.cmd == "igpu-finish":
        return run_finish(args)
    if args.cmd == "igpu-install":
        return run_install(args)
    return None
