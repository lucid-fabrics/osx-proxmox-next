"""Command line for the NVIDIA (NullMoth) setup: nvidia-attach does everything after the one-time notice."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from . import nullmoth, nullmoth_finish
from .defaults import DEFAULT_ISO_DIR
from .domain import MAX_VMID, MIN_VMID
from .nullmoth_engine import LATEST

__all__ = ["choose_version", "report", "run_attach", "run_versions", "version_lines"]

DRY_RUN_INTRO = (
    "Before that the tool downloads the NullMoth release, verifies it against SHA256SUMS, boots macOS recovery once "
    "to stage it inside macOS, and after the card is attached the first boot installs it by itself (about 15 minutes).",
)


def version_lines(releases: list[dict]) -> list[str]:
    """One line per release for a picker; the first stable release is the default."""
    default = next((r["tag"] for r in releases if not r["prerelease"]), "")
    lines = []
    for r in releases:
        mark = " (latest)" if r["tag"] == default else ""
        pre = " (pre-release)" if r["prerelease"] else ""
        lines.append(f"{r['tag']}{mark}{pre}  {r['published'][:10]}")
    return lines


def choose_version(args: argparse.Namespace, ask: Callable[[str], str] = input,
                   releases: Callable[[], list[dict]] = nullmoth_finish.versions) -> str:
    """--nullmoth-version when given, else the user's pick on a terminal (Enter = latest), else the latest."""
    chosen = (getattr(args, "nullmoth_version", "") or "").strip()
    if chosen or getattr(args, "yes", False) or not sys.stdin.isatty():
        return chosen or LATEST
    found = releases()
    print("NullMoth releases (newest first):")
    for line in version_lines(found):
        print("  " + line)
    return ask("Release to install [latest]: ").strip() or LATEST


def report(vmid: int, result: nullmoth_finish.FinishResult, say: Callable[[str], None] = print) -> int:
    for note in result.notes:
        say(f"NOTE: {note}")
    if not result.verified:
        say(f"The NullMoth driver is not confirmed in VM {vmid}: "
            f"{result.detail or result.result or 'the guest reported nothing'}.")
        say(nullmoth.FAILED_NOTE)
        return 1
    say(f"Done. VM {vmid}: NullMoth {result.version} is installed and loaded ({result.kexts} extensions).")
    if result.ip:
        say(f"  ssh <user>@{result.ip}" + ("" if result.ssh_up else "   (not answering yet)"))
        say(f"  vnc://{result.ip}" + ("" if result.vnc_up else "   (not answering yet)"))
    else:
        say("  The guest's address was not found yet: see your router or `ip neigh` on the host.")
    if result.display != "yes":
        say(nullmoth.DISPLAY_LINE)
    for line in nullmoth.NOTES:
        say(line)
    return 0


def _print_plan(plan: nullmoth.AttachPlan) -> None:
    for issue in plan.issues:
        print(f"REFUSED: {issue}")
    for line in plan.hints:
        print(f"  {line}")
    for warning in plan.warnings:
        print(f"WARN: {warning}")


def run_versions(_args: argparse.Namespace) -> int:
    try:
        found = nullmoth_finish.versions()
    except nullmoth_finish.FinishError as exc:
        print(f"ERROR: {exc}")
        return 1
    for line in version_lines(found):
        print(line)
    return 0


def run_attach(args: argparse.Namespace) -> int:
    from .oc_builder import AMFIPASS

    vmid = args.vmid
    if vmid < MIN_VMID or vmid > MAX_VMID:
        print(f"ERROR: VMID must be between {MIN_VMID} and {MAX_VMID}.")
        return 2
    print(nullmoth.notice_text())
    print()
    if not args.dry_run and not args.accept_risk:
        print("Nothing was changed. Read the notice above, then re-run with --accept-nullmoth-risk to apply.")
        return 2
    cache = Path(args.iso_dir or DEFAULT_ISO_DIR)
    if args.dry_run:
        plan = nullmoth.prepare_attach(vmid, args.gpu, cache / AMFIPASS.filename, macos=args.macos or None)
        _print_plan(plan)
        print("DRY RUN - nothing is changed.")
        for line in DRY_RUN_INTRO:
            print(line)
        for idx, step in enumerate(plan.steps, start=1):
            first = step.command.splitlines()[0]
            print(f"{idx:02d}. {step.title}")
            print(f"    {first[:200]}{'...' if len(first) > 200 or step.command.count(chr(10)) else ''}")
        return 0 if plan.ok else 2
    try:
        choice = choose_version(args)
        result = nullmoth_finish.finish(vmid, args.gpu, choice, iso_dir=cache, macos=args.macos,
                                        on_event=lambda m: print(f"  {m}", flush=True))
    except nullmoth_finish.FinishError as exc:
        print(f"ERROR: {exc}")
        return 2
    return report(vmid, result)
