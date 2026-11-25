"""CLI helpers shared by cli.py and the subcommand modules (igpu_cli).

They live below both so neither has to import the other."""
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from .assets import required_assets, suggested_fetch_commands
from .defaults import detect_cpu_info, detect_iso_storage, detect_net_model
from .domain import VmConfig, validate_config
from .downloader import ensure_restrictevents
from .services import run_download_worker


def config_from_args(args: argparse.Namespace) -> VmConfig:
    return VmConfig(
        vmid=args.vmid,
        name=args.name,
        macos=args.macos,
        cores=args.cores,
        memory_mb=args.memory,
        disk_gb=args.disk,
        bridge=args.bridge,
        vlan=getattr(args, "vlan", 0) or 0,
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


def auto_download_missing(config: VmConfig, dest_dir: Path) -> None:
    assets = required_assets(config)
    missing = [a for a in assets if (not a.ok or a.stale) and a.downloadable]
    if not missing:
        # ISO may be cached from an older version; still make sure the
        # RestrictEvents kext zip is available for the OpenCore disk build.
        ensure_restrictevents(dest_dir)
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


def validate_and_fetch_assets(args: argparse.Namespace, config: VmConfig) -> int | None:
    """Validate config and ensure required assets exist. Returns error code or None."""
    issues = validate_config(config)
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}")
        return 2

    assets = required_assets(config)
    missing = [a for a in assets if not a.ok]
    stale = any(a.stale for a in assets)

    if (missing or stale) and not getattr(args, "no_download", False):
        dest_dir = Path(config.iso_dir) if config.iso_dir else Path(detect_iso_storage()[0])
        auto_download_missing(config, dest_dir)
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
