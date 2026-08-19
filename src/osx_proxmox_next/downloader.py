from __future__ import annotations

import hashlib
import logging
import secrets
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from json import loads as json_loads
from pathlib import Path
from collections.abc import Callable
from typing import Optional
from urllib.parse import urlparse

from . import __version__
from .infrastructure import ProxmoxAdapter

log = logging.getLogger(__name__)


@dataclass
class DownloadProgress:
    downloaded: int
    total: int  # 0 if unknown
    phase: str  # "opencore" | "recovery"


ProgressCallback = Optional[Callable[[DownloadProgress], None]]


class DownloadError(Exception):
    pass


# Some storage backends (ZFS, Ceph, certain NFS mounts) require attached raw
# images to be a multiple of the request alignment, typically 512B or 4096B.
# dmg2img output isn't guaranteed to land on that boundary, and QEMU refuses
# to start with "Image size is not a multiple of request alignment" if it
# doesn't. 1MiB covers both alignments.
_RAW_IMAGE_ALIGNMENT = 1024 * 1024


RECOVERY_BOARD_IDS: dict[str, str] = {
    "ventura": "Mac-4B682C642B45593E",
    "sonoma": "Mac-827FAC58A8FDFA22",
    "sequoia": "Mac-27AD2F918AE68F61",
    "tahoe": "Mac-27AD2F918AE68F61",  # Sequoia board ID + os=latest → returns Tahoe
}

# osrecovery returns "latest" OS for Tahoe (macOS 26), "default" for others
_RECOVERY_OS_TYPE: dict[str, str] = {
    "tahoe": "latest",
}

_OSRECOVERY_URL = "http://osrecovery.apple.com/"
_OSRECOVERY_IMAGE_URL = "http://osrecovery.apple.com/InstallationPayload/RecoveryImage"
_MLB_ZERO = "00000000000000000"

_GITHUB_API = "https://api.github.com/repos/lucid-fabrics/osx-proxmox-next/releases"
_CHUNK_SIZE = 65536
_MAX_RETRIES = 3
_BACKOFF_SECONDS = [1, 2, 4]


_OPENCORE_UNIVERSAL = "opencore-osx-proxmox-vm.iso"
_ASSETS_TAG = "assets"

# RestrictEvents silences the cosmetic "Memory Modules Misconfigured"
# notification that macOS shows on every boot with the MacPro7,1 SMBIOS
# (the real Mac Pro has 12 RAM slots; a VM's flat layout trips the check).
# It is shipped and configured (revblock=pci via script_renderer.BOOT_ARGS_BASE)
# but does NOT actually suppress the banner on Sequoia: measured with every
# precondition met and the notifier still running. See the note there. The kext
# is kept because it is correct configuration and costs nothing.
RESTRICTEVENTS_VERSION = "1.1.6"
RESTRICTEVENTS_ZIP = f"RestrictEvents-{RESTRICTEVENTS_VERSION}-RELEASE.zip"
RESTRICTEVENTS_URL = (
    "https://github.com/acidanthera/RestrictEvents/releases/download/"
    f"{RESTRICTEVENTS_VERSION}/{RESTRICTEVENTS_ZIP}"
)


def download_restrictevents(
    dest_dir: Path,
    on_progress: ProgressCallback = None,
    force: bool = False,
) -> Path:
    dest = dest_dir / RESTRICTEVENTS_ZIP
    if dest.exists() and not force:
        log.debug("RestrictEvents cache hit: %s", dest)
        return dest
    _download_file(RESTRICTEVENTS_URL, dest, on_progress, "restrictevents")
    return dest


def ensure_restrictevents(dest_dir: Path) -> Optional[Path]:
    """Best-effort RestrictEvents fetch; the fix is cosmetic so failure is non-fatal."""
    try:
        return download_restrictevents(dest_dir)
    except DownloadError as exc:
        log.warning("RestrictEvents download failed, memory warning fix skipped: %s", exc)
        return None


