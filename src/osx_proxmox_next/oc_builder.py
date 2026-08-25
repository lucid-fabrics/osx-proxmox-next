"""Assemble the OpenCore boot ISO locally from pinned upstream releases.

Replaces the prebuilt opencore-osx-proxmox-vm.iso release asset: every
component is downloaded from its upstream project at a pinned version,
verified against a pinned SHA-256, and assembled into the same GPT+ESP
image the rest of the pipeline already consumes. The prebuilt asset
remains as a fallback when assembly is not possible.

Keep the pins and the assembly layout in sync with the bash installer
(scripts/bash/osx-proxmox-next.sh); tests/test_oc_builder.py diffs both.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .downloader import (
    _OPENCORE_UNIVERSAL,
    DownloadError,
    DownloadProgress,
    ProgressCallback,
    _download_file,
    download_opencore,
)

log = logging.getLogger(__name__)

__all__ = [
    "OC_COMPONENTS",
    "ChecksumError",
    "Component",
    "build_opencore_iso",
    "ensure_opencore_iso",
]


class ChecksumError(DownloadError):
    """A pinned component failed SHA-256 verification.

    Deliberately NOT recovered by the prebuilt-ISO fallback: a mismatch is
    the tamper signal, and silently downgrading to the unpinned prebuilt
    image would defeat the verification entirely."""


@dataclass(frozen=True)
class Component:
    version: str
    url: str
    sha256: str

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[1]


_ACID = "https://github.com/acidanthera"

# OcBinaryData has no releases; pin a commit archive instead. GitHub only
# promises checksum stability for release assets, so if this tarball is ever
# regenerated differently the hash check fails loudly and assembly falls
# back to the prebuilt ISO until the pin is bumped.
_OCBD_COMMIT = "e74e533d8f89c1d5014cfb47c185502bf415741f"

OC_COMPONENTS: dict[str, Component] = {
    "OpenCorePkg": Component(
        "1.0.7",
        f"{_ACID}/OpenCorePkg/releases/download/1.0.7/OpenCore-1.0.7-RELEASE.zip",
        "2ffab6ebf58c7aefb0bcb3a1a385d207746823d6dd87d44bd666e1286939943e",
    ),
    "Lilu": Component(
        "1.7.2",
        f"{_ACID}/Lilu/releases/download/1.7.2/Lilu-1.7.2-RELEASE.zip",
        "53967d7dcfaab01023a33df2e969a89522f13d6654a6a56ac4711b62dabf3ab8",
    ),
    "VirtualSMC": Component(
        "1.3.7",
        f"{_ACID}/VirtualSMC/releases/download/1.3.7/VirtualSMC-1.3.7-RELEASE.zip",
        "12f1d379969f926306fa92d94ddbf33b32b31176589dc42089d864a26b31b700",
    ),
    "WhateverGreen": Component(
        "1.7.0",
        f"{_ACID}/WhateverGreen/releases/download/1.7.0/WhateverGreen-1.7.0-RELEASE.zip",
        "6d6ffe8334ad60f784a662794e67b2560b79d757d506841dc8ca9994ab39979b",
    ),
    "AppleALC": Component(
        "1.9.7",
        f"{_ACID}/AppleALC/releases/download/1.9.7/AppleALC-1.9.7-RELEASE.zip",
        "81a8ba79986130e8c845fff595950226cbc30e588f8d37089e467f776469c29d",
    ),
    "CryptexFixup": Component(
        "1.0.5",
        f"{_ACID}/CryptexFixup/releases/download/1.0.5/CryptexFixup-1.0.5-RELEASE.zip",
        "25041d94a0fe9a0261caf0ba89b36dfcb21682bf3c697a34bcaddc839576ab30",
    ),
    "RestrictEvents": Component(
        "1.1.6",
        f"{_ACID}/RestrictEvents/releases/download/1.1.6/RestrictEvents-1.1.6-RELEASE.zip",
        "98170dfae195ddd28b5d95e3f040125a13ca783bcb9bd1e5b8c588e217b14ee6",
    ),
    "OcBinaryData": Component(
        _OCBD_COMMIT[:12],
        f"{_ACID}/OcBinaryData/archive/{_OCBD_COMMIT}.tar.gz",
        "397c676372794bf0b63ac6fa5e3b1924caafad7da28f82d3ecad996eea5842ea",
    ),
}

# Kexts extracted straight from their release zip root into EFI/OC/Kexts.
_ROOT_KEXTS = ("Lilu", "WhateverGreen", "AppleALC", "CryptexFixup", "RestrictEvents")

# OpenCorePkg release members copied into the image, relative to X64/.
_OC_PKG_FILES = {
    "X64/EFI/BOOT/BOOTx64.efi": "EFI/BOOT/BOOTx64.efi",
    "X64/EFI/OC/OpenCore.efi": "EFI/OC/OpenCore.efi",
    "X64/EFI/OC/Drivers/OpenRuntime.efi": "EFI/OC/Drivers/OpenRuntime.efi",
    "X64/EFI/OC/Drivers/OpenHfsPlus.efi": "EFI/OC/Drivers/OpenHfsPlus.efi",
    "X64/EFI/OC/Drivers/OpenCanopy.efi": "EFI/OC/Drivers/OpenCanopy.efi",
    "X64/EFI/OC/Drivers/OpenPartitionDxe.efi": "EFI/OC/Drivers/OpenPartitionDxe.efi",
    "X64/EFI/OC/Drivers/ResetNvramEntry.efi": "EFI/OC/Drivers/ResetNvramEntry.efi",
    # config.plist Misc->Tools references Shell.efi, the historical name.
    "X64/EFI/OC/Tools/OpenShell.efi": "EFI/OC/Tools/Shell.efi",
    "X64/EFI/OC/Tools/ResetSystem.efi": "EFI/OC/Tools/ResetSystem.efi",
}

# OcBinaryData subtrees needed by OpenCanopy with PickerVariant
# Acidanthera\Syrah. Audio is skipped: AudioDxe stays disabled.
_OCBD_SUBTREES = ("Resources/Font/", "Resources/Label/", "Resources/Image/Acidanthera/Syrah/")

_ISO_SIZE_MB = 128


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch_component(name: str, comp: Component, dest_dir: Path,
                     on_progress: ProgressCallback = None) -> Path:
    """Download (or reuse) a component archive and verify its pinned hash."""
    dest = dest_dir / comp.filename
    if dest.exists() and _sha256(dest) == comp.sha256:
        log.debug("%s cache hit: %s", name, dest)
        return dest
    dest.unlink(missing_ok=True)
    _download_file(comp.url, dest, on_progress, "opencore")
    actual = _sha256(dest)
    if actual != comp.sha256:
        dest.unlink(missing_ok=True)
        raise ChecksumError(
            f"{name} checksum mismatch: expected {comp.sha256}, got {actual}. "
            "Upstream file changed or the download was tampered with."
        )
    return dest


def _check_member(name: str) -> None:
    if name.startswith("/") or ".." in name.split("/"):
        raise DownloadError(f"Refusing archive member with unsafe path: {name}")


def _extract_zip_subtree(zip_path: Path, prefix: str, dest: Path,
                         strip: int = 0) -> int:
    """Extract members under *prefix* into *dest*, stripping *strip* leading
    path components. Returns the number of files written."""
    count = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if not info.filename.startswith(prefix) or info.is_dir():
                continue
            _check_member(info.filename)
            rel = "/".join(info.filename.split("/")[strip:])
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    if count == 0:
        raise DownloadError(f"No members under '{prefix}' in {zip_path.name}")
    return count


def _extract_zip_files(zip_path: Path, mapping: dict[str, str], dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        for member, rel in mapping.items():
            if member not in names:
                raise DownloadError(f"Missing '{member}' in {zip_path.name}")
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)


def _extract_ocbd_resources(tar_path: Path, dest: Path) -> None:
    """Copy the OpenCanopy resource subtrees out of the OcBinaryData archive.

    The tarball has a single OcBinaryData-<commit>/ root directory."""
    count = 0
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            _check_member(member.name)
            rel = member.name.split("/", 1)[1] if "/" in member.name else member.name
            if not any(rel.startswith(sub) for sub in _OCBD_SUBTREES):
                continue
            out = dest / "EFI/OC" / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            with open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    if count == 0:
        raise DownloadError(f"No OpenCanopy resources found in {tar_path.name}")


def _write_repo_data(dest: Path) -> None:
    """Write config.plist, SSDTs, and MCEReporterDisabler from package data."""
    root = resources.files("osx_proxmox_next") / "data/opencore"
    (dest / "EFI/OC").mkdir(parents=True, exist_ok=True)
    (dest / "EFI/OC/config.plist").write_bytes(
        (root / "config.plist").read_bytes()
    )
    acpi = dest / "EFI/OC/ACPI"
    acpi.mkdir(parents=True, exist_ok=True)
    for aml in ("SSDT-DTGP.aml", "SSDT-EC.aml", "SSDT-EHCI.aml", "SSDT-PLUG.aml"):
        (acpi / aml).write_bytes((root / "ACPI" / aml).read_bytes())
    mce = dest / "EFI/OC/Kexts/MCEReporterDisabler.kext/Contents"
    mce.mkdir(parents=True, exist_ok=True)
    (mce / "Info.plist").write_bytes(
        (root / "MCEReporterDisabler.kext/Contents/Info.plist").read_bytes()
    )


def _scaled_progress(on_progress: ProgressCallback, index: int,
                     count: int) -> ProgressCallback:
    """Map one component's 0-100% into its slice of the overall phase.

    Without this the UI progress bar would reset to 0% for each of the
    downloaded components; with it the bar climbs monotonically across all
    of them (equal weight per component)."""
    if on_progress is None:
        return None

    def _cb(p: DownloadProgress) -> None:
        if p.total > 0:
            done = int((index + p.downloaded / p.total) * 1000)
            on_progress(DownloadProgress(downloaded=done, total=count * 1000,
                                         phase=p.phase))

    return _cb


def assemble_efi_tree(cache_dir: Path, work_dir: Path,
                      on_progress: ProgressCallback = None) -> Path:
    """Download all pinned components and lay out the EFI tree in *work_dir*."""
    items = list(OC_COMPONENTS.items())
    archives = {
        name: _fetch_component(name, comp, cache_dir,
                               _scaled_progress(on_progress, idx, len(items)))
        for idx, (name, comp) in enumerate(items)
    }

    tree = work_dir / "tree"
    _extract_zip_files(archives["OpenCorePkg"], _OC_PKG_FILES, tree)
    kexts = tree / "EFI/OC/Kexts"
    for name in _ROOT_KEXTS:
        _extract_zip_subtree(archives[name], f"{name}.kext/", kexts)
    # VirtualSMC nests its kexts under Kexts/; only VirtualSMC.kext is needed.
    _extract_zip_subtree(archives["VirtualSMC"], "Kexts/VirtualSMC.kext/", kexts, strip=1)
    _extract_ocbd_resources(archives["OcBinaryData"], tree)
    _write_repo_data(tree)
    return tree


def _iso_build_script(tree: Path, iso_tmp: Path) -> str:
    """Bash that packs *tree* into a GPT+ESP FAT32 image at *iso_tmp*.

    Mirrors the layout of the historical prebuilt ISO so every downstream
    consumer (loop-mount, blkid vfat detection) behaves identically."""
    from shlex import quote as shquote

    qt = shquote(str(tree))
    qi = shquote(str(iso_tmp))
    return (
        # Detach loops left behind by a previous run that was killed hard
        # (SIGKILL skips the EXIT trap, so the device would leak forever).
        f"for lo in $(losetup -j {qi} -O NAME --noheadings 2>/dev/null); do "
        "umount -l $lo* 2>/dev/null; losetup -d $lo 2>/dev/null; done; "
        "ILOOP=''; IMNT=$(mktemp -d) && "
        "trap 'umount $IMNT 2>/dev/null; [ -n \"$ILOOP\" ] && losetup -d $ILOOP 2>/dev/null; "
        "rmdir $IMNT 2>/dev/null' EXIT; "
        f"rm -f {qi} && truncate -s {_ISO_SIZE_MB}M {qi} && "
        f"sgdisk -Z {qi} >/dev/null && "
        f"sgdisk -n 1:0:0 -t 1:EF00 -c 1:OPENCORE {qi} >/dev/null && "
        f"ILOOP=$(losetup -fP --show {qi}) && "
        "partprobe $ILOOP 2>/dev/null; "
        "for _i in $(seq 1 10); do [ -b \"${ILOOP}p1\" ] && break; sleep 1; "
        "partprobe $ILOOP 2>/dev/null; done; "
        "[ -b \"${ILOOP}p1\" ] && "
        "mkfs.fat -F 32 -n OPENCORE ${ILOOP}p1 >/dev/null && "
        "mount ${ILOOP}p1 $IMNT && "
        f"cp -a {qt}/. $IMNT/ && "
        "umount $IMNT && losetup -d $ILOOP && ILOOP=''"
    )


def build_opencore_iso(dest_dir: Path, on_progress: ProgressCallback = None) -> Path:
    """Assemble the OpenCore ISO from pinned components into *dest_dir*."""
    from .services import get_proxmox_adapter

    dest_dir.mkdir(parents=True, exist_ok=True)
    iso = dest_dir / _OPENCORE_UNIVERSAL
    # mkdtemp: 0700 with an unpredictable name, so a hostile sibling in a
    # user-chosen iso_dir cannot pre-create or race the assembly tree. Kept
    # inside dest_dir so the final rename stays on one filesystem.
    work_dir = Path(tempfile.mkdtemp(prefix=".oc-build-", dir=dest_dir))
    iso_tmp = dest_dir / (iso.name + ".part")
    try:
        tree = assemble_efi_tree(dest_dir, work_dir, on_progress)
        result = get_proxmox_adapter().run(["bash", "-c", _iso_build_script(tree, iso_tmp)])
        if not result.ok or not iso_tmp.exists():
            iso_tmp.unlink(missing_ok=True)
            raise DownloadError(f"OpenCore image build failed: {result.output}")
        iso_tmp.rename(iso)
        log.info("Assembled OpenCore ISO at %s", iso)
        return iso
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def ensure_opencore_iso(
    macos: str,
    dest_dir: Path,
    on_progress: ProgressCallback = None,
    force: bool = False,
) -> Path:
    """Return a ready OpenCore ISO, assembling it locally when missing.

    Falls back to the prebuilt release asset when assembly fails for an
    operational reason (offline upstream, missing host tools, loop/mount
    trouble), so an install never dies for a reason the old download path
    would have survived. A checksum mismatch is NOT such a reason: it
    propagates as ChecksumError and aborts the install."""
    iso = dest_dir / _OPENCORE_UNIVERSAL
    if iso.exists() and not force:
        log.debug("OpenCore cache hit: %s", iso)
        return iso
    try:
        return build_opencore_iso(dest_dir, on_progress)
    except ChecksumError:
        # Tamper signal: never downgrade to the unpinned prebuilt image.
        raise
    except (DownloadError, OSError) as exc:
        log.warning("Local OpenCore assembly failed (%s); falling back to prebuilt ISO", exc)
        return download_opencore(macos, dest_dir, on_progress, force=force)
