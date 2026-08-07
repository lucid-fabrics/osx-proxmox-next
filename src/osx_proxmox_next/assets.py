from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from .defaults import DEFAULT_ISO_DIR
from .domain import VmConfig

log = logging.getLogger(__name__)

# Seconds to wait for a directory to answer before treating it as unusable.
# Generous enough for a slow spinning disk, short enough that a handful of dead
# mounts do not stall the CLI noticeably.
_LISTDIR_TIMEOUT = 2.0


def _listdir_or_none(path: Path, timeout: float | None = None) -> list[Path] | None:
    """Sorted directory contents, or None if the path does not answer in time.

    An offline NFS or CIFS mount under /mnt/pve puts any stat or readdir into
    uninterruptible sleep. A plain exists() therefore hangs the whole tool with
    no timeout and no way to kill the process, so a Proxmox host with one dead
    storage cannot run plan or apply at all. Probe on a daemon thread instead:
    if it does not come back, skip that root and carry on. The thread stays
    parked on the dead mount but never blocks interpreter exit.
    """
    # Read the module global at call time so tests can shorten it
    timeout = _LISTDIR_TIMEOUT if timeout is None else timeout
    out: list[list[Path]] = []

    def _work() -> None:
        try:
            out.append(sorted(path.iterdir()))
        except OSError:
            out.append([])

    worker = threading.Thread(target=_work, daemon=True)
    worker.start()
    worker.join(timeout)
    if not out:
        log.warning("Skipping unresponsive path %s (no answer in %.0fs)", path, timeout)
        return None
    return out[0]


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
    iso_root = config.iso_dir or DEFAULT_ISO_DIR
    return [
        f"# Auto-download available - run: osx-next-cli download --macos {config.macos}",
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
    return Path(DEFAULT_ISO_DIR) / "opencore-osx-proxmox-vm.iso"


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
    return Path(DEFAULT_ISO_DIR) / f"{config.macos}-recovery.iso"


def _find_iso(
    patterns: list[str], extra_dirs: list[Path] | None = None,
) -> Path | None:
    roots = [
        Path(DEFAULT_ISO_DIR),
    ]
    if extra_dirs:
        for d in extra_dirs:
            if d not in roots:
                roots.append(d)
    for entry in _listdir_or_none(Path("/mnt/pve")) or []:
        roots.append(entry / "template" / "iso")
    # Listing each root once avoids re-probing a dead mount for every pattern
    listings = [(root, _listdir_or_none(root)) for root in roots]
    # Try patterns in priority order so exact names match before globs
    lowered = [p.lower() for p in patterns]
    for pattern in lowered:
        for _root, entries in listings:
            for candidate in entries or []:
                if not candidate.is_file():
                    continue
                if fnmatch(candidate.name.lower(), pattern):
                    return candidate
    return None
