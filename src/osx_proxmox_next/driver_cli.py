"""Command line of "update the driver": driver-status, driver-update and driver-undo."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from . import driver_engine as engine, driver_snapshot, driver_spawn, nullmoth
from .defaults import DEFAULT_ISO_DIR
from .domain import MAX_VMID, MIN_VMID, SUPPORTED_MACOS
from .driver_core import DriverError, RunEnv
from .driver_plan import DriverPlan, prepare
from .driver_run import finish_interrupted, put_back, run_update, undo_target
from .driver_targets import Choice
from .services import get_proxmox_adapter
from .unattended import QmConsole

__all__ = ["add_parsers", "dispatch", "run_status", "run_undo", "run_update_command"]


def add_parsers(sub: argparse._SubParsersAction) -> None:
    status = sub.add_parser("driver-status", help="Show which graphics driver a Mac has, what is newer and its driver snapshots")
    status.add_argument("--vmid", type=int, required=True, help="VM ID of the Mac")
    status.add_argument("--json", action="store_true", help="Print the answer as JSON for scripts")
    update = sub.add_parser(
        "driver-update",
        help=("Update the graphics driver of a Mac that has a card attached: NullMoth (NVIDIA), WhateverXe and its "
              "graphics package (Intel) or Lilu and WhateverGreen (AMD). A snapshot is saved first."))
    update.add_argument("--vmid", type=int, required=True, help="VM ID of the Mac")
    update.add_argument("--version", default="", metavar="TAG", help="NVIDIA: NullMoth release tag (default: the latest)")
    update.add_argument("--wex-version", default="", metavar="TAG", help="Intel: WhateverXe release tag (default: the latest)")
    update.add_argument("--graphics-version", default="", metavar="TAG",
                        help="Intel: WhateverXe Graphics release tag (default: the latest)")
    update.add_argument("--dry-run", action="store_true", dest="dry_run", help="Print the plan, change nothing")
    update.add_argument("--yes", action="store_true", help="Do not ask for confirmation (never answers the NullMoth notice)")
    update.add_argument("--accept-nullmoth-risk", action="store_true", dest="accept_risk",
                        help="NVIDIA: acknowledge the NullMoth notice (required to apply)")
    update.add_argument("--no-snapshot", action="store_true", dest="no_snapshot",
                        help="Go on without a snapshot when the storage of the Mac cannot take one")
    update.add_argument("--reinstall", action="store_true", help="Install the same version again")
    update.add_argument("--finish", action="store_true", help="Continue the update that was cut off")
    update.add_argument("--macos", default="", help="When the Mac's description or name does not say which macOS it runs")
    update.add_argument("--iso-dir", default="", help="Folder that caches the downloads")
    undo = sub.add_parser("driver-undo", help="Undo the last driver update: put the Mac back to its driver snapshot")
    undo.add_argument("--vmid", type=int, required=True, help="VM ID of the Mac")
    undo.add_argument("--yes", action="store_true", help="Do not ask for confirmation")


def _bad_vmid(vmid: int) -> bool:
    if MIN_VMID <= vmid <= MAX_VMID:
        return False
    print(f"ERROR: VMID must be between {MIN_VMID} and {MAX_VMID}.")
    return True


def _bad_args(args: argparse.Namespace) -> bool:
    """A release name or tag that would end up in a file pattern or a URL must look like one."""
    if args.macos and args.macos not in SUPPORTED_MACOS:
        print(f"ERROR: --macos must be one of: {', '.join(SUPPORTED_MACOS)}.")
        return True
    for flag, value in (("--version", args.version), ("--wex-version", args.wex_version),
                        ("--graphics-version", args.graphics_version)):
        if value and not driver_spawn.TAG.match(value):
            print(f"ERROR: {flag} {value!r} is not a release tag.")
            return True
    return False


def _choice(args: argparse.Namespace) -> Choice:
    return Choice(args.version, args.wex_version, args.graphics_version, args.macos)


def status_data(vmid: int, plan: DriverPlan, latest_error: str = "") -> dict:
    snaps = driver_snapshot.list_tool(get_proxmox_adapter(), vmid)
    judged = plan.judged() if plan.target else {"state": "", "rows": []}
    return {"vmid": vmid, "name": plan.name, "kind": plan.kind, "refused": plan.refusals,
            "installed": plan.installed if plan.installed_known else None,
            "latest": plan.target.versions() if plan.target else {}, "state": judged["state"],
            "snapshots": snaps, "unfinished": plan.interrupted, "latest_error": latest_error}


def run_status(args: argparse.Namespace) -> int:
    if _bad_vmid(args.vmid):
        return 2
    plan = prepare(args.vmid, reinstall=True)
    data = status_data(args.vmid, plan)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0 if not plan.refusals else 2
    for line in plan.refusals:
        print(f"REFUSED: {line}")
    if plan.refusals and not plan.kind:
        return 2
    print(f"VM {args.vmid} {plan.name}: {engine.CARD_NAME.get(plan.kind, '?')} card")
    if plan.interrupted:
        print("The driver update stopped before it finished. Finish it: osx-next-cli driver-update --vmid %d --finish --yes"
              " (or undo it: osx-next-cli driver-undo --vmid %d)." % (args.vmid, args.vmid))
    print("Installed: " + (engine.driver_line(plan.kind, plan.installed) if plan.installed_known else "installed version unknown"))
    if plan.target:
        print("Latest:    " + engine.driver_line(plan.kind, plan.target.versions()))
        print({"update": "A newer driver is available.", "current": "Up to date.",
               "unknown": "The installed version is not known."}.get(data["state"], ""))
    print("Driver snapshots: " + (", ".join(data["snapshots"]) or "none"))
    return 0


def _confirm(prompt: str, yes: bool, ask: Callable[[str], str]) -> bool:
    if yes:
        return True
    return sys.stdin.isatty() and ask(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def _env(args: argparse.Namespace, say: Callable[[str], None]) -> RunEnv:
    return RunEnv(get_proxmox_adapter(), QmConsole(args.vmid), Path(args.iso_dir or DEFAULT_ISO_DIR),
                  on_event=lambda m: say(f"  {m}"), no_snapshot=args.no_snapshot)


def _gate(args: argparse.Namespace, plan: DriverPlan, ask: Callable[[str], str]) -> int | None:
    """The questions before anything changes. None means go on, a number is the exit code."""
    if plan.kind == "nvidia":
        print(nullmoth.notice_text() + "\n")
        if not args.accept_risk:
            print("Nothing was changed. Read the notice above, then re-run with --accept-nullmoth-risk to apply.")
            return 2
    if not plan.snapshot_ok and not args.no_snapshot:
        typed = sys.stdin.isatty() and ask(f"Type '{engine.NO_SNAPSHOT_PHRASE}' to go on without one: ").strip()
        if typed != engine.NO_SNAPSHOT_PHRASE:
            print("Nothing was changed. Re-run with --no-snapshot to go on without a snapshot.")
            return 2
        args.no_snapshot = True
    if not _confirm("Go on?", args.yes, ask):
        print("Nothing was changed.")
        return 2
    return None


def run_update_command(args: argparse.Namespace, ask: Callable[[str], str] = input,
                       say: Callable[[str], None] = print) -> int:
    if _bad_vmid(args.vmid) or _bad_args(args):
        return 2
    if args.finish:
        return _finish(args, say)
    plan = prepare(args.vmid, _choice(args), reinstall=args.reinstall)
    for line in plan.lines():
        print(line)
    if not plan.ok:
        return 2
    if plan.nothing_to_do:
        return 0
    if args.dry_run:
        print("DRY RUN - nothing is changed.")
        return 0
    code = _gate(args, plan, ask)
    if code is not None:
        return code
    try:
        return _report(run_update(plan, _env(args, say)), say)
    except DriverError as exc:
        print(f"ERROR: {exc}")
        return 2


def _finish(args: argparse.Namespace, say: Callable[[str], None]) -> int:
    try:
        return _report(finish_interrupted(args.vmid, _env(args, say)), say)
    except DriverError as exc:
        print(f"ERROR: {exc}")
        return 2


def _report(result, say: Callable[[str], None]) -> int:
    for note in result.notes:
        say(f"NOTE: {note}")
    if not result.ok:
        say(f"The update did not finish: {result.error}")
        return 1
    say("Done. " + result.detail + ".")
    if result.snapshot:
        say(f"Driver snapshot {result.snapshot} is kept. driver-undo goes back to it.")
    return 0


def run_undo(args: argparse.Namespace, ask: Callable[[str], str] = input) -> int:
    if _bad_vmid(args.vmid):
        return 2
    env = RunEnv(get_proxmox_adapter(), on_event=lambda m: print(f"  {m}"))
    try:
        name = undo_target(args.vmid, env)
    except DriverError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"The Mac stops, its disks go back to the snapshot of {engine.snapshot_date(name)} ({name}) and it starts again. "
          "Everything done in macOS since is lost.")
    if not _confirm("Undo the driver update?", args.yes, ask):
        print("Nothing was changed.")
        return 2
    try:
        put_back(args.vmid, env)
    except DriverError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Done. VM {args.vmid} is back to {name}.")
    return 0


def dispatch(args: argparse.Namespace) -> int | None:
    if args.cmd == "driver-status":
        return run_status(args)
    if args.cmd == "driver-update":
        return run_update_command(args)
    if args.cmd == "driver-undo":
        return run_undo(args)
    return None