def download_opencore(
    macos: str,
    dest_dir: Path,
    on_progress: ProgressCallback = None,
    force: bool = False,
) -> Path:
    version = __version__
    # Try version-specific first, fall back to universal OC image
    candidates = [f"opencore-{macos}.iso", _OPENCORE_UNIVERSAL]
    if not force:
        for name in candidates:
            dest = dest_dir / name
            if dest.exists():
                log.debug("OpenCore cache hit: %s", dest)
                return dest

    # Check version-tagged release, latest release, then permanent 'assets' tag
    releases = _fetch_github_releases(version)
    for release in releases:
        for name in candidates:
            url = _find_release_asset(release, name, required=False)
            if url:
                dest = dest_dir / name
                log.debug("Downloading OpenCore %s from %s", name, url)
                _download_file(url, dest, on_progress, "opencore")
                return dest

    tags_tried = [r.get("tag_name", "?") for r in releases]
    raise DownloadError(
        f"No OpenCore asset found in releases {tags_tried}. "
        f"Tried: {', '.join(candidates)}"
    )


def download_recovery(
    macos: str,
    dest_dir: Path,
    on_progress: ProgressCallback = None,
) -> Path:
    if macos not in RECOVERY_BOARD_IDS:
        raise DownloadError(f"No recovery board ID for '{macos}'.")

    dest = dest_dir / f"{macos}-recovery.img"
    if dest.exists():
        log.debug("Recovery cache hit: %s", dest)
        # Images cached by older versions may be unaligned; fix in place so
        # a stale cache doesn't keep reproducing the QEMU alignment error.
        # Best-effort: on read-only storage keep the old return-as-is
        # behavior instead of turning a cache hit into a crash.
        try:
            _align_raw_image(dest)
        except OSError as exc:
            log.warning("Could not align cached recovery image %s: %s", dest, exc)
        return dest

    board_id = RECOVERY_BOARD_IDS[macos]
    os_type = _RECOVERY_OS_TYPE.get(macos, "default")
    log.debug("Fetching %s recovery (board=%s, os_type=%s)", macos, board_id, os_type)
    session = _get_recovery_session()
    image_info = _get_recovery_image_info(session, board_id, os_type)
    image_url = image_info["AU"]
    chunklist_url = image_info["CU"]
    asset_token = image_info["AT"]
    chunklist_token = image_info["CT"]

    dmg_path = dest_dir / f"{macos}-BaseSystem.dmg"
    chunklist_path = dest_dir / f"{macos}-BaseSystem.chunklist"

    _download_file_with_token(image_url, asset_token, dmg_path, on_progress, "recovery")
    _download_file_with_token(chunklist_url, chunklist_token, chunklist_path, None, "recovery")

    _build_recovery_image(dmg_path, chunklist_path, dest)

    dmg_path.unlink(missing_ok=True)
    chunklist_path.unlink(missing_ok=True)

    return dest


_CHUNKLIST_MAGIC = b"CNKL"
_CHUNKLIST_ENTRY = 36  # 4-byte length + 32-byte sha256


