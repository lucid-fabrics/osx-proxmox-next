"""Command line for the Intel iGPU passthrough (beta): igpu-check, igpu-install and igpu-finish."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import igpu_attach, igpu_engine, igpu_finish, igpu_report, igpu_watchdog
from .igpu_attach import SETUP_ASSISTANT_NOTE
from .defaults import DEFAULT_ISO_DIR
from .domain import MAX_VMID, MIN_VMID, SUPPORTED_MACOS
from .intel_host import check_intel_gpu

TROUBLESHOOTING_URL = "https://github.com/lucid-fabrics/osx-proxmox-next/blob/main/docs/docs/guides/intel-igpu-passthrough.md"

__all__ = ["add_parsers", "dispatch", "run_check", "run_finish", "run_install", "run_report", "run_versions",
           "run_watchdog"]

DOCS = "https://lucid-fabrics.github.io/osx-proxmox-next/docs/guides/intel-igpu-passthrough"
NOTICE = (
    "BETA. The host gives up its Intel graphics for good: the screen on the host stays dark and the Proxmox "
    "console works only over the network. The VM needs a monitor or an HDMI dummy plug on the host's native "
    "HDMI port (LSPCON HDMI and USB-C do not work). The iGPU shares the CPU's power budget, so heavy host load "
    "slows it. macOS Tahoe is experimental: a watchdog on the host finishes its restarts and shut downs, "
    "which hang at the very end. The watchdog also records crashes of any macOS version: igpu-report bundles them."
)


VERSION_HELP = ("WhateverXe release to install: a tag from igpu-versions (default: the latest release). "
                "Ask on a terminal when not given.")
GFX_HELP = "WhateverXe Graphics release to install: a tag from igpu-versions (default: the latest release)."


def _add_version_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--wex-version", default="", metavar="TAG", help=VERSION_HELP)
    parser.add_argument("--graphics-version", default="", metavar="TAG", help=GFX_HELP)


def add_parsers(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    check = sub.add_parser("igpu-check", help="BETA: check whether this host can pass its Intel iGPU to a macOS VM")
    check.add_argument("--wex-version", default="", metavar="TAG",
                       help="Judge the chip against this WhateverXe release (default: the latest release)")
    install = sub.add_parser(
        "igpu-install", parents=[common],
        help=("BETA: create a macOS VM with the Intel iGPU passed through and install macOS hands-off, "
              "including the graphics package. ERASES the new VM's disk. The host must already be prepared "
              "(gpu-setup)."),
    )
    install.add_argument("--yes", action="store_true", help="Skip the confirmation question")
    _add_version_options(install)
    finish = sub.add_parser(
        "igpu-finish",
        help=("BETA: attach the Intel iGPU to an installed i440fx macOS VM and install the graphics package "
              "inside it, with no steps in macOS. The VM must be stopped."),
    )
    finish.add_argument("--yes", action="store_true", help="Skip the confirmation question")
    finish.add_argument("vmid", type=int, help="VM ID (stopped, installed, machine type pc)")
    finish.add_argument("--macos", default="", help="ventura, sonoma, sequoia or tahoe, when it cannot be read from the VM")
    finish.add_argument("--iso-dir", default="", help="Directory for the downloads (default: the ISO storage)")
    _add_version_options(finish)
    watchdog = sub.add_parser(
        "igpu-watchdog",
        help=("BETA: install (or --remove) the host-side watchdog of a macOS VM with the iGPU: it records crashes "
              "and finishes the restarts and shut downs that hang on Tahoe. igpu-finish installs it already. "
              "The VM must be stopped."),
    )
    watchdog.add_argument("vmid", type=int, help="VM ID (stopped, iGPU on hostpci0)")
    watchdog.add_argument("--remove", action="store_true", help="Stop and remove the watchdog of this VM")
    sub.add_parser("igpu-versions", help="BETA: list the published WhateverXe and WhateverXe Graphics releases, newest first")
    rep = sub.add_parser(
        "igpu-report",
        help=("BETA: bundle the latest crash snapshots, the installed versions and the config of an iGPU VM into one "
              "tar.gz and print a summary to paste into a WhateverXe issue. Nothing is sent anywhere."),
    )
    rep.add_argument("vmid", type=int, help="VM ID")
    rep.add_argument("--out", default="", help="Directory for the bundle (default: /var/log/osx-proxmox-next/<vmid>)")


def _print_report(report) -> None:
    for line in report.issues:
        print(f"REFUSED: {line}")
    for line in report.hints:
        print(f"  {line}")
    for line in report.warnings:
        print(f"WARN: {line}")


def run_check(wex: str = igpu_engine.LATEST) -> int:
    report = check_intel_gpu(wex=wex)
    info = report.engine["info"]
    if info:
        kind, head, detail = report.engine["verdict"]
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
    for line in result.versions:
        print(f"Installed: {line}")
    for note in result.notes:
        print(f"NOTE: {note}")
    if result.installed and result.accelerated:
        print(f"Done. VM {vmid}: Metal acceleration is active ({result.accel_detail}). Plug a monitor or an HDMI "
              "dummy plug into the host's native HDMI port: the macOS screen appears there, the Proxmox console "
              "stays blank.")
        return 0
    if result.installed:
        print(f"The graphics package is installed in VM {vmid}, but acceleration was not confirmed: the host saw "
              f"no GPU work after the final boot ({result.accel_detail}).")
        print(f"See the troubleshooting section of {TROUBLESHOOTING_URL}")
        return 1
    print(f"The graphics package is not installed yet: {result.result or 'the guest reported nothing'}.")
    print("The VM is usable without acceleration. The package is tried again on the next boot (three tries at most).")
    return 1


def _versions_from(args: argparse.Namespace, interactive: bool) -> tuple[str, str]:
    """(WhateverXe, Graphics) choice from the flags; asked on a terminal for the ones not given."""
    wex, gfx = args.wex_version or "", args.graphics_version or ""
    if interactive and not args.yes:
        wex = wex or igpu_finish.choose_release("whateverxe", input, print)
        gfx = gfx or igpu_finish.choose_release("graphics", input, print)
    return wex or igpu_engine.LATEST, gfx or igpu_engine.LATEST


def run_versions() -> int:
    try:
        for kind in igpu_engine.RELEASE_KINDS:
            releases = igpu_engine.list_releases(kind)
            latest = next((r["tag"] for r in releases if not r["prerelease"]), "")
            print(f"{igpu_engine.RELEASE_KINDS[kind][2]} releases, newest first:")
            for rel in releases:
                flags = (" (latest)" if rel["tag"] == latest else "") + (" (pre-release)" if rel["prerelease"] else "")
                print(f"  {rel['tag']}  {rel['published'][:10]}{flags}")
    except igpu_engine.ReleaseError as exc:
        print(f"ERROR: {exc}")
        return 2
    return 0


def run_report(args: argparse.Namespace) -> int:
    try:
        path, summary = igpu_report.report(args.vmid, Path(args.out) if args.out else None)
    except OSError as exc:
        print(f"ERROR: could not write the report: {exc}")
        return 2
    print(summary, end="")
    return 0


def run_finish(args: argparse.Namespace) -> int:
    vmid = args.vmid
    if vmid < MIN_VMID or vmid > MAX_VMID:
        print(f"ERROR: VMID must be between {MIN_VMID} and {MAX_VMID}.")
        return 2
    print(NOTICE)
    if not args.yes and not (sys.stdin.isatty() and input(
            f"VM {vmid} boots macOS recovery once, then gets the iGPU. Continue? [y/N] ").strip().lower() in ("y", "yes")):
        print("Nothing was changed. Confirm (or pass --yes) to attach the iGPU.")
        return 2
    try:
        wex, gfx = _versions_from(args, sys.stdin.isatty())
        result = igpu_finish.finish(vmid, iso_dir=Path(args.iso_dir or DEFAULT_ISO_DIR), on_event=_on_event,
                                    macos=args.macos, wex=wex, graphics=gfx)
    except (igpu_finish.FinishError, igpu_engine.ReleaseError) as exc:
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
    report = check_intel_gpu(wex=getattr(args, "wex_version", "") or igpu_engine.LATEST)
    _print_report(report)
    if not report.ok:
        print("Nothing was changed. Prepare the host first: osx-next-cli gpu-setup")
        return 2
    try:
        wex, gfx = _versions_from(args, sys.stdin.isatty())
        if wex != igpu_engine.LATEST and wex != getattr(args, "wex_version", ""):
            report = check_intel_gpu(wex=wex)  # picked from the list: judge the chip against that release
            _print_report(report)
            if not report.ok:
                print("Nothing was changed. Pick another WhateverXe release or prepare the host: osx-next-cli gpu-setup")
                return 2
        if wex != igpu_engine.LATEST or gfx != igpu_engine.LATEST:
            # fail now on a wrong tag or an unreachable GitHub, not after a 45 minute install
            igpu_engine.select_assets(wex, gfx)
    except igpu_engine.ReleaseError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(NOTICE)
    print(f"VM {config.vmid} ({config.macos}) will be created and its {config.disk_gb} GB disk ERASED by the install.")
    if not args.yes and input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing was changed.")
        return 2
    snapshot = create_snapshot(config.vmid)
    # everything build_plan does except its final "Start VM"
    steps = [s for s in build_plan(config) if s.title != "Start VM"] + igpu_attach.build_fresh_vm_steps(config.vmid, SUPPORTED_MACOS[config.macos]["label"])
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
                                      on_event=_on_event, wex=wex, graphics=gfx)
    except igpu_finish.FinishError as exc:
        print(f"ERROR: {exc}")
        print(f"macOS is installed. Fix the problem above, then run: osx-next-cli igpu-finish {config.vmid}")
        return 2
    code = _report_finish(config.vmid, finished)
    print(SETUP_ASSISTANT_NOTE)  # vga is none: the Proxmox console is blank, so the setup is finished on the host's screen
    return code


def run_watchdog(args: argparse.Namespace) -> int:
    from .executor import apply_plan

    issues, steps = igpu_watchdog.prepare(args.vmid, remove=args.remove)
    for line in issues:
        print(f"REFUSED: {line}")
    if issues:
        return 2
    result = apply_plan(steps, execute=True)
    if not result.ok:
        failed = next((r for r in result.results if not r.ok), None)
        print("FAILED" + (f" at: {failed.title}" if failed else "") + f". Log: {result.log_path}")
        return 4
    if args.remove:
        print(f"The watchdog of VM {args.vmid} is removed. Crashes are no longer recorded, and restarts and shut downs "
              "from inside macOS Tahoe hang again; stop the VM from Proxmox.")
    else:
        print(f"The watchdog of VM {args.vmid} is running. {igpu_watchdog.WATCHDOG_NOTE}")
        print(f"Its log: journalctl -u {igpu_watchdog.unit_name(args.vmid)}")
    return 0


def dispatch(args: argparse.Namespace) -> int | None:
    if args.cmd == "igpu-check":
        return run_check(getattr(args, "wex_version", "") or igpu_engine.LATEST)
    if args.cmd == "igpu-finish":
        return run_finish(args)
    if args.cmd == "igpu-install":
        return run_install(args)
    if args.cmd == "igpu-watchdog":
        return run_watchdog(args)
    if args.cmd == "igpu-versions":
        return run_versions()
    if args.cmd == "igpu-report":
        return run_report(args)
    return None
