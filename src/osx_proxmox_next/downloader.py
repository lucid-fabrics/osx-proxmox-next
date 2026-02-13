from __future__ import annotations

import gzip
import plistlib
import random
import re
import shutil
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
}

_OSRECOVERY_URL = "http://osrecovery.apple.com/"
_OSRECOVERY_IMAGE_URL = "http://osrecovery.apple.com/InstallationPayload/RecoveryImage"
_MLB_ZERO = "00000000000000000"

_GITHUB_API = "https://api.github.com/repos/lucid-fabrics/osx-proxmox-next/releases"
_CHUNK_SIZE = 65536
_MAX_RETRIES = 3
_BACKOFF_SECONDS = [1, 2, 4]

_SUCATALOG_URL = (
    "https://swscan.apple.com/content/catalogs/others/"
    "index-15-14-13-12-10.16-10.15-10.14-10.13-10.12-10.11-10.10-10.9-"
    "mountainlion-lion-snowleopard-leopard.merged-1.sucatalog.gz"
)
_MACOS_TITLES: dict[str, str] = {"tahoe": "macOS Tahoe"}
_MIN_INSTALLER_SIZE = 5_000_000_000  # 5 GB — filters small packages


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
        return _download_tahoe_installer(dest_dir, on_progress)

    if macos not in RECOVERY_BOARD_IDS:
        raise DownloadError(f"No recovery board ID for '{macos}'.")

    dest = dest_dir / f"{macos}-recovery.img"
    if dest.exists():
        return dest

    board_id = RECOVERY_BOARD_IDS[macos]
    session = _get_recovery_session()
    image_info = _get_recovery_image_info(session, board_id)
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


def _download_tahoe_installer(
    dest_dir: Path,
    on_progress: ProgressCallback = None,
) -> Path:
    dest = dest_dir / "tahoe-full-installer.img"
    if dest.exists():
        return dest

    pkg_url = _find_installer_url("tahoe")
    pkg_path = dest_dir / "tahoe-InstallAssistant.pkg"
    _download_file(pkg_url, pkg_path, on_progress, "installer")

    dmg_path = _extract_sharedsupport_dmg(pkg_path, dest_dir)

    _build_recovery_image(dmg_path, dmg_path, dest)

    pkg_path.unlink(missing_ok=True)
    dmg_path.unlink(missing_ok=True)

    return dest


def _find_installer_url(macos: str) -> str:
    title = _MACOS_TITLES.get(macos)
    if not title:
        raise DownloadError(f"No installer title mapping for '{macos}'.")

    catalog_bytes = _http_get_bytes(_SUCATALOG_URL)
    catalog_data = gzip.decompress(catalog_bytes)
    catalog = plistlib.loads(catalog_data)

    products = catalog.get("Products", {})
    candidates: list[tuple[str, str]] = []  # (post_date_str, pkg_url)

    for product_id, product in products.items():
        packages = product.get("Packages", [])
        pkg_url = ""
        for pkg in packages:
            url = pkg.get("URL", "")
            size = pkg.get("Size", 0)
            if "InstallAssistant.pkg" in url and size > _MIN_INSTALLER_SIZE:
                pkg_url = url
                break
        if not pkg_url:
            continue

        dist_url = ""
        distributions = product.get("Distributions", {})
        dist_url = distributions.get("English") or distributions.get("en", "")
        if not dist_url:
            continue

        try:
            dist_data = _http_get_bytes(dist_url)
            dist_text = dist_data.decode("utf-8", errors="replace")
        except Exception:
            continue

        match = re.search(r"<title>(.*?)</title>", dist_text, re.IGNORECASE)
        if not match:
            continue

        if title.lower() not in match.group(1).lower():
            continue

        post_date = str(product.get("PostDate", ""))
        candidates.append((post_date, pkg_url))

    if not candidates:
        raise DownloadError(
            f"No installer found for '{macos}' in Apple software catalog."
        )

    candidates.sort(reverse=True)
    return candidates[0][1]


def _http_get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "osx-proxmox-next"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception as exc:
        raise DownloadError(f"Failed to fetch {url}: {exc}") from exc


def _extract_sharedsupport_dmg(pkg_path: Path, dest_dir: Path) -> Path:
    extract_dir = dest_dir / "tahoe-pkg-extract"
    try:
        subprocess.run(
            ["7z", "x", str(pkg_path), f"-o{extract_dir}", "SharedSupport.dmg", "-r"],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        raise DownloadError(
            "7z is required but not installed. "
            "Install it with: apt install p7zip-full"
        )
    except subprocess.CalledProcessError as exc:
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        raise DownloadError(f"Failed to extract installer package: {exc.stderr}") from exc

    found = list(extract_dir.rglob("SharedSupport.dmg"))
    if not found:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise DownloadError("SharedSupport.dmg not found inside installer package.")

    dmg_dest = dest_dir / "tahoe-SharedSupport.dmg"
    shutil.move(str(found[0]), str(dmg_dest))
    shutil.rmtree(extract_dir, ignore_errors=True)

    return dmg_dest


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


def _find_release_asset(release: dict, asset_name: str) -> str:
    for asset in release.get("assets", []):
        if asset.get("name") == asset_name:
            url = asset.get("browser_download_url", "")
            if url:
                return url
    raise DownloadError(
        f"Asset '{asset_name}' not found in release '{release.get('tag_name', '?')}'."
    )


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


def _get_recovery_image_info(session: str, board_id: str) -> dict[str, str]:
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
        "os": "default",
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
