"""Command line for the macOS 27 arm64 via Stela (beta) target: plan, apply, uninstall and the GPU list."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from .domain import PlanStep
from .executor import StepResult, apply_plan
from .stela import (
    DEFAULT_CORES,
    DEFAULT_DISK_GB,
    DEFAULT_MEMORY_MB,
    MAX_VMID,
    MIN_VMID,
    STELA_LABEL,
    STELA_RELEASE_URL,
    StelaConfig,
    validate_stela_config,
)
from .stela_gpus import list_host_gpus
from .stela_install import PveHost, default_log_path, run_install
from .stela_planner import build_stela_destroy_plan, build_stela_plan

__all__ = ["add_parser", "install_vm", "run"]


def add_parser(sub: argparse._SubParsersAction) -> None:
    stela = sub.add_parser("stela", help=f"{STELA_LABEL}: install macOS 27 with no Mac, using a downloaded Stela release")
    actions = stela.add_subparsers(dest="stela_cmd", required=True)
    for name, text in (("plan", "Show the steps without running them"), ("apply", "Create the VM and run the install")):
        cmd = actions.add_parser(name, help=text)
        cmd.add_argument("--vmid", type=int, required=True)
        cmd.add_argument("--name", required=True)
        cmd.add_argument("--storage", required=True, help="Storage for the VM's disk and EFI disk")
        cmd.add_argument("--cores", type=int, default=DEFAULT_CORES)
        cmd.add_argument("--memory", type=int, default=DEFAULT_MEMORY_MB, help="RAM in MB")
        cmd.add_argument("--disk", type=int, default=DEFAULT_DISK_GB, help="Disk in GB")
        cmd.add_argument("--bridge", default="vmbr0")
        cmd.add_argument("--gpu-pci", default="", help="Pass this host GPU to the VM, e.g. 0000:0a:00 (default none; see `stela gpus`)")
        cmd.add_argument("--stela-url", default=STELA_RELEASE_URL,
                         help="Folder with the Stela tarball and its SHA256SUMS (a mirror; the pinned sha256 is still required)")
        cmd.add_argument("--vga", default="std", choices=["std", "none"], help="Emulated display when no GPU is passed")
        if name == "plan":
            cmd.add_argument("--json", action="store_true", help="Print the plan as JSON")
        else:
            cmd.add_argument("--execute", action="store_true", help="Actually run (default is dry run)")
    actions.add_parser("gpus", help="List the host GPUs that can be passed to the VM")
    rm = actions.add_parser("uninstall", help="Destroy the VM and what it left on the host")
    rm.add_argument("--vmid", type=int, required=True)
    rm.add_argument("--execute", action="store_true", help="Actually run (default is dry run)")


def _config(args: argparse.Namespace) -> StelaConfig:
    return StelaConfig(vmid=args.vmid, name=args.name, storage=args.storage, cores=args.cores, memory_mb=args.memory,
                       disk_gb=args.disk, bridge=args.bridge, gpu_pci=args.gpu_pci, vga=args.vga, stela_url=args.stela_url)


def _run_steps(steps: list, execute: bool) -> tuple[bool, Path]:
    result = apply_plan(steps, execute=execute)
    for item in result.results:
        print(f"{'ok  ' if item.ok else 'FAIL'} {item.title}")
        if not item.ok:
            print(item.output)
    print(f"Log: {result.log_path}")
    return result.ok, result.log_path


def _print_plan(steps: list, as_json: bool) -> None:
    if as_json:
        print(json.dumps([{"title": s.title, "argv": s.argv, "risk": s.risk} for s in steps], indent=2))
        return
    for idx, step in enumerate(steps, start=1):
        detail = step.command if step.argv[0] != "bash" else f"bash script ({len(step.argv) - 4} arguments)"
        print(f"{idx:>2}. {step.title}\n    {detail}")


def install_vm(config: StelaConfig, emit: Callable[[str], None] = print) -> bool:
    """The whole install, from nothing to Setup Assistant: the plan, then the keys and the serial log."""
    steps = build_stela_plan(config)
    began = time.monotonic()

    def say(line: str) -> None:    # every line is stamped with the seconds since the start
        emit(f"[{int(time.monotonic() - began):>5}s] {line}")

    def on_step(idx: int, total: int, step: PlanStep, result: StepResult | None) -> None:
        if result is None:
            say(f"[{idx}/{total}] {step.title}")
        elif not result.ok:
            say(f"FAILED: {step.title}\n{result.output}")

    if not apply_plan(steps, execute=True, on_step=on_step).ok:
        return False
    log = default_log_path(config.vmid)
    say(f"Serial log: {log}")
    with PveHost(config, log) as host:
        outcome = run_install(host, say)
    if not outcome.ok:
        subprocess.run(["qm", "stop", str(config.vmid)], capture_output=True, timeout=120, check=False)
        say(f"The VM {config.vmid} is stopped. Remove it with: osx-next-cli stela uninstall --vmid {config.vmid} --execute")
        return False
    say(outcome.message)
    say("Phase times (s from the VM start): " + ", ".join(f"{k} {v}" for k, v in outcome.seconds.items()))
    return True


def run(args: argparse.Namespace) -> int:
    if args.stela_cmd == "gpus":
        for gpu in list_host_gpus():
            print(gpu.label)
        return 0
    if args.stela_cmd == "uninstall":
        if not MIN_VMID <= args.vmid <= MAX_VMID:
            print(f"ERROR: VMID must be between {MIN_VMID} and {MAX_VMID}.")
            return 2
        ok, _ = _run_steps(build_stela_destroy_plan(args.vmid), args.execute)
        return 0 if ok else 1
    config = _config(args)
    issues = validate_stela_config(config)
    if issues:
        print("\n".join(f"ERROR: {issue}" for issue in issues))
        return 2
    if args.stela_cmd == "plan":
        _print_plan(build_stela_plan(config), args.json)
        return 0
    if not args.execute:
        ok, _ = _run_steps(build_stela_plan(config), False)
        return 0 if ok else 1
    return 0 if install_vm(config) else 1
