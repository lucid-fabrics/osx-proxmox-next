from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from . import __version__
from .assets import required_assets, suggested_fetch_commands
from .defaults import DEFAULT_ISO_DIR, detect_cpu_info, detect_iso_storage, detect_net_model
from .diagnostics import export_log_bundle, recovery_guide
from .domain import MIN_VMID, MAX_VMID, SUPPORTED_MACOS, VmConfig, EditChanges, validate_config, validate_edit_changes
from .downloader import DownloadError, DownloadProgress, download_opencore, download_recovery
from .executor import apply_plan
from .planner import build_plan, build_destroy_plan, build_edit_plan, build_clone_plan
from .services import fetch_vm_info, get_proxmox_adapter, run_download_worker
from .script_renderer import render_script
from .preflight import run_preflight, has_missing_build_deps, install_missing_packages
from .rollback import create_snapshot, rollback_hints

_MB = 1024 * 1024


def _config_from_args(args: argparse.Namespace) -> VmConfig:
    return VmConfig(
        vmid=args.vmid,
        name=args.name,
        macos=args.macos,
        cores=args.cores,
        memory_mb=args.memory,
        disk_gb=args.disk,
        bridge=args.bridge,
        storage=args.storage,
        installer_path=args.installer_path or "",
        smbios_serial=args.smbios_serial or "",
        smbios_uuid=args.smbios_uuid or "",
        smbios_mlb=args.smbios_mlb or "",
        smbios_rom=args.smbios_rom or "",
        smbios_model=args.smbios_model or "",
        no_smbios=args.no_smbios,
        apple_services=args.apple_services,
        verbose_boot=args.verbose_boot,
        iso_dir=getattr(args, "iso_dir", "") or "",
        cpu_model=getattr(args, "cpu_model", "") or "",
        net_model=getattr(args, "net_model", "") or detect_net_model(detect_cpu_info()),
    )


def _cli_progress(p: DownloadProgress) -> None:
    mb_down = p.downloaded / _MB
    if p.total > 0:
        mb_total = p.total / _MB
        pct = int(p.downloaded * 100 / p.total)
        sys.stdout.write(f"\r[{p.phase}] {mb_down:.1f}/{mb_total:.1f} MB ({pct}%)")
    else:
        sys.stdout.write(f"\r[{p.phase}] {mb_down:.1f} MB")
    sys.stdout.flush()


def _auto_download_missing(config: VmConfig, dest_dir: Path) -> None:
    assets = required_assets(config)
    missing = [a for a in assets if not a.ok and a.downloadable]
    if not missing:
        return

    config_with_dir = config if config.iso_dir else \
        dataclasses.replace(config, iso_dir=str(dest_dir))

    def _on_progress(phase: str, pct: int) -> None:
        sys.stdout.write(f"\r[{phase}] {pct}%")
        sys.stdout.flush()

    errors = run_download_worker(config_with_dir, missing, on_progress=_on_progress)
    print()
    for err in errors:
        print(f"Download failed: {err}")


def _build_common_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vmid", type=int, required=True)
    common.add_argument("--name", type=str, required=True)
    common.add_argument("--macos", type=str, required=True)
    common.add_argument("--cores", type=int, required=True)
    common.add_argument("--memory", type=int, required=True)
    common.add_argument("--disk", type=int, required=True)
    common.add_argument("--bridge", type=str, required=True)
    common.add_argument("--storage", type=str, required=True)
    common.add_argument("--installer-path", type=str, default="")
    common.add_argument("--smbios-serial", type=str, default="")
    common.add_argument("--smbios-uuid", type=str, default="")
    common.add_argument("--smbios-mlb", type=str, default="")
    common.add_argument("--smbios-rom", type=str, default="")
    common.add_argument("--smbios-model", type=str, default="")
    common.add_argument("--no-smbios", action="store_true", default=False)
    common.add_argument("--apple-services", action="store_true", default=False,
                        help="Configure for Apple services (iMessage, FaceTime, iCloud). Adds vmgenid and static MAC.")
    common.add_argument("--no-download", action="store_true", default=False,
                        help="Skip auto-download of missing assets")
    common.add_argument("--verbose-boot", action="store_true", default=False,
                        help="Show verbose kernel log instead of Apple logo during boot")
    common.add_argument("--iso-dir", type=str, default="",
                        help="Directory for ISO/recovery images (default: auto-detect)")
    common.add_argument("--cpu-model", type=str, default="",
                        help="Override QEMU CPU model (e.g. Skylake-Server-IBRS). Default: auto-detect")
    common.add_argument("--net-model", type=str, default="",
                        help="NIC model: vmxnet3 (default) or e1000-82545em (recommended for Xeon/older Intel). Default: auto-detect")
    return common


