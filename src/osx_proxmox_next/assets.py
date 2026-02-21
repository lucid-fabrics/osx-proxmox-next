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
    checks: list[AssetCheck] = []
    extra_dirs = [Path(config.iso_dir)] if config.iso_dir else []
    opencore_path = resolve_opencore_path(config.macos, extra_dirs=extra_dirs)

    checks.append(
        AssetCheck(
            name="OpenCore image",
            path=opencore_path,
            ok=opencore_path.exists(),
            hint="Provide OpenCore ISO before apply mode.",
            downloadable=True,
        )
    )

    recovery_path = resolve_recovery_or_installer_path(config, extra_dirs=extra_dirs)
    checks.append(
        AssetCheck(
            name="Recovery image",
            path=recovery_path,
            ok=recovery_path.exists(),
            hint="Provide recovery image or run auto-download.",
            downloadable=True,
        )
    )
    return checks


def suggested_fetch_commands(config: VmConfig) -> list[str]:
    iso_root = config.iso_dir or "/var/lib/vz/template/iso"
    return [
        f"# Auto-download available — run: osx-next-cli download --macos {config.macos}",
        f"# Or manually place OpenCore image at {iso_root}/opencore-{config.macos}.iso",
        f"# Or place recovery image at {iso_root}/{config.macos}-recovery.iso",
    ]


def resolve_opencore_path(macos: str, extra_dirs: list[Path] | None = None) -> Path:
    match = _find_iso(
        [
            "opencore-osx-proxmox-vm.iso",
            f"opencore-{macos}.iso",
            f"opencore-{macos}-*.iso",
        ],
        extra_dirs=extra_dirs,
    )
    if match:
        return match
    return Path("/var/lib/vz/template/iso") / "opencore-osx-proxmox-vm.iso"


def resolve_recovery_or_installer_path(
    config: VmConfig, extra_dirs: list[Path] | None = None,
) -> Path:
    if config.installer_path:
        return Path(config.installer_path)
    match = _find_iso(
        [
            f"{config.macos}-recovery.iso",
            f"{config.macos}-recovery.img",
            f"{config.macos}-recovery.dmg",
        ],
        extra_dirs=extra_dirs,
    )
    if match:
        return match
    return Path("/var/lib/vz/template/iso") / f"{config.macos}-recovery.iso"


def _find_iso(
    patterns: list[str], extra_dirs: list[Path] | None = None,
) -> Path | None:
    roots = [
        Path("/var/lib/vz/template/iso"),
    ]
    if extra_dirs:
        for d in extra_dirs:
            if d not in roots:
                roots.append(d)
    mnt_pve = Path("/mnt/pve")
    if mnt_pve.exists():
        for entry in sorted(mnt_pve.iterdir()):
            roots.append(entry / "template" / "iso")
    # Try patterns in priority order so exact names match before globs
    lowered = [p.lower() for p in patterns]
    for pattern in lowered:
        for root in roots:
            if not root.exists():
                continue
            for candidate in sorted(root.iterdir()):
                if not candidate.is_file():
                    continue
                if fnmatch(candidate.name.lower(), pattern):
                    return candidate
    return None
