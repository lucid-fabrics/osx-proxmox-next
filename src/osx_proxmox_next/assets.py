from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from .domain import VmConfig


@dataclass
class AssetCheck:
    name: str
    path: Path
    ok: bool
    hint: str
    downloadable: bool = False


def required_assets(config: VmConfig) -> list[AssetCheck]:
    iso_root = Path("/var/lib/vz/template/iso")
    checks: list[AssetCheck] = []
    opencore_path = resolve_opencore_path(config.macos)

    checks.append(
        AssetCheck(
            name="OpenCore image",
            path=opencore_path,
            ok=opencore_path.exists(),
            hint="Provide OpenCore ISO before apply mode.",
            downloadable=True,
        )
    )

    recovery_path = resolve_recovery_or_installer_path(config)
    checks.append(
        AssetCheck(
            name="Installer / recovery image",
            path=recovery_path,
            ok=recovery_path.exists(),
            hint="Tahoe should use a full installer image path.",
            downloadable=(config.macos != "tahoe"),
        )
    )
    return checks


def suggested_fetch_commands(config: VmConfig) -> list[str]:
    iso_root = "/var/lib/vz/template/iso"
    if config.macos == "tahoe":
        commands = [
            f"# OpenCore auto-download: osx-next-cli download --macos {config.macos} --opencore-only",
            f"# Or manually place OpenCore image at {iso_root}/opencore-{config.macos}.iso",
            "# Tahoe: provide a full installer image and set installer_path",
        ]
    else:
        commands = [
            f"# Auto-download available — run: osx-next-cli download --macos {config.macos}",
            f"# Or manually place OpenCore image at {iso_root}/opencore-{config.macos}.iso",
            f"# Or place recovery image at {iso_root}/{config.macos}-recovery.iso",
        ]
    return commands


def resolve_opencore_path(macos: str) -> Path:
    match = _find_iso(
        [
            "OpenCore-v21.iso",
            "opencore-v21.iso",
            "opencore-osx-proxmox-vm.iso",
            f"opencore-{macos}.iso",
            f"opencore*{macos}*.iso",
            "opencore*.iso",
        ]
    )
    if match:
        return match
    return Path("/var/lib/vz/template/iso") / f"opencore-{macos}.iso"


def resolve_recovery_or_installer_path(config: VmConfig) -> Path:
    if config.installer_path:
        return Path(config.installer_path)
    if config.macos == "tahoe":
        match = _find_iso(["*tahoe*full*.iso", "*tahoe*.iso", "*26*.iso", "*InstallAssistant*.iso"])
        if match:
            return match
    match = _find_iso([
        f"{config.macos}-recovery.iso",
        f"{config.macos}-recovery.img",
        f"{config.macos}-recovery.dmg",
    ])
    if match:
        return match
    return Path("/var/lib/vz/template/iso") / f"{config.macos}-recovery.iso"


def _find_iso(patterns: list[str]) -> Path | None:
    roots = [
        Path("/var/lib/vz/template/iso"),
        Path("/root/OSX-PROXMOX/EFI"),
        Path("/root/OSX-PROXMOX-fork/EFI"),
    ]
    mnt_pve = Path("/mnt/pve")
    if mnt_pve.exists():
        for entry in sorted(mnt_pve.iterdir()):
            roots.append(entry / "template" / "iso")
    for root in roots:
        if not root.exists():
            continue
        lowered = [p.lower() for p in patterns]
        for candidate in sorted(root.iterdir()):
            if not candidate.is_file():
                continue
            name = candidate.name.lower()
            if any(fnmatch(name, pattern) for pattern in lowered):
                return candidate
    return None