def _verify_chunklist(dmg_path: Path, chunklist_path: Path) -> None:
    """Check BaseSystem.dmg against Apple's chunklist, raising on a mismatch.

    Apple ships a CNKL manifest next to the image: a 36-byte header followed by
    one (length, sha256) entry per chunk. Verifying it turns a silently corrupt
    download into an error here, instead of a recovery that converts fine and
    then misbehaves at boot. Chunks are streamed so a ~1 GB image costs one
    sequential read and no extra memory.

    A chunklist we cannot parse is not treated as a failed download: Apple has
    changed this format before, and refusing to install over it would be worse
    than the corruption it guards against.
    """
    try:
        blob = chunklist_path.read_bytes()
    except OSError as exc:
        log.warning("Could not read chunklist %s: %s", chunklist_path, exc)
        return

    if len(blob) < 36 or blob[:4] != _CHUNKLIST_MAGIC:
        log.warning("Chunklist %s is not in the expected CNKL format, skipping verification",
                    chunklist_path)
        return

    # CNKL header: magic(4) size(4) version(1) method(1) pad(2) chunk_count(8)
    header_size, _file_ver, _chunk_method = struct.unpack_from("<IBB", blob, 4)
    chunk_count = struct.unpack_from("<Q", blob, 12)[0]
    end = header_size + chunk_count * _CHUNKLIST_ENTRY
    if header_size < 36 or end > len(blob):
        log.warning("Chunklist %s is truncated, skipping verification", chunklist_path)
        return

    with dmg_path.open("rb") as fh:
        for index in range(chunk_count):
            base = header_size + index * _CHUNKLIST_ENTRY
            length = struct.unpack_from("<I", blob, base)[0]
            expected = blob[base + 4:base + _CHUNKLIST_ENTRY]
            data = fh.read(length)
            if len(data) != length:
                raise DownloadError(
                    f"Recovery download is short: chunk {index + 1}/{chunk_count} wanted "
                    f"{length} bytes, got {len(data)}. Re-run the download."
                )
            if hashlib.sha256(data).digest() != expected:
                raise DownloadError(
                    f"Recovery download failed Apple's checksum at chunk "
                    f"{index + 1}/{chunk_count} (offset {fh.tell() - length}). "
                    "The bytes on disk are not what Apple served. Re-run the download; "
                    "if it fails again the host is corrupting data, so check RAM "
                    "(memtest) and storage (zpool scrub / SMART)."
                )
        if fh.read(1):
            log.warning("Recovery image has trailing data beyond the chunklist, continuing")

    log.debug("Recovery image verified against chunklist: %d chunks", chunk_count)


def _build_recovery_image(dmg_path: Path, chunklist_path: Path, dest: Path) -> None:
    from .services import get_proxmox_adapter

    _verify_chunklist(dmg_path, chunklist_path)
    adapter = get_proxmox_adapter()
    result = adapter.run(["dmg2img", str(dmg_path), str(dest)])
    if not result.ok:
        if dest.exists():
            dest.unlink()
        if result.returncode == 127:
            raise DownloadError(
                "dmg2img is required but not installed. "
                "Install it with: apt install dmg2img"
            )
        raise DownloadError(f"Failed to convert recovery DMG: {result.output}")
    # Unlike the cache-hit path, a freshly built image that can't be aligned
    # WILL fail at VM start, so fail loud through the normal error channel.
    try:
        _align_raw_image(dest)
    except OSError as exc:
        raise DownloadError(f"Failed to align recovery image: {exc}") from exc


