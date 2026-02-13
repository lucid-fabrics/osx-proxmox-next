from __future__ import annotations

import random
import string
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from json import loads as json_loads
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from . import __version__


@dataclass
class DownloadProgress:
    downloaded: int
    total: int  # 0 if unknown
    phase: str  # "opencore" | "recovery"


ProgressCallback = Optional[Callable[[DownloadProgress], None]]


class DownloadError(Exception):
    pass


RECOVERY_BOARD_IDS: dict[str, str] = {
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


def download_opencore(
    macos: str,
    dest_dir: Path,
    on_progress: ProgressCallback = None,
) -> Path:
    version = __version__
    # Try version-specific first, fall back to universal OC image
    candidates = [f"opencore-{macos}.iso", _OPENCORE_UNIVERSAL]
    for name in candidates:
        dest = dest_dir / name
        if dest.exists():
            return dest

    release = _fetch_github_release(version)
    for name in candidates:
        url = _find_release_asset(release, name, required=False)
        if url:
            dest = dest_dir / name
            _download_file(url, dest, on_progress, "opencore")
            return dest
    raise DownloadError(
        f"No OpenCore asset found in release '{release.get('tag_name', '?')}'. "
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
        return dest

    board_id = RECOVERY_BOARD_IDS[macos]
    os_type = _RECOVERY_OS_TYPE.get(macos, "default")
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


def _build_recovery_image(dmg_path: Path, _chunklist_path: Path, dest: Path) -> None:
    try:
        subprocess.run(
            ["dmg2img", str(dmg_path), str(dest)],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        if dest.exists():
            dest.unlink()
        raise DownloadError(f"Failed to convert recovery DMG: {exc.stderr}") from exc
    except FileNotFoundError:
        raise DownloadError(
            "dmg2img is required but not installed. "
            "Install it with: apt install dmg2img"
        )


def _fetch_github_release(version: str) -> dict:
    tag_url = f"{_GITHUB_API}/tags/v{version}"
    try:
        data = _http_get_json(tag_url)
        return data
    except (urllib.error.HTTPError, DownloadError):
        pass

    latest_url = f"{_GITHUB_API}/latest"
    try:
        data = _http_get_json(latest_url)
        return data
    except (urllib.error.HTTPError, DownloadError) as exc:
        raise DownloadError(
            f"Could not fetch GitHub release (tried v{version} and latest): {exc}"
        ) from exc


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
    return "".join(random.choices(string.hexdigits[:16].upper(), k=length))


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
    except Exception as exc:
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
    except Exception as exc:
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


def _download_file_with_token(
    url: str,
    asset_token: str,
    dest: Path,
    on_progress: ProgressCallback,
    phase: str,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest.parent / (dest.name + ".part")

    last_error: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            parsed = urlparse(url)
            headers = {
                "Host": parsed.hostname,
                "Connection": "close",
                "User-Agent": "InternetRecovery/1.0",
                "Cookie": f"AssetToken={asset_token}",
            }
            _do_download(url, part_path, on_progress, phase, extra_headers=headers)
            part_path.rename(dest)
            return
        except Exception as exc:
            last_error = exc
            if part_path.exists():
                part_path.unlink()
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_BACKOFF_SECONDS[attempt])

    raise DownloadError(f"Download failed after {_MAX_RETRIES} attempts: {last_error}")


def _download_file(
    url: str,
    dest: Path,
    on_progress: ProgressCallback,
    phase: str,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest.parent / (dest.name + ".part")

    last_error: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            _do_download(url, part_path, on_progress, phase)
            part_path.rename(dest)
            return
        except Exception as exc:
            last_error = exc
            if part_path.exists():
                part_path.unlink()
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_BACKOFF_SECONDS[attempt])

    raise DownloadError(f"Download failed after {_MAX_RETRIES} attempts: {last_error}")


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