def _add_simple_subparsers(sub: argparse._SubParsersAction) -> None:
    sub.add_parser("preflight")
    sub.add_parser("bundle")
    guide = sub.add_parser("guide")
    guide.add_argument("reason", nargs="?", default="boot issue")


def _add_download_subparser(sub: argparse._SubParsersAction) -> None:
    dl = sub.add_parser("download", help="Download OpenCore ISOs and macOS recovery images")
    dl.add_argument("--macos", type=str, required=True, help="macOS target (ventura, sonoma, sequoia, tahoe)")
    dl.add_argument("--dest", type=str, default=DEFAULT_ISO_DIR, help="Destination directory")
    dl.add_argument("--opencore-only", action="store_true", help="Only download OpenCore ISO")
    dl.add_argument("--recovery-only", action="store_true", help="Only download recovery image")


def _add_vm_subparsers(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    plan = sub.add_parser("plan", parents=[common])
    plan.add_argument("--script-out", type=str, default="")
    plan.add_argument("--json", action="store_true", default=False,
                      help="Output plan as JSON instead of human-readable text")

    apply_cmd = sub.add_parser("apply", parents=[common])
    apply_cmd.add_argument("--execute", action="store_true")

    status = sub.add_parser("status", help="Show info about an existing macOS VM")
    status.add_argument("--vmid", type=int, required=True, help="VM ID to query")

    uninstall = sub.add_parser("uninstall", help="Destroy an existing macOS VM")
    uninstall.add_argument("--vmid", type=int, required=True, help="VM ID to destroy")
    uninstall.add_argument("--purge", action="store_true", help="Also delete all disk images")
    uninstall.add_argument("--execute", action="store_true", help="Actually run (default is dry run)")

    edit = sub.add_parser("edit", help="Modify an existing macOS VM (stops VM, applies changes)")
    edit.add_argument("--vmid", type=int, required=True, help="VM ID to edit")
    edit.add_argument("--name", type=str, default=None, help="New VM name")
    edit.add_argument("--cores", type=int, default=None, help="New CPU core count")
    edit.add_argument("--memory", type=int, default=None, help="New memory in MB")
    edit.add_argument("--bridge", type=str, default=None, help="New network bridge (e.g. vmbr1)")
    edit.add_argument("--add-disk", type=int, default=None, dest="disk_gb_add",
                      help="Extend the target disk by N GB")
    edit.add_argument("--disk-name", type=str, default="virtio0", dest="disk_name",
                      help="Disk device to resize (default: virtio0)")
    edit.add_argument("--nic-model", type=str, default=None, dest="nic_model",
                      help="NIC model to use when updating bridge (default: preserve existing)")
    edit.add_argument("--start", action="store_true", default=False,
                      help="Start VM after applying changes")
    edit.add_argument("--execute", action="store_true", help="Actually run (default is dry run)")

    clone = sub.add_parser("clone", help="Clone a macOS VM with a fresh SMBIOS identity")
    clone.add_argument("--source-vmid", type=int, required=True, dest="source_vmid",
                       help="VM ID to clone from")
    clone.add_argument("--new-vmid", type=int, required=True, dest="new_vmid",
                       help="VM ID for the clone")
    clone.add_argument("--name", type=str, default=None,
                       help="Name for the cloned VM (default: Proxmox auto-generates)")
    clone.add_argument("--macos", type=str, default="sequoia",
                       help="macOS version hint for SMBIOS model selection (default: sequoia)")
    clone.add_argument("--no-apple-services", action="store_true", default=False,
                       dest="no_apple_services",
                       help="Skip vmgenid and MAC regeneration (not recommended — breaks Apple services isolation)")
    clone.add_argument("--execute", action="store_true",
                       help="Actually run (default is dry run)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="osx-next-cli")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    _add_simple_subparsers(sub)
    _add_download_subparser(sub)
    common = _build_common_parser()
    _add_vm_subparsers(sub, common)
    return parser


def _handle_list_command(args: argparse.Namespace, config: VmConfig, steps: list) -> int:
    """Render the plan as JSON or human-readable text."""
    if getattr(args, "json", False):
        plan_data = [
            {"step": idx, "title": step.title, "command": step.command, "risk": step.risk}
            for idx, step in enumerate(steps, start=1)
        ]
        print(json.dumps(plan_data, indent=2))
    else:
        for idx, step in enumerate(steps, start=1):
            print(f"{idx:02d}. {step.title}")
            print(f"    {step.command}")
    return 0


def _handle_script_command(args: argparse.Namespace, config: VmConfig, steps: list) -> None:
    """Write the plan as a shell script if --script-out is set."""
    if args.script_out:
        out = Path(args.script_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_script(config, steps), encoding="utf-8")
        print(f"Script written: {out}")


def _handle_apply_command(args: argparse.Namespace, config: VmConfig, steps: list) -> int:
    """Execute the plan and report apply result."""
    snapshot = create_snapshot(config.vmid)
    result = apply_plan(steps, execute=bool(args.execute))
    if result.ok:
        print(f"Apply OK. Log: {result.log_path}")
        print()
        print("POST-INSTALL: After macOS finishes installing, fix the boot order")
        print("so the main disk boots first (instead of recovery):")
        print(f"  qm set {config.vmid} --boot order=virtio0;ide0")
        print()
        print("If this saved you time: https://ko-fi.com/lucidfabrics | https://buymeacoffee.com/lucidfabrics")
        return 0

    print(f"Apply FAILED. Log: {result.log_path}")
    for hint in rollback_hints(snapshot):
        print(f"ROLLBACK: {hint}")
    return 4


def _dispatch_simple_commands(args: argparse.Namespace) -> int | None:
    """Handle commands that don't need VM config. Returns exit code or None to continue."""
    if args.cmd == "preflight":
        checks = run_preflight()
        if has_missing_build_deps(checks):
            ok, pkgs = install_missing_packages(on_output=lambda msg: print(f"  -> {msg}"))
            if ok and pkgs:
                checks = run_preflight()
        for check in checks:
            print(f"{'OK' if check.ok else 'FAIL'} {check.name}: {check.details}")
        return 0
    if args.cmd == "bundle":
        print(export_log_bundle())
        return 0
    if args.cmd == "guide":
        for line in recovery_guide(args.reason):
            print(line)
        return 0
    if args.cmd == "download":
        return _run_download(args)
    if args.cmd == "status":
        return _run_status(args)
    if args.cmd == "uninstall":
        return _run_uninstall(args)
    if args.cmd == "edit":
        return _run_edit(args)
    if args.cmd == "clone":
        return _run_clone(args)
    return None


def _validate_and_fetch_assets(args: argparse.Namespace, config: VmConfig) -> int | None:
    """Validate config and ensure required assets exist. Returns error code or None."""
    issues = validate_config(config)
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}")
        return 2

    assets = required_assets(config)
    missing = [a for a in assets if not a.ok]

    if missing and not getattr(args, "no_download", False):
        dest_dir = Path(config.iso_dir) if config.iso_dir else Path(detect_iso_storage()[0])
        _auto_download_missing(config, dest_dir)
        # Re-check after download
        assets = required_assets(config)
        missing = [a for a in assets if not a.ok]

    if missing:
        for item in missing:
            print(f"MISSING: {item.name}: {item.path}")
        for cmd in suggested_fetch_commands(config):
            print(cmd)
        return 3

    return None


def _print_cpu_info(args: argparse.Namespace, config: VmConfig) -> None:
    cpu = detect_cpu_info()
    json_mode = args.cmd == "plan" and getattr(args, "json", False)
    if not json_mode:
        if config.cpu_model:
            cpu_mode = f"override: {config.cpu_model}"
        elif cpu.needs_emulated_cpu:
            cpu_mode = "Cascadelake-Server emulation"
        else:
            cpu_mode = "native host passthrough"
        cpu_label = cpu.model_name or cpu.vendor
        print(f"CPU: {cpu_label} ({cpu_mode})")


def run_cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    rc = _dispatch_simple_commands(args)
    if rc is not None:
        return rc

    config = _config_from_args(args)
    rc = _validate_and_fetch_assets(args, config)
    if rc is not None:
        return rc

    _print_cpu_info(args, config)
    steps = build_plan(config)

    if args.cmd == "plan":
        rc = _handle_list_command(args, config, steps)
        _handle_script_command(args, config, steps)
        return rc

    return _handle_apply_command(args, config, steps)


def _run_status(args: argparse.Namespace) -> int:
    vmid = args.vmid
    if vmid < MIN_VMID or vmid > MAX_VMID:
        print(f"ERROR: VMID must be between {MIN_VMID} and {MAX_VMID}.")
        return 2

    info = fetch_vm_info(vmid, adapter=get_proxmox_adapter())
    if info is None:
        print(f"ERROR: VM {vmid} not found.")
        return 2

    print(f"VM {vmid}: {info.name}")
    print(f"Status: {info.status}")
    if info.config_raw:
        for line in info.config_raw.splitlines():
            key = line.split(":")[0].strip()
            if key in ("cores", "memory", "balloon", "net0", "smbios1", "cpu", "machine"):
                print(f"  {line.strip()}")
    return 0


def _run_uninstall(args: argparse.Namespace) -> int:
    vmid = args.vmid
    if vmid < MIN_VMID or vmid > MAX_VMID:
        print(f"ERROR: VMID must be between {MIN_VMID} and {MAX_VMID}.")
        return 2

    if args.execute:
        info = fetch_vm_info(vmid, adapter=get_proxmox_adapter())
        if info is None:
            print(f"ERROR: VM {vmid} not found.")
            return 2
        print(f"VM {vmid}: {info.name} ({info.status})")
        snapshot = create_snapshot(vmid)
        print(f"Snapshot saved: {snapshot.path}")
    else:
        print(f"Target: VM {vmid}")

    steps = build_destroy_plan(vmid, purge=args.purge)

    if not args.execute:
        for idx, step in enumerate(steps, start=1):
            print(f"{idx:02d}. {step.title}")
            print(f"    {step.command}")
        return 0

    result = apply_plan(steps, execute=True)
    if result.ok:
        print(f"Destroy OK. Log: {result.log_path}")
        return 0

    print(f"Destroy FAILED. Log: {result.log_path}")
    return 6


def _run_download(args: argparse.Namespace) -> int:
    macos = args.macos
    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    ok = True

    if not args.recovery_only:
        print(f"Downloading OpenCore image for {macos}...")
        try:
            path = download_opencore(macos, dest_dir, on_progress=_cli_progress)
            print(f"\nDownloaded: {path}")
        except DownloadError as exc:
            print(f"\nOpenCore download failed: {exc}")
            ok = False

    if not args.opencore_only:
        print(f"Downloading recovery image for {macos}...")
        try:
            path = download_recovery(macos, dest_dir, on_progress=_cli_progress)
            print(f"\nDownloaded: {path}")
        except DownloadError as exc:
            print(f"\nRecovery download failed: {exc}")
            ok = False

    return 0 if ok else 5


def _run_edit(args: argparse.Namespace) -> int:
    vmid = args.vmid
    if vmid < MIN_VMID or vmid > MAX_VMID:
        print(f"ERROR: VMID must be between {MIN_VMID} and {MAX_VMID}.")
        return 2

    changes = EditChanges(
        name=args.name,
        cores=args.cores,
        memory_mb=args.memory,
        bridge=args.bridge,
        disk_gb_add=args.disk_gb_add,
        nic_model=args.nic_model,
        disk_name=args.disk_name,
    )

    issues = validate_edit_changes(vmid, changes)
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}")
        return 2

    current_net0 = None
    if args.execute:
        info = fetch_vm_info(vmid, adapter=get_proxmox_adapter())
        if info is None:
            print(f"ERROR: VM {vmid} not found.")
            return 2
        print(f"VM {vmid}: {info.name} ({info.status})")
        snapshot = create_snapshot(vmid)
        print(f"Snapshot saved: {snapshot.path}")
        current_net0 = info.config_raw

    steps = build_edit_plan(vmid, changes, start_after=args.start, current_net0=current_net0)

    if not args.execute:
        print("DRY RUN — pass --execute to apply:\n")

    for idx, step in enumerate(steps, start=1):
        print(f"{idx:02d}. {step.title}")
        print(f"    {step.command}")

    if not args.execute:
        return 0

    result = apply_plan(steps, execute=True)
    if result.ok:
        print(f"Edit OK. Log: {result.log_path}")
        return 0

    print(f"Edit FAILED. Log: {result.log_path}")
    return 7