def _align_raw_image(path: Path, alignment: int = _RAW_IMAGE_ALIGNMENT) -> None:
    """Pad a raw image up to the next alignment boundary, in place.

    Padding with zero bytes at the end doesn't touch the GPT primary header
    or any partition contents; only the backup GPT header ends up pointing
    at a stale last-LBA, which is harmless for a read-only recovery boot.
    """
    size = path.stat().st_size
    aligned = -(-size // alignment) * alignment
    if aligned != size:
        with path.open("r+b") as f:
            f.truncate(aligned)


def _fetch_github_releases(version: str) -> list[dict]:
    """Return a list of releases to search for assets, in priority order.

    Order: version-tagged → latest → permanent 'assets' tag.
    """
    releases: list[dict] = []
    seen_tags: set[str] = set()

    for url in (
        f"{_GITHUB_API}/tags/v{version}",
        f"{_GITHUB_API}/latest",
        f"{_GITHUB_API}/tags/{_ASSETS_TAG}",
    ):
        try:
            data = _http_get_json(url)
            tag = data.get("tag_name", "")
            if tag and tag not in seen_tags:
                seen_tags.add(tag)
                releases.append(data)
        except (urllib.error.HTTPError, DownloadError) as exc:
            log.debug("Release fetch failed for %s: %s", url, exc)

    if not releases:
        raise DownloadError(
            f"Could not fetch any GitHub release (tried v{version}, latest, {_ASSETS_TAG})."
        )
    return releases


def _find_release_asset(release: dict, asset_name: str, *, required: bool = True) -> str:
    for asset in release.get("assets", []):
        if asset.get("name") == asset_name:
            url = asset.get("browser_download_url", "")
            if url:
                return url
    if required:
        raise DownloadError(
            f"Asset '{asset_name}' not found in release '{release.get('tag_name', '?')}'."
        )
    return ""


def _generate_id(length: int) -> str:
    return secrets.token_hex(length // 2).upper()


def _get_recovery_session() -> str:
    headers = {
        "Host": "osrecovery.apple.com",
        "Connection": "close",
        "User-Agent": "InternetRecovery/1.0",
    }
    req = urllib.request.Request(_OSRECOVERY_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            for key, value in resp.headers.items():
                if key.lower() == "set-cookie":
                    for part in value.split("; "):
                        if part.startswith("session="):
                            return part
    except (OSError, urllib.error.URLError) as exc:
        raise DownloadError(f"Failed to get recovery session: {exc}") from exc
    raise DownloadError("No session cookie in Apple recovery response.")


def _get_recovery_image_info(
    session: str, board_id: str, os_type: str = "default"
) -> dict[str, str]:
    headers = {
        "Host": "osrecovery.apple.com",
        "Connection": "close",
        "User-Agent": "InternetRecovery/1.0",
        "Cookie": session,
        "Content-Type": "text/plain",
    }
    post_data = {
        "cid": _generate_id(16),
        "sn": _MLB_ZERO,
        "bid": board_id,
        "k": _generate_id(64),
        "fg": _generate_id(64),
        "os": os_type,
    }
    body = "\n".join(f"{k}={v}" for k, v in post_data.items()).encode()
    req = urllib.request.Request(_OSRECOVERY_IMAGE_URL, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            output = resp.read().decode("utf-8")
    except (OSError, urllib.error.URLError) as exc:
        raise DownloadError(f"Failed to get recovery image info: {exc}") from exc

    info: dict[str, str] = {}
    for line in output.split("\n"):
        if ": " in line:
            key, value = line.split(": ", 1)
            info[key] = value

    for required_key in ("AU", "AT", "CU", "CT"):
        if required_key not in info:
            raise DownloadError(
                f"Missing key '{required_key}' in Apple recovery response."
            )
    return info


def _retry_download(
    url: str,
    dest: Path,
    on_progress: ProgressCallback,
    phase: str,
    extra_headers: dict[str, str] | None = None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest.parent / (dest.name + ".part")

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            _do_download(url, part_path, on_progress, phase, extra_headers=extra_headers)
            part_path.rename(dest)
            return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            log.debug("Download attempt %d/%d failed for %s: %s", attempt + 1, _MAX_RETRIES, url, exc)
            if part_path.exists():
                part_path.unlink()
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_BACKOFF_SECONDS[attempt])

    raise DownloadError(f"Download failed after {_MAX_RETRIES} attempts: {last_error}")


def _download_file_with_token(
    url: str,
    asset_token: str,
    dest: Path,
    on_progress: ProgressCallback,
    phase: str,
) -> None:
    parsed = urlparse(url)
    headers = {
        "Host": parsed.hostname,
        "Connection": "close",
        "User-Agent": "InternetRecovery/1.0",
        "Cookie": f"AssetToken={asset_token}",
    }
    _retry_download(url, dest, on_progress, phase, extra_headers=headers)


def _download_file(
    url: str,
    dest: Path,
    on_progress: ProgressCallback,
    phase: str,
) -> None:
    _retry_download(url, dest, on_progress, phase)


def _do_download(
    url: str,
    dest: Path,
    on_progress: ProgressCallback,
    phase: str,
    extra_headers: dict[str, str] | None = None,
) -> None:
    headers = extra_headers or {"User-Agent": "osx-proxmox-next"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(_CHUNK_SIZE)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress:
                    on_progress(DownloadProgress(
                        downloaded=downloaded,
                        total=total,
                        phase=phase,
                    ))


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": "osx-proxmox-next",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json_loads(resp.read())
