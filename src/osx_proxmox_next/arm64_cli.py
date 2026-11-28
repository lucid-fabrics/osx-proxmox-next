"""Command line for the macOS 27 arm64 (beta) target: plan, apply and uninstall of the runner VM."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .arm64 import (
    ARM64_LABEL,
    DEFAULT_CORES,
    DEFAULT_DISK_GB,
    DEFAULT_MEMORY_MB,
    DEFAULT_SOURCE_REF,
    DEFAULT_SOURCE_SHA256,
    DEFAULT_GUEST_USER,
    DEFAULT_SOURCE_URL,
    Arm64Config,
    validate_arm64_config,
)
from .arm64_planner import build_arm64_destroy_plan, build_arm64_plan
from .executor import apply_plan
from .domain import MAX_VMID, MIN_VMID

__all__ = ["add_parser", "run"]


def add_parser(sub: argparse._SubParsersAction) -> None:
    arm = sub.add_parser("arm64", help=f"{ARM64_LABEL}: a Debian runner VM that builds and runs it")
    actions = arm.add_subparsers(dest="arm64_cmd", required=True)
    for name, text in (("plan", "Show the steps without running them"), ("apply", "Create the runner VM")):
        cmd = actions.add_parser(name, help=text)
        cmd.add_argument("--vmid", type=int, required=True)
        cmd.add_argument("--name", required=True)
        cmd.add_argument("--storage", required=True, help="Storage for the runner's disk")
        cmd.add_argument("--cores", type=int, default=DEFAULT_CORES)
        cmd.add_argument("--memory", type=int, default=DEFAULT_MEMORY_MB, help="Runner RAM in MB")
        cmd.add_argument("--disk", type=int, default=DEFAULT_DISK_GB, help="Runner disk in GB")
        cmd.add_argument("--bridge", default="vmbr0")
        cmd.add_argument("--vlan", type=int, default=0)
        cmd.add_argument("--cpuunits", type=int, default=0, help="CPU weight (0 = Proxmox default)")
        cmd.add_argument("--ssh-key-file", default="",
                         help="Public key file for the runner's login (default: a key is made on this host, see /root/.ssh)")
        cmd.add_argument("--gpu-pci", default="", help="Pass this free host GPU to the runner (default off)")
        cmd.add_argument("--source-url", default=DEFAULT_SOURCE_URL,
                         help="Archive of this repository ({ref} is replaced)")
        cmd.add_argument("--source-ref", default=DEFAULT_SOURCE_REF,
                         help="Release tag or full commit id the runner builds from")
        cmd.add_argument("--source-sha256", default=DEFAULT_SOURCE_SHA256,
                         help="sha256 of the archive at the ref (the runner refuses any other)")
        cmd.add_argument("--renderer-url", default="",
                         help="Renderer tarball to build from instead of the release one (with --renderer-sha256)")
        cmd.add_argument("--renderer-sha256", default="")
        cmd.add_argument("--ipsw", default="", help="Path on the runner to the IPSW (default: fetch 26B131 inside the runner)")
        cmd.add_argument("--aux", default="", help="Path on the runner to the aux image the restore boots with")
        cmd.add_argument("--ecid", default="", help="Machine uuid (ECID) the restore is personalized for, decimal")
        cmd.add_argument("--disk-target", default="", help="Path on the runner for the raw disk the install writes")
        cmd.add_argument("--ipsw-file", default="", help="IPSW on this Proxmox host, uploaded to the runner")
        cmd.add_argument("--bundle-file", default="", help="Ambrosia restore bundle on this Proxmox host, uploaded")
        cmd.add_argument("--ramdisk-file", default="", help="Restore ramdisk im4p on this Proxmox host, uploaded")
        cmd.add_argument("--restore-ambrosia-file", default="", help="Ambrosia restore build on this Proxmox host, uploaded")
        cmd.add_argument("--cache-dir", default="", help="TSS cache directory on this Proxmox host, uploaded")
        cmd.add_argument("--aux-file", default="", help="Aux image on this Proxmox host, uploaded to the runner")
        cmd.add_argument("--kdk-dir", default="", help="KDK directory on this Proxmox host, uploaded to the runner")
        cmd.add_argument("--template-file", default="", help="Golden LocalPolicy img4 on this Proxmox host, uploaded")
        cmd.add_argument("--user", default=DEFAULT_GUEST_USER,
                         help="First macOS account made by firstboot (default snap)")
        cmd.add_argument("--password-file", default="", help="Password for that account on this Proxmox host, uploaded")
        if name == "plan":
            cmd.add_argument("--json", action="store_true", help="Print the plan as JSON")
        else:
            cmd.add_argument("--execute", action="store_true", help="Actually run (default is dry run)")
    rm = actions.add_parser("uninstall", help="Destroy the runner VM and what it left on the host")
    rm.add_argument("--vmid", type=int, required=True)
    rm.add_argument("--execute", action="store_true", help="Actually run (default is dry run)")


def _config(args: argparse.Namespace) -> Arm64Config:
    key = ""
    if args.ssh_key_file:
        key = Path(args.ssh_key_file).read_text(encoding="utf-8").strip()
    return Arm64Config(
        vmid=args.vmid, name=args.name, storage=args.storage, cores=args.cores, memory_mb=args.memory,
        disk_gb=args.disk, bridge=args.bridge, vlan=args.vlan, cpu_units=args.cpuunits, ssh_public_key=key,
        gpu_pci=args.gpu_pci, source_url=args.source_url, source_ref=args.source_ref,
        source_sha256=args.source_sha256, renderer_url=args.renderer_url, renderer_sha256=args.renderer_sha256,
        ipsw=args.ipsw, aux=args.aux, ecid=args.ecid, disk_target=args.disk_target,
        ipsw_file=args.ipsw_file, bundle_file=args.bundle_file, ramdisk_file=args.ramdisk_file,
        restore_ambrosia_file=args.restore_ambrosia_file, cache_dir=args.cache_dir, aux_file=args.aux_file, kdk_dir=args.kdk_dir,
        template_file=args.template_file, guest_user=args.user,
        password_file=args.password_file,
    )


def _run_steps(steps: list, execute: bool) -> int:
    result = apply_plan(steps, execute=execute)
    for item in result.results:
        print(f"{'ok  ' if item.ok else 'FAIL'} {item.title}")
        if not item.ok:
            print(item.output)
    print(f"Log: {result.log_path}")
    return 0 if result.ok else 1


def _print_plan(steps: list, as_json: bool) -> None:
    if as_json:
        print(json.dumps([{"title": s.title, "argv": s.argv, "risk": s.risk} for s in steps], indent=2))
        return
    for idx, step in enumerate(steps, start=1):
        detail = step.command if step.argv[0] != "bash" else f"bash script ({len(step.argv) - 4} arguments)"
        print(f"{idx:>2}. {step.title}\n    {detail}")


def run(args: argparse.Namespace) -> int:
    if args.arm64_cmd == "uninstall":
        if not MIN_VMID <= args.vmid <= MAX_VMID:
            print(f"ERROR: VMID must be between {MIN_VMID} and {MAX_VMID}.")
            return 2
        return _run_steps(build_arm64_destroy_plan(args.vmid), args.execute)
    try:
        config = _config(args)
    except OSError as exc:
        print(f"ERROR: cannot read the key file: {exc}")
        return 2
    issues = validate_arm64_config(config)
    if issues:
        print("\n".join(f"ERROR: {issue}" for issue in issues))
        return 2
    for label, path, want_dir in (("IPSW file", config.ipsw_file, False), ("Restore bundle", config.bundle_file, False),
                                  ("Restore ramdisk", config.ramdisk_file, False),
                                  ("Restore Ambrosia", config.restore_ambrosia_file, False),
                                  ("Aux file", config.aux_file, False), ("KDK directory", config.kdk_dir, True),
                                  ("TSS cache", config.cache_dir, True),
                                  ("Policy template", config.template_file, False), ("Password file", config.password_file, False)):
        if not path:
            continue
        if want_dir and not Path(path).is_dir():
            print(f"ERROR: {label} does not exist or is not a directory: {path}")
            return 2
        if not want_dir and not Path(path).is_file():
            print(f"ERROR: {label} does not exist or is not a file: {path}")
            return 2
    steps = build_arm64_plan(config)
    if args.arm64_cmd == "plan":
        _print_plan(steps, args.json)
        return 0
    return _run_steps(steps, args.execute)
