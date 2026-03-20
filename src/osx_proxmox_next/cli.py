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
from .domain import MIN_VMID, MAX_VMID, VmConfig, validate_config
from .downloader import DownloadError, DownloadProgress, download_opencore, download_recovery
from .executor import apply_plan
from .services.download_service import run_download_worker
from .planner import build_plan, build_destroy_plan, fetch_vm_info
from .script_renderer import render_script
from .preflight import run_preflight, has_missing_build_deps, install_missing_packages
from .rollback import create_snapshot, rollback_hints


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
    mb_down = p.downloaded / (1024 * 1024)
    if p.total > 0:
        mb_total = p.total / (1024 * 1024)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="osx-next-cli")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("preflight")
    sub.add_parser("bundle")

    guide = sub.add_parser("guide")
    guide.add_argument("reason", nargs="?", default="boot issue")

    # Download subcommand
    dl = sub.add_parser("download", help="Download OpenCore ISOs and macOS recovery images")
    dl.add_argument("--macos", type=str, required=True, help="macOS target (ventura, sonoma, sequoia, tahoe)")
    dl.add_argument("--dest", type=str, default=DEFAULT_ISO_DIR, help="Destination directory")
    dl.add_argument("--opencore-only", action="store_true", help="Only download OpenCore ISO")
    dl.add_argument("--recovery-only", action="store_true", help="Only download recovery image")

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

    plan = sub.add_parser("plan", parents=[common])
    plan.add_argument("--script-out", type=str, default="")
    plan.add_argument("--json", action="store_true", default=False,
                      help="Output plan as JSON instead of human-readable text")

    apply_cmd = sub.add_parser("apply", parents=[common])
    apply_cmd.add_argument("--execute", action="store_true")

    # Status subcommand
    status = sub.add_parser("status", help="Show info about an existing macOS VM")
    status.add_argument("--vmid", type=int, required=True, help="VM ID to query")

    # Uninstall subcommand
    uninstall = sub.add_parser("uninstall", help="Destroy an existing macOS VM")
    uninstall.add_argument("--vmid", type=int, required=True, help="VM ID to destroy")
    uninstall.add_argument("--purge", action="store_true", help="Also delete all disk images")
    uninstall.add_argument("--execute", action="store_true", help="Actually run (default is dry run)")

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


def _handle_destroy_command(args: argparse.Namespace, config: VmConfig, steps: list) -> int:
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


def run_cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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

    config = _config_from_args(args)
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

    steps = build_plan(config)

    if args.cmd == "plan":
        rc = _handle_list_command(args, config, steps)
        _handle_script_command(args, config, steps)
        return rc

    return _handle_destroy_command(args, config, steps)


def _run_status(args: argparse.Namespace) -> int:
    vmid = args.vmid
    if vmid < MIN_VMID or vmid > MAX_VMID:
        print(f"ERROR: VMID must be between {MIN_VMID} and {MAX_VMID}.")
        return 2

    info = fetch_vm_info(vmid)
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
        info = fetch_vm_info(vmid)
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


if __name__ == "__main__":
    raise SystemExit(run_cli())