def _run_clone(args: argparse.Namespace) -> int:
    src_vmid = args.source_vmid
    dst_vmid = args.new_vmid

    if src_vmid < MIN_VMID or src_vmid > MAX_VMID:
        print(f"ERROR: Source VMID must be between {MIN_VMID} and {MAX_VMID}.")
        return 2
    if dst_vmid < MIN_VMID or dst_vmid > MAX_VMID:
        print(f"ERROR: New VMID must be between {MIN_VMID} and {MAX_VMID}.")
        return 2
    if src_vmid == dst_vmid:
        print("ERROR: Source and destination VMID must differ.")
        return 2

    if args.macos not in SUPPORTED_MACOS:
        supported = ", ".join(SUPPORTED_MACOS)
        print(f"ERROR: --macos must be one of: {supported}.")
        return 2

    if args.name is not None:
        import re as _re
        if len(args.name) < 3 or len(args.name) > 63:
            print("ERROR: VM name must be between 3 and 63 characters.")
            return 2
        if not _re.fullmatch(r"[a-zA-Z0-9]([a-zA-Z0-9.\-]*[a-zA-Z0-9])?", args.name):
            print("ERROR: VM name must start with alphanumeric and contain only [a-zA-Z0-9.-].")
            return 2

    current_net0 = None
    if args.execute:
        info = fetch_vm_info(src_vmid, adapter=get_proxmox_adapter())
        if info is None:
            print(f"ERROR: VM {src_vmid} not found.")
            return 2
        print(f"Source: VM {src_vmid}: {info.name} ({info.status})")
        current_net0 = info.config_raw

    apple_services = not args.no_apple_services
    steps = build_clone_plan(
        src_vmid=src_vmid,
        dst_vmid=dst_vmid,
        new_name=args.name,
        macos=args.macos,
        apple_services=apple_services,
        current_net0=current_net0,
    )

    if not args.execute:
        print("DRY RUN — pass --execute to apply:\n")

    for idx, step in enumerate(steps, start=1):
        print(f"{idx:02d}. {step.title}")
        print(f"    {step.command}")

    if not args.execute:
        return 0

    result = apply_plan(steps, execute=True)
    if result.ok:
        print(f"\nClone OK. Log: {result.log_path}")
        print(f"VM {dst_vmid} is ready with a fresh SMBIOS identity.")
        if apple_services:
            print("Apple services (iMessage, FaceTime, iCloud) are isolated from the source VM.")
        return 0

    print(f"\nClone FAILED. Log: {result.log_path}")
    return 8


if __name__ == "__main__":
    raise SystemExit(run_cli())
