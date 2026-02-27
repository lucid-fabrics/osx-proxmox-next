from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .assets import required_assets, suggested_fetch_commands
from .defaults import DEFAULT_ISO_DIR, detect_cpu_info, detect_iso_storage
from .diagnostics import export_log_bundle, recovery_guide
from .domain import VmConfig, validate_config
from .downloader import DownloadError, DownloadProgress, download_opencore, download_recovery
from .executor import apply_plan
from .planner import build_plan, build_destroy_plan, fetch_vm_info, render_script
from .preflight import run_preflight
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

    for asset in missing:
        if "OpenCore" in asset.name:
            print(f"Downloading OpenCore image for {config.macos}...")
            try:
                path = download_opencore(config.macos, dest_dir, on_progress=_cli_progress)
                print(f"\nDownloaded: {path}")
            except DownloadError as exc:
                print(f"\nOpenCore download failed: {exc}")
        elif "recovery" in asset.name.lower() or "installer" in asset.name.lower():
            print(f"Downloading recovery image for {config.macos}...")
            try:
                path = download_recovery(config.macos, dest_dir, on_progress=_cli_progress)
                print(f"\nDownloaded: {path}")
            except DownloadError as exc:
                print(f"\nRecovery download failed: {exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="osx-next-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("preflight")
    sub.add_parser("bundle")

    guide = sub.add_parser("guide")
    guide.add_argument("reason", nargs="?", default="boot issue")

    # Download subcommand
    dl = sub.add_parser("download", help="Download OpenCore ISOs and macOS recovery images")
    dl.add_argument("--macos", type=str, required=True, help="macOS target (ventura, sonoma, sequoia, tahoe)")
    dl.add_argument("--dest", type=str, default="/var/lib/vz/template/iso", help="Destination directory")
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

    plan = sub.add_parser("plan", parents=[common])
    plan.add_argument("--script-out", type=str, default="")

    apply_cmd = sub.add_parser("apply", parents=[common])
    apply_cmd.add_argument("--execute", action="store_true")

    # Uninstall subcommand
    uninstall = sub.add_parser("uninstall", help="Destroy an existing macOS VM")
    uninstall.add_argument("--vmid", type=int, required=True, help="VM ID to destroy")
    uninstall.add_argument("--purge", action="store_true", help="Also delete all disk images")
    uninstall.add_argument("--execute", action="store_true", help="Actually run (default is dry run)")

    return parser


def run_cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "preflight":
        for check in run_preflight():
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
        for idx, step in enumerate(steps, start=1):
            print(f"{idx:02d}. {step.title}")
            print(f"    {step.command}")
        if args.script_out:
            out = Path(args.script_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render_script(config, steps), encoding="utf-8")
            print(f"Script written: {out}")
        return 0

    snapshot = create_snapshot(config.vmid)
    result = apply_plan(steps, execute=bool(args.execute))
    if result.ok:
        print(f"Apply OK. Log: {result.log_path}")
        print()
        print("If this saved you time: https://ko-fi.com/lucidfabrics | https://buymeacoffee.com/lucidfabrics")
        return 0

    print(f"Apply FAILED. Log: {result.log_path}")
    for hint in rollback_hints(snapshot):
        print(f"ROLLBACK: {hint}")
    return 4


def _run_uninstall(args: argparse.Namespace) -> int:
    vmid = args.vmid
    if vmid < 100 or vmid > 999999:
        print("ERROR: VMID must be between 100 and 999999.")
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
