from __future__ import annotations

import gzip
import plistlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from json import loads as json_loads
from pathlib import Path
from typing import Callable, Optional

from . import __version__


@dataclass
class DownloadProgress:
    downloaded: int
    total: int  # 0 if unknown
    phase: str  # "opencore" | "recovery"


ProgressCallback = Optional[Callable[[DownloadProgress], None]]


class DownloadError(Exception):
    pass


RECOVERY_CATALOG: dict[str, dict[str, str]] = {
    "sonoma": {
        "board_id": "Mac-27AD2F918AE68F61",
        "catalog_url": (
            "https://swscan.apple.com/content/catalogs/others/"
            "index-14-13-12-10.16-10.15-10.14-10.13-10.12-10.11-10.10-10.9"
            "-mountainlion-lion-snowleopard-leopard.merged-1.sucatalog.gz"
        ),
    },
    "sequoia": {
        "board_id": "Mac-27AD2F918AE68F61",
        "catalog_url": (
            "https://swscan.apple.com/content/catalogs/others/"
            "index-15-14-13-12-10.16-10.15-10.14-10.13-10.12-10.11-10.10-10.9"
            "-mountainlion-lion-snowleopard-leopard.merged-1.sucatalog.gz"
        ),
    },
}

_GITHUB_API = "https://api.github.com/repos/lucid-fabrics/osx-proxmox-next/releases"
_CHUNK_SIZE = 65536
_MAX_RETRIES = 3
_BACKOFF_SECONDS = [1, 2, 4]


def download_opencore(
    macos: str,
    dest_dir: Path,
    on_progress: ProgressCallback = None,
) -> Path:
    version = __version__
    asset_name = f"opencore-{macos}.iso"
    dest = dest_dir / asset_name

    if dest.exists():
        return dest

    release = _fetch_github_release(version)
    browser_url = _find_release_asset(release, asset_name)

    _download_file(browser_url, dest, on_progress, "opencore")
    return dest


def download_recovery(
    macos: str,
    dest_dir: Path,
    on_progress: ProgressCallback = None,
) -> Path:
    if macos == "tahoe":
        raise DownloadError(
            "Tahoe requires a full installer image. "
            "Auto-download is not available for Tahoe recovery."
        )

    if macos not in RECOVERY_CATALOG:
        raise DownloadError(f"No recovery catalog entry for '{macos}'.")

    dest = dest_dir / f"{macos}-recovery.img"
    if dest.exists():
        return dest

    catalog_info = RECOVERY_CATALOG[macos]
    base_system_url = _find_base_system_url(
        catalog_info["catalog_url"],
        catalog_info["board_id"],
    )

    _download_file(base_system_url, dest, on_progress, "recovery")
    return dest


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


def _find_release_asset(release: dict, asset_name: str) -> str:
    for asset in release.get("assets", []):
        if asset.get("name") == asset_name:
            url = asset.get("browser_download_url", "")
            if url:
                return url
    raise DownloadError(
        f"Asset '{asset_name}' not found in release '{release.get('tag_name', '?')}'."
    )


def _find_base_system_url(catalog_url: str, board_id: str) -> str:
    try:
        req = urllib.request.Request(catalog_url, headers={"User-Agent": "osx-proxmox-next"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except Exception as exc:
        raise DownloadError(f"Failed to fetch Apple catalog: {exc}") from exc

    try:
        xml_data = gzip.decompress(raw)
    except gzip.BadGzipFile:
        xml_data = raw

    try:
        catalog = plistlib.loads(xml_data)
    except Exception as exc:
        raise DownloadError(f"Failed to parse Apple catalog plist: {exc}") from exc

    products = catalog.get("Products", {})
    candidate_url = ""

    for _prod_id, product in products.items():
        packages = product.get("Packages", [])
        base_system_pkg = ""
        for pkg in packages:
            pkg_url = pkg.get("URL", "")
            if "BaseSystem.dmg" in pkg_url:
                base_system_pkg = pkg_url
                break

        if not base_system_pkg:
            continue

        raw_boards = str(product.get("ExtendedMetaInfo", {}).get("InstallAssistantPackageIdentifiers", {}))
        if board_id in raw_boards:
            candidate_url = base_system_pkg
        elif not candidate_url:
            candidate_url = base_system_pkg

    if not candidate_url:
        raise DownloadError(
            f"Could not find BaseSystem.dmg in Apple catalog for board ID '{board_id}'."
        )

    return candidate_url


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
) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "osx-proxmox-next"})
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
